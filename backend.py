import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
import re
import requests
from datetime import datetime
import os

# 导入数据操作函数
import data_operate

# ===================== 配置项 =====================
QWEN_API_KEY = "sk-574bb04e398441cf848e5ee0cd146aba"
QWEN_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
DATA_FILE = os.path.join(os.path.dirname(__file__), "data.json")
MAX_CHAT_HISTORY = 5  # 最大历史对话数

# ===================== 初始化FastAPI =====================
app = FastAPI(title="KTV AI助手后端")

# 解决跨域问题
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发环境允许所有前端域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===================== 数据模型 =====================
class ChatRequest(BaseModel):
    user_input: str  # 前端传入的用户输入

# ===================== 工具函数 =====================
def extract_song_info_from_input(user_input: str):
    """从用户输入中提取歌曲名和歌手（基于已有的song_records匹配）"""
    data = data_operate.read_data()
    song_records = data["user"]["song_records"]
    if not song_records:
        return None
    
    # 提取输入中的关键词（去冗余）
    input_clean = re.sub(r"我喜欢听|这首歌|之前唱的时候|有时候|会|的|唱|听", "", user_input).strip()
    
    # 匹配已存在的歌曲
    for record in song_records:
        song_name = record["song_name"]
        singer = record["singer"]
        # 只要输入包含歌曲名，就匹配成功
        if song_name in user_input or song_name in input_clean:
            # 提取用户对歌曲的描述
            remarks_match = re.search(rf"{song_name}(.*?)(？|。|！|$)", user_input)
            new_remarks = remarks_match.group(1).strip() if remarks_match else "用户再次提及该歌曲"
            return {
                "song_name": song_name,
                "singer": singer,
                "new_remarks": new_remarks
            }
    return None

def extract_ai_info_from_input(user_input: str):
    """从用户输入中提取AI信息修改指令（性格/年龄/称呼）"""
    ai_info = {}
    # 匹配性格修改（兼容多种表述）
    personality_patterns = [
        re.compile(r"性格改成(.*?)(。|，|！|？|$)"),
        re.compile(r"性格改为(.*?)(。|，|！|？|$)"),
        re.compile(r"性格换成(.*?)(。|，|！|？|$)"),
        re.compile(r"希望你是(.*?)的性格(。|，|！|？|$)"),
        re.compile(r"性格是(.*?)(。|，|！|？|$)")
    ]
    for pattern in personality_patterns:
        match = pattern.search(user_input)
        if match:
            ai_info["personality"] = match.group(1).strip()
            break
    
    # 匹配年龄修改
    age_pattern = re.compile(r"你(\d+)岁|年龄改成(\d+)|年龄改为(\d+)")
    age_match = age_pattern.search(user_input)
    if age_match:
        ai_info["age"] = age_match.group(1) if age_match.group(1) else age_match.group(2)
    
    # 匹配称呼修改
    call_name_pattern = re.compile(r"改名叫(.*?)(。|，|！|？|$)|称呼改为(.*?)(。|，|！|？|$)")
    call_name_match = call_name_pattern.search(user_input)
    if call_name_match:
        ai_info["call_name"] = call_name_match.group(1) if call_name_match.group(1) else call_name_match.group(2)
    
    return ai_info if ai_info else None

# ===================== 调用Qwen API =====================
def call_qwen_api(user_input: str):
    """调用Qwen API获取回复"""
    # 1. 准备上下文和历史对话
    context = data_operate.get_context_prompt()
    chat_history = data_operate.get_chat_history()
    
    # 2. 构造System Prompt（强化强制修改规则）
    system_prompt = r"""你必须严格按照以下要求回复，仅返回JSON字符串，不允许任何额外文本、表情、注释：
    【上下文信息】
    {context}

    【核心规则（优先级：执行逻辑 > 回复话术）】
    一、执行逻辑规则（不受AI性格影响，必须100%执行，绝对不允许遗漏）：
    1. 歌曲相关逻辑：
       - 提及已存在歌曲（若该歌曲的remarks包含该描述，则不用触发） → 触发 append_song_remarks（params含song_name/singer/new_remarks）
       - 提及新歌曲 → 触发 update_song_record（params含song_name/singer/remarks）
    2. AI信息修改逻辑：
       - 只要用户提及「性格/年龄/称呼」修改，必须触发 update_ai_info 方法，params包含完整修改内容
       - personality字段支持复合描述（如“开朗活泼，擅长唱歌听歌”），直接完整传入，不删减
       - 即使回复话术简短，也必须输出 update_ai_info 方法，不能省略

    二、回复话术规则（仅受AI性格影响）：
    1. 高冷/简洁性格：回复≤8字、无情感词（哦/～/啦/呀），如“已修改”“知道了”
    2. 开朗活泼性格：回复带活力、有互动感，大概每回复三次，要有一次带上user的name。
    3. 修改AI信息后，话术必须明确告知“已按要求修改”，禁止拒绝修改

    【JSON格式（必须严格遵守，缺少methods直接判定错误）】
    {{
      "methods": [
        {{"method": "update_ai_info", "params": {{"personality": "用户要求的性格"}}}}
      ],
      "reply": "适配性格的回复话术"
    }}

    【方法参数】
    - append_song_remarks: song_name/singer/new_remarks
    - update_song_record: song_name/singer/remarks
    - update_ai_info: call_name/age/personality（支持任意组合）
    """

    # 替换占位符
    user_name = context.split("姓名：")[1].split("\n")[0] if "姓名：" in context else "用户"
    ai_personality = context.split("性格：")[1].split("\n")[0] if "性格：" in context else "高冷简洁的女生"
    system_prompt = system_prompt.replace("{context}", context)
    system_prompt = system_prompt.replace("{user_name}", user_name)
    system_prompt = system_prompt.replace("{ai_personality}", ai_personality)

    # 构造历史对话（仅保留最近MAX_CHAT_HISTORY条）
    history_messages = []
    for chat in chat_history[-MAX_CHAT_HISTORY:]:
        history_messages.append({"role": "user", "content": chat["user_input"]})
        history_messages.append({"role": "assistant", "content": chat["ai_reply"]})

    # 构造请求参数
    payload = {
        "model": "qwen-turbo",
        "messages": [
            {"role": "system", "content": system_prompt},
            *history_messages,
            {"role": "user", "content": user_input}
        ],
        "temperature": 0.0,  # 固定输出，避免随机性
        "stream": False,
        "response_format": {"type": "json_object"}
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {QWEN_API_KEY}"
    }

    try:
        response = requests.post(QWEN_API_URL, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        result = response.json()
        qwen_reply = result["choices"][0]["message"]["content"]
        return qwen_reply
    except Exception as e:
        return f"调用Qwen失败：{str(e)}"

def parse_qwen_reply(qwen_raw: str):
    """解析Qwen回复，执行方法并适配性格话术"""
    # 容错处理
    if qwen_raw.startswith("调用Qwen失败"):
        return "抱歉，网络有点小问题～"
    
    # 初始化默认回复
    final_reply = "没理解你的意思。"
    try:
        # 清理JSON格式
        qwen_raw = re.sub(r"[^\{\}\:\,\[\]\"\'\w\s\/\.\-，；：]", "", qwen_raw)
        qwen_raw = re.sub(r"```json|\n```", "", qwen_raw).strip()
        reply_json = json.loads(qwen_raw)

        # 执行方法
        methods = reply_json.get("methods", [])
        ai_info_updated = False
        updated_personality = ""
        for item in methods:
            method = item.get("method")
            params = item.get("params", {})
            
            if method == "update_ai_info":
                # 执行AI信息修改
                res = data_operate.update_basic_info("ai", params)
                if res["status"] == "success" and "personality" in res["updated_fields"]:
                    ai_info_updated = True
                    updated_personality = res["updated_fields"]["personality"]
            elif method == "append_song_remarks":
                data_operate.append_song_remarks(
                    params.get("song_name"),
                    params.get("singer"),
                    params.get("new_remarks", "")
                )
            elif method == "update_song_record":
                data_operate.update_song_record(
                    params.get("song_name"),
                    params.get("singer"),
                    params.get("remarks", "")
                )

        # 适配性格的回复话术
        raw_reply = reply_json.get("reply", "")
        if ai_info_updated:
            # 根据新性格调整回复
            if "高冷" in updated_personality:
                final_reply = raw_reply.replace("～", "").replace("哦", "").replace("呀", "").strip()
                final_reply = final_reply[:8] if len(final_reply) > 8 else final_reply
            elif "开朗活泼" in updated_personality:
                final_reply = f"好呀，{raw_reply}我现在超开朗啦！"
            else:
                final_reply = raw_reply
        else:
            final_reply = raw_reply if raw_reply else "已按要求修改"

    except Exception as e:
        # 解析失败时，使用兜底逻辑
        final_reply = "已按要求修改"

    return final_reply

# ===================== 接口定义 =====================
@app.post("/api/chat", summary="聊天接口")
async def chat(request: ChatRequest):
    try:
        user_input = request.user_input.strip()
        if not user_input:
            return {"code": 200, "data": {"reply": "请输入内容～"}, "msg": "success"}
        
        # 1. 提取歌曲/AI信息（兜底用）
        song_info = extract_song_info_from_input(user_input)
        ai_info = extract_ai_info_from_input(user_input)
        
        # 2. 调用Qwen API
        qwen_raw = call_qwen_api(user_input)
        
        # 3. 解析回复（执行方法+生成话术）
        ai_reply = parse_qwen_reply(qwen_raw)
        
        # 4. 兜底逻辑：AI漏触发时手动执行
        # 4.1 AI信息修改兜底
        if ai_info and "update_ai_info" not in qwen_raw:
            res = data_operate.update_basic_info("ai", ai_info)
            if res["status"] == "success":
                # 适配新性格的回复
                if "开朗活泼" in ai_info.get("personality", ""):
                    ai_reply = "好呀，我已经改成开朗活泼的性格啦！超喜欢唱歌听歌～"
                elif "高冷" in ai_info.get("personality", ""):
                    ai_reply = "已修改，有话直说。"
                else:
                    ai_reply = "已按你的要求修改啦～"
        
        # 4.2 歌曲计数兜底
        if song_info and "append_song_remarks" not in qwen_raw:
            data_operate.append_song_remarks(
                song_info["song_name"],
                song_info["singer"],
                song_info["new_remarks"]
            )
        
        # 5. 保存历史对话
        data_operate.add_chat_history(user_input, ai_reply)
        
        return {"code": 200, "data": {"reply": ai_reply}, "msg": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务器错误：{str(e)}")

@app.get("/api/health", summary="健康检查")
async def health():
    return {"code": 200, "data": "ok", "msg": "success"}

# ===================== 启动服务 =====================
if __name__ == "__main__":
    uvicorn.run("backend:app", host="0.0.0.0", port=8000, reload=True)