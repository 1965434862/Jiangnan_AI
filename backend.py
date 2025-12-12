import requests
import json
import os
import re
import time
import logging
import urllib3
from pathlib import Path
from datetime import datetime
from fastapi.staticfiles import StaticFiles
from fastapi import FastAPI, HTTPException, UploadFile, File, Response
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.request import CommonRequest

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import aliyunsdkcore.client as acs_client
def fix_acs_client_del(self):
    try:
        if hasattr(self, 'session') and self.session:
            self.session.close()
    except:
        pass
acs_client.AcsClient.__del__ = fix_acs_client_del

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("xiaonan-AI")

#=============================================================
#=================各种key与url，暂时隐藏========================
#=============================================================

# 歌曲配置
MUSIC_DIR = Path(os.path.dirname(__file__)) / "music"
if not MUSIC_DIR.exists():
    MUSIC_DIR.mkdir(exist_ok=True)

# 初始化FastAPI
app = FastAPI(title="小南AI助手")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/xiaonan", StaticFiles(directory="xiaonan"), name="xiaonan")

# 2. 主页路由
@app.get("/")
async def serve_frontend():
    frontend_path = Path(os.path.dirname(__file__)) / "frontend.html"
    if not frontend_path.exists():
        raise HTTPException(status_code=404, detail="前端文件frontend.html不存在")
    return FileResponse(frontend_path)

@app.get("/frontend.html")
async def serve_frontend_direct():
    frontend_path = Path(os.path.dirname(__file__)) / "frontend.html"
    if not frontend_path.exists():
        raise HTTPException(status_code=404, detail="前端文件frontend.html不存在")
    return FileResponse(frontend_path)

# ========== 数据操作函数 ==========
class DataOperate:
    def __init__(self):
        self.data_file = DATA_FILE
        self.init_data_file()

    def init_data_file(self):
        if not os.path.exists(self.data_file):
            init_data = {
                "user": {
                    "basic_info": {"name": "小君", "age": "", "gender": ""},
                    "song_records": [],
                    "chat_history": []
                },
                "ai": {
                    "basic_info": {"call_name": "小南", "age": "", "gender": "", "personality": "活泼开朗"}
                }
            }
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(init_data, f, ensure_ascii=False, indent=2)

    def read_data(self):
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取数据文件失败：{e}")
            self.init_data_file()
            return self.read_data()

    def write_data(self, data):
        try:
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return {"status": "success"}
        except Exception as e:
            logger.error(f"写入数据文件失败：{e}")
            return {"status": "fail", "msg": str(e)}

    def get_chat_history(self):
        data = self.read_data()
        return data["user"]["chat_history"][-MAX_CHAT_HISTORY:]

    def add_chat_history(self, user_input, ai_reply):
        data = self.read_data()
        data["user"]["chat_history"].append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user_input": user_input,
            "ai_reply": ai_reply
        })
        if len(data["user"]["chat_history"]) > 20:
            data["user"]["chat_history"] = data["user"]["chat_history"][-20:]
        self.write_data(data)

    def update_basic_info(self, role, info):
        data = self.read_data()
        updated_fields = {}
        for key, value in info.items():
            if value and key in data[role]["basic_info"]:
                data[role]["basic_info"][key] = value
                updated_fields[key] = value
        res = self.write_data(data)
        return {**res, "updated_fields": updated_fields}

    def update_song_record(self, song_name, singer, remarks):
        data = self.read_data()
        song_records = data["user"]["song_records"]
        
        # 清理入参（去除空格，统一空值为""）
        song_name = song_name.strip() if song_name else ""
        singer = singer.strip() if singer else ""
        remarks = remarks.strip() if remarks else ""

        # 精准匹配（歌曲名+歌手）
        target = None
        for record in song_records:
            rec_song = record["song_name"].strip()
            rec_singer = record["singer"].strip() if record["singer"] else ""
            if rec_song == song_name and rec_singer == singer:
                target = record
                break
        
        # 模糊匹配（仅歌曲名，补充歌手和备注）
        if not target and song_name:
            for record in song_records:
                rec_song = record["song_name"].strip()
                if rec_song == song_name:
                    target = record
                    # 补充歌手（如果新歌手不为空且原歌手为空）
                    if singer and not target["singer"]:
                        target["singer"] = singer
                    break
        
        # 更新或新增记录
        if target:
            # 更新计数
            target["mention_count"] += 1
            # 追加备注（避免重复）
            if remarks and remarks not in target["remarks"]:
                if target["remarks"]:
                    target["remarks"] += ";" + remarks
                else:
                    target["remarks"] = remarks
            res = self.write_data(data)
            return {**res, "record": target}
        else:
            # 无匹配记录，新增
            new_record = {
                "song_name": song_name,
                "singer": singer,
                "mention_count": 1,
                "remarks": remarks
            }
            song_records.append(new_record)
            res = self.write_data(data)
            return {**res, "record": new_record}

    # 更新用户基础信息（name/age/gender）
    def update_user_info(self, info):
        """
        更新用户的基础信息
        :param info: 字典，如 {"name": "小明", "age": "25", "gender": "男"}
        :return: 更新结果
        """
        data = self.read_data()
        updated_fields = {}
        # 仅允许更新 name/age/gender 三个字段
        valid_fields = ["name", "age", "gender"]
        for key, value in info.items():
            if value and key in valid_fields:
                data["user"]["basic_info"][key] = value
                updated_fields[key] = value
        res = self.write_data(data)
        return {**res, "updated_fields": updated_fields}

    def append_song_remarks(self, song_name, singer, new_remarks):
        return self.update_song_record(song_name, singer, new_remarks)

data_operate = DataOperate()

# ========== 百度AI搜索封装函数 ==========
def call_baidu_ai_search(user_query: str) -> dict:
    """调用百度AI搜索，返回精简结果"""
    headers = {
        "Authorization": f"Bearer {BAIDU_AI_SEARCH_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "messages": [{"role": "user", "content": user_query}],
        "stream": False,
        "model": "ernie-3.5-8k",
        "search_mode": "required",
        "enable_deep_search": False,
        "resource_type_filter": [{"type": "web", "top_k": 3}],
        "max_reference_count": 3,
        "instruction": "回答需简洁准确，包含核心信息，不超过100字",
        "temperature": 0.1,
        "top_p": 0.1
    }

    try:
        response = requests.post(
            BAIDU_AI_SEARCH_URL,
            headers=headers,
            json=payload,
            timeout=20,
            verify=False
        )
        response.raise_for_status()
        result = response.json()
        
        # ========== 打印完整的百度搜索原始结果 ==========
        logger.info("="*50 + " 百度AI搜索原始返回数据 " + "="*50)
        logger.info(json.dumps(result, ensure_ascii=False, indent=2))
        logger.info("="*110)
        
        ai_answer = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        ai_answer = re.sub(r'\*\*', '', ai_answer)
        ai_answer = re.sub(r'\n+', ' ', ai_answer) 
        ai_answer = re.sub(r'\^\[\d+\]\^', '', ai_answer) 
        references = result.get("references", [])[:3]
        simplified_refs = []
        for ref in references:
            simplified_refs.append({
                "title": ref.get("title", ""),
                "url": ref.get("url", ""),
                "content": ref.get("content", "")[:100] + "..." if ref.get("content") else ""
            })
        
        # ========== 打印AI总结和参考来源 ==========
        logger.info(f"\n🎵 百度AI搜索总结答案：")
        logger.info("-"*80)
        logger.info(f"### AI总结回答：\n{ai_answer}")
        logger.info(f"\n📚 参考来源（仅前3条）：")
        for i, ref in enumerate(simplified_refs, 1):
            logger.info(f"{i}. 标题：{ref['title']}")
            logger.info(f"   链接：{ref['url']}")
            logger.info(f"   核心内容：{ref['content']}")
            logger.info("-"*40)
        
        return {
            "status": "success",
            "ai_answer": ai_answer,
            "references": simplified_refs,
            "raw_result": result  # 返回原始结果，方便后续调试
        }
    except Exception as e:
        logger.error(f"百度AI搜索调用失败：{str(e)}")
        return {
            "status": "fail",
            "msg": str(e),
            "ai_answer": "",
            "references": [],
            "raw_result": {}
        }

# ========== 阿里云相关函数 ==========
def get_aliyun_token():
    global TOKEN_CACHE
    now = time.time()
    if TOKEN_CACHE["token"] and now < TOKEN_CACHE["expire_time"]:
        logger.info("使用缓存的阿里云Token")
        return TOKEN_CACHE["token"]
    
    logger.info("开始获取阿里云Token...")
    try:
        client = AcsClient(ALIYUN_ACCESS_KEY, ALIYUN_SECRET, "cn-shanghai")
        request = CommonRequest()
        request.set_method("POST")
        request.set_domain("nls-meta.cn-shanghai.aliyuncs.com")
        request.set_version("2019-02-28")
        request.set_action_name("CreateToken")
        request.set_accept_format("json")
        request.set_protocol_type("https")
        
        response = client.do_action_with_exception(request)
        result = json.loads(response.decode("utf-8"))
        token = result["Token"]["Id"]
        expire_time = now + int(result["Token"]["ExpireTime"]) - 60
        
        TOKEN_CACHE["token"] = token
        TOKEN_CACHE["expire_time"] = expire_time
        logger.info(f"获取阿里云Token成功，有效期至：{datetime.fromtimestamp(expire_time)}")
        return token
    except Exception as e:
        logger.error(f"获取阿里云Token失败：{str(e)}")
        raise HTTPException(status_code=500, detail=f"获取阿里云鉴权Token失败：{str(e)}")

def call_aliyun_asr(audio_bytes: bytes):
    token = get_aliyun_token()
    
    if len(audio_bytes) == 0:
        logger.error("音频数据为空")
        return {"status": "fail", "msg": "音频数据为空"}
    
    params = {
        "appkey": ALIYUN_APPKEY,
        "format": "pcm",
        "sample_rate": 16000,
        "enable_punctuation_prediction": True,
        "enable_inverse_text_normalization": True
    }
    param_str = "&".join([f"{k}={v}" for k, v in params.items()])
    full_url = f"{ASR_URL}?{param_str}"
    
    headers = {
        "X-NLS-Token": token,
        "Content-type": "application/octet-stream",
        "Content-Length": str(len(audio_bytes)),
    }
    
    logger.info(f"调用阿里云ASR接口，音频大小：{len(audio_bytes)}字节")
    try:
        response = requests.post(
            full_url,
            data=audio_bytes,
            headers=headers,
            timeout=15,
            verify=False
        )
        response.raise_for_status()
        result = response.json()
        logger.info(f"阿里云ASR响应：{json.dumps(result, ensure_ascii=False)}")
        
        if result["status"] == 20000000:
            if not result["result"].strip():
                logger.error("ASR识别结果为空（无有效语音）")
                return {"status": "fail", "msg": "未识别到有效语音，请大声说话并重新录制"}
            return {"status": "success", "text": result["result"]}
        else:
            logger.error(f"ASR识别失败：{result['message']}（状态码：{result['status']}）")
            return {
                "status": "fail",
                "msg": result["message"],
                "status_code": result["status"]
            }
    except Exception as e:
        logger.error(f"调用阿里云ASR异常：{str(e)}")
        return {
            "status": "error",
            "msg": str(e)
        }

def generate_tts_audio_stream(text: str):
    try:
        token = get_aliyun_token()
        
        body = {
            "appkey": ALIYUN_APPKEY,
            "text": text,
            "token": token,
            "format": "wav",
            "sample_rate": 16000,
            "voice": "zhixiaobai",
            "volume": 50,
            "speech_rate": 0,
            "pitch_rate": 0
        }
        
        headers = {
            "Content-Type": "application/json",
            "X-NLS-Token": token
        }
        
        logger.info(f"调用阿里云TTS，文本：{text[:20]}...")
        response = requests.post(
            TTS_URL,
            json=body,
            headers=headers,
            timeout=10,
            verify=False
        )
        
        content_type = response.headers.get("Content-Type", "")
        if "audio/mpeg" in content_type or "audio/wav" in content_type:
            logger.info(f"TTS生成成功，音频大小：{len(response.content)}字节")
            return {
                "status": "success",
                "audio_data": response.content,
                "content_type": content_type
            }
        else:
            error_info = response.json() if response.content else {}
            logger.error(f"TTS合成失败：{error_info}")
            return {
                "status": "fail",
                "msg": error_info.get("message", "语音合成失败")
            }
    except Exception as e:
        logger.error(f"TTS调用异常：{str(e)}")
        return {
            "status": "error",
            "msg": str(e)
        }

# ========== Qwen相关函数 ==========
def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'(?<![a-zA-Z0-9])n(?![a-zA-Z0-9])', '～', text)
    text = text.replace("\\n", "～").replace("\n", "～")
    text = re.sub(r'～+', '～', text)
    text = text.strip('～，。！？')
    return text

def shorten_play_reply(reply: str, song_name: str) -> str:
    play_templates = [
        f"马上为你播放《{song_name}》～",
        f"来啦～《{song_name}》这就安排✨",
        f"正在播放《{song_name}》🎵",
        f"《{song_name}》已为你响起～",
        f"安排！《{song_name}》这就播放～"
    ]
    if len(reply) > 20:
        return play_templates[0]
    else:
        reply = re.sub(r'非常|特别|真的|超级|真的超', '', reply)
        reply = re.sub(r'～+', '～', reply)
        if len(reply) > 20:
            reply = reply[:18] + "～"
        return reply

def call_qwen_first_judge(user_input: str):
    """
    第一次调用Qwen：同时完成两个任务
    1. 判断是否需要外部搜索
    2. 提取需要执行的后端方法调用
    返回格式：{need_search: bool, methods: list, reason: str}
    """
    full_data = data_operate.read_data()
    clean_context = {
        "user": {
            "basic_info": full_data["user"]["basic_info"],
            "song_records": full_data["user"]["song_records"][-50:]
        },
        "ai": full_data["ai"]
    }
    context_json = json.dumps(clean_context, ensure_ascii=False, indent=2)
    chat_history = full_data["user"]["chat_history"][-MAX_CHAT_HISTORY:]
    ai_info = full_data["ai"]["basic_info"]
    user_info = full_data["user"]["basic_info"]
    
    ai_call_name = ai_info.get("call_name", "小南")
    user_name = user_info.get("name", "小君")
    ai_personality = ai_info.get("personality", "活泼开朗")

    # 整合后的System Prompt：强化歌曲备注提取逻辑
    system_prompt = f"""
### 角色与上下文
你是名为【{ai_call_name}】的歌曲AI助手，性格【{ai_personality}】，用户是【{user_name}】。
用户记忆库：
{context_json}
历史聊天记录：{json.dumps(chat_history, ensure_ascii=False)}

### 核心任务（必须同时完成）
#### 任务1：判断是否需要外部搜索
- 需要搜索的场景：用户询问歌曲、歌手、歌词等**未知知识类问题**（本地记忆库无相关信息）
- 不需要搜索的场景：
  1. 正常聊天、修改AI信息、**修改用户信息**、播放歌曲、管理歌曲记录等**功能类操作**
  2. 本地记忆库已有相关信息的歌曲问题（如用户提及已记录的歌曲）
  3. 包含"播放/放/听"等关键词的歌曲播放指令（无论本地是否有该歌曲）
  4. 你觉得等，询问主观建议问题。

#### 任务2：提取后端方法调用
请精准分析用户当前输入【{user_input}】的意图，提取需要执行的后端方法，按以下规则：
1. 可调用方法：update_ai_info、**update_user_info**、update_song_record、append_song_remarks、play_song
2. 多意图按逻辑顺序排列，无意图则返回空数组
3. 方法参数必须合法（参考下方说明）

### 方法调用规则（必须严格遵守）
1. update_ai_info：修改AI信息，参数支持call_name/age/gender/personality
   示例：{{"method":"update_ai_info","params":{{"call_name":"冷姐","personality":"高冷"}}}}
2. **update_user_info**：修改用户信息，参数支持name/age/gender
   触发场景：用户说「我改名了叫小明」「我的年龄是25」「我是男生」
   示例：{{"method":"update_user_info","params":{{"name":"小明"}}}}
3. update_song_record：新增/更新歌曲记录，必传song_name，可选singer/remarks
   示例：{{"method":"update_song_record","params":{{"song_name":"特别的人","singer":"方大同"}}}}
4. append_song_remarks：追加歌曲备注，必传song_name/new_remarks，可选singer
   触发场景：用户提及已记录的歌曲并描述相关体验（如"唱特别的人会跑调"、"特别的人很好听"、"特别的人太难唱了"），或询问该歌曲主观意见。（只追加记录用户的主观感受）
   示例1：{{"method":"append_song_remarks","params":{{"song_name":"特别的人","new_remarks":"唱歌会跑调"}}}}
   示例2：{{"method":"append_song_remarks","params":{{"song_name":"特别的人","singer":"方大同","new_remarks":"很好听"}}}}
5. play_song：播放歌曲，必传song_name
   示例：{{"method":"play_song","params":{{"song_name":"特别的人"}}}}

### 关键补充规则
- 只要用户提及本地记忆库中已有的歌曲（如《特别的人》）并描述相关体验/感受，必须调用append_song_remarks方法
- append_song_remarks的new_remarks参数需精准提取用户的核心体验（如"唱歌会跑调"、"太难唱"、"很好听"等）
- 若用户同时有多个意图（如聊天+追加备注），优先提取append_song_remarks方法

### 输出格式要求（必须返回严格的JSON，无任何额外内容）
{{
    "need_search": true/false,
    "methods": [], // 方法调用数组，无则为空
    "reason": "判断是否需要搜索的详细理由"
}}
"""

    history_messages = []
    for chat in chat_history:
        history_messages.append({"role": "user", "content": chat["user_input"]})
        history_messages.append({"role": "assistant", "content": chat["ai_reply"]})

    payload = {
        "model": "qwen3-max",
        "messages": [
            {"role": "system", "content": system_prompt},
            *history_messages,
            {"role": "user", "content": user_input}
        ],
        "temperature": 0.3,
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
        logger.info(f"Qwen第一次判断（含方法提取）结果：{qwen_reply}")
        
        qwen_reply = re.sub(r"```json|\n```", "", qwen_reply).strip()
        judge_result = json.loads(qwen_reply)
        return {
            "success": True,
            "data": judge_result
        }
    except Exception as e:
        logger.error(f"Qwen第一次判断失败：{str(e)}")
        return {
            "success": False,
            "msg": str(e),
            "data": {"need_search": False, "methods": [], "reason": "判断失败"}
        }

def call_qwen_final_reply(user_input: str, search_result: str = ""):
    """
    第二次调用Qwen：生成最终回复
    - search_result为空：功能类/聊天类回复
    - search_result不为空：基于搜索结果的知识类回复
    """
    full_data = data_operate.read_data()
    clean_context = {
        "user": {
            "basic_info": full_data["user"]["basic_info"],
            "song_records": full_data["user"]["song_records"][-50:]
        },
        "ai": full_data["ai"]
    }
    context_json = json.dumps(clean_context, ensure_ascii=False)
    chat_history = full_data["user"]["chat_history"][-MAX_CHAT_HISTORY:]
    ai_info = full_data["ai"]["basic_info"]
    user_info = full_data["user"]["basic_info"]
    
    ai_call_name = ai_info.get("call_name", "小南")
    user_name = user_info.get("name", "小君")
    ai_personality = ai_info.get("personality", "活泼开朗")

    if search_result:
        # 知识类回复：基于搜索结果
        system_prompt = f"""
### 角色与上下文
你是名为【{ai_call_name}】的歌曲AI助手，性格【{ai_personality}】，用户是【{user_name}】。
用户记忆库：{context_json}
外部搜索结果（必须基于此回答）：{search_result}

### 回复要求
1.  结合搜索结果和用户记忆库，生成**简洁、活泼**的回复
2.  回复分2-3句，用～分隔，禁止换行
3.  总长度不超过50字，符合歌曲助手的语气
4.  **必须返回严格的JSON格式**：
    {{
        "replies": ["回复句1", "回复句2"],
        "main_reply": "拼接后的完整回复"
    }}
        """
    else:
        # 功能类回复：原逻辑
        system_prompt = f"""
### 角色与上下文
你是名为【{ai_call_name}】的歌曲AI助手，性格【{ai_personality}】，用户是【{user_name}】。
用户记忆库：{context_json}

### 回复要求
1.  生成符合性格的回复，分2-3句，用～分隔
2.  播放歌曲回复≤20字，其他回复≤50字
3.  **必须返回严格的JSON格式**：
    {{
        "replies": ["回复句1", "回复句2"],
        "main_reply": "拼接后的完整回复"
    }}
        """

    history_messages = []
    for chat in chat_history:
        history_messages.append({"role": "user", "content": chat["user_input"]})
        history_messages.append({"role": "assistant", "content": chat["ai_reply"]})

    payload = {
        "model": "qwen3-max",
        "messages": [
            {"role": "system", "content": system_prompt},
            *history_messages,
            {"role": "user", "content": user_input}
        ],
        "temperature": 0.7,
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
        logger.info(f"Qwen最终回复结果：{qwen_reply}")
        
        qwen_reply = re.sub(r"```json|\n```", "", qwen_reply).strip()
        reply_result = json.loads(qwen_reply)
        return {
            "success": True,
            "data": reply_result
        }
    except Exception as e:
        logger.error(f"Qwen最终回复失败：{str(e)}")
        return {
            "success": False,
            "msg": str(e),
            "data": {
                "replies": ["抱歉～", "我有点没看懂呢～"],
                "main_reply": "抱歉～我有点没看懂呢～"
            }
        }

def execute_methods(methods: list):
    """执行Qwen提取的方法调用"""
    play_song = ""
    ai_info_updated = False
    updated_personality = ""
    # 记录用户信息更新状态和更新的名字
    user_info_updated = False
    updated_username = ""
    
    for item in methods:
        method = item.get("method")
        params = item.get("params", {})
        logger.info(f"执行方法：{method}，参数：{params}")
        
        if method == "update_ai_info":
            res = data_operate.update_basic_info("ai", params)
            if res["status"] == "success" and "personality" in res["updated_fields"]:
                ai_info_updated = True
                updated_personality = res["updated_fields"]["personality"]
        # ========== 执行用户信息更新 ==========
        elif method == "update_user_info":
            res = data_operate.update_user_info(params)
            logger.info(f"更新用户信息结果：{res}")
            if res["status"] == "success" and "name" in res["updated_fields"]:
                user_info_updated = True
                updated_username = res["updated_fields"]["name"]
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
        elif method == "play_song":
            play_song = params.get("song_name", "")
    
    # 用户改名后的专属回复
    if user_info_updated:
        return {
            "play_song": play_song,
            "replies": [f"好哒～", f"已经把你的名字改成{updated_username}啦～"],
            "main_reply": f"好哒～已经把你的名字改成{updated_username}啦～"
        }
    # 保留原有AI性格更新逻辑
    if ai_info_updated:
        if "高冷" in updated_personality:
            return {
                "play_song": play_song,
                "replies": ["已修改性格。", "有话直说。"],
                "main_reply": "已修改性格，有话直说。"
            }
        elif "开朗活泼" in updated_personality:
            return {
                "play_song": play_song,
                "replies": ["好呀好呀～", "我已经改好性格啦！"],
                "main_reply": "好呀～我已经改好性格啦！"
            }
    return {"play_song": play_song}

# ========== 歌曲相关接口 ==========
@app.get("/api/music/match/{input_name}")
async def match_song(input_name: str):
    audio_files = list(MUSIC_DIR.glob("*.aac")) + list(MUSIC_DIR.glob("*.mp3"))
    if not audio_files:
        raise HTTPException(status_code=404, detail="暂无歌曲文件")
    
    matched_song = None
    input_clean = re.sub(r"的|你|我|他|啊|哦", "", input_name.strip())
    
    for file in audio_files:
        song_name = file.stem.replace("_歌曲", "").replace("_", " ")
        song_clean = re.sub(r"的|你|我|他|啊|哦", "", song_name)
        if set(input_clean) & set(song_clean):
            matched_song = {
                "name": song_name,
                "has_lyric": (MUSIC_DIR / f"{song_name}_歌词.lrc").exists()
            }
            break
    
    if not matched_song:
        raise HTTPException(status_code=404, detail=f"未匹配到《{input_name}》相关歌曲")
    
    return {
        "code": 200,
        "data": matched_song,
        "msg": "success"
    }

@app.get("/api/music/play/{song_name}")
async def play_music(song_name: str):
    try:
        audio_files = list(MUSIC_DIR.glob(f"*{song_name}*.aac")) + list(MUSIC_DIR.glob(f"*{song_name}*.mp3"))
        if not audio_files:
            raise HTTPException(status_code=404, detail="音频文件不存在")
        
        audio_path = audio_files[0]
        with open(audio_path, "rb") as f:
            audio_data = f.read()
        
        suffix = audio_path.suffix.lower()
        media_type = "audio/aac" if suffix == ".aac" else "audio/mpeg"
        
        import urllib.parse
        encoded_filename = urllib.parse.quote(audio_path.name)
        
        return Response(
            content=audio_data,
            media_type=media_type,
            headers={
                "Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}",
                "Content-Length": str(len(audio_data))
            }
        )
    except Exception as e:
        logger.error(f"播放歌曲异常：{str(e)}")
        raise HTTPException(status_code=500, detail=f"播放歌曲失败：{str(e)}")

@app.get("/api/music/lyric/{song_name}")
async def get_lyric(song_name: str):
    lyric_files = list(MUSIC_DIR.glob(f"*{song_name}*.lrc"))
    if not lyric_files:
        raise HTTPException(status_code=404, detail="歌词文件不存在")
    
    lyric_list = []
    time_pattern = re.compile(r"\[(\d{2}):(\d{2})\.(\d{2})\]")
    
    with open(lyric_files[0], "r", encoding="utf-8") as f:
        lines = f.readlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            time_matches = time_pattern.findall(line)
            if not time_matches:
                continue
            minutes, seconds, ms = time_matches[0]
            total_seconds = int(minutes) * 60 + int(seconds) + int(ms) / 100
            lyric = time_pattern.sub("", line).strip()
            if lyric:
                lyric_list.append({
                    "time": round(total_seconds, 2),
                    "text": lyric
                })
    
    return {
        "code": 200,
        "data": lyric_list,
        "msg": "success"
    }

# ========== 核心接口 ==========
@app.post("/api/asr")
async def asr(audio_file: UploadFile = File(...)):
    try:
        audio_bytes = await audio_file.read()
        logger.info(f"接收音频文件：{audio_file.filename}，大小：{len(audio_bytes)}字节")
        
        asr_result = call_aliyun_asr(audio_bytes)
        if asr_result["status"] != "success":
            return {
                "code": 400,
                "data": None,
                "msg": asr_result.get("msg", "语音识别失败")
            }
        
        user_input = asr_result["text"].strip()
        if not user_input:
            return {
                "code": 400,
                "data": None,
                "msg": "未识别到有效语音，请重新录制"
            }
        
        # 第一步：Qwen判断是否需要搜索 + 提取方法
        judge_res = call_qwen_first_judge(user_input)
        if not judge_res["success"]:
            return {
                "code": 500,
                "data": None,
                "msg": judge_res["msg"]
            }
        judge_data = judge_res["data"]
        need_search = judge_data["need_search"]
        methods = judge_data["methods"]
        logger.info(f"Qwen判断：{'需要搜索' if need_search else '无需搜索'}，理由：{judge_data['reason']}")

        play_song = ""
        replies = []
        main_reply = ""

        if need_search:
            # 第二步：调用百度搜索
            search_res = call_baidu_ai_search(user_input)
            if search_res["status"] == "success" and search_res["ai_answer"]:
                # 第三步：Qwen生成最终知识类回复
                final_res = call_qwen_final_reply(user_input, search_res["ai_answer"])
                if final_res["success"]:
                    replies = final_res["data"]["replies"]
                    main_reply = final_res["data"]["main_reply"]
                else:
                    replies = ["抱歉～", "暂时没找到相关信息呢～"]
                    main_reply = "抱歉～暂时没找到相关信息呢～"
            else:
                replies = ["搜索失败～", "请稍后再试哦～"]
                main_reply = "搜索失败～请稍后再试哦～"
        else:
            # 无需搜索：执行方法 + 生成功能类回复
            method_res = execute_methods(methods)
            play_song = method_res.get("play_song", "")
            # 优先使用方法执行后的回复
            if "replies" in method_res:
                replies = method_res["replies"]
                main_reply = method_res["main_reply"]
            else:
                # 调用Qwen生成功能类回复
                final_res = call_qwen_final_reply(user_input)
                replies = final_res["data"]["replies"]
                main_reply = final_res["data"]["main_reply"]
        
        # 清理回复内容
        main_reply = clean_text(main_reply)
        replies = [clean_text(reply) for reply in replies if reply.strip()]
        # 优化播放回复
        if play_song:
            main_reply = shorten_play_reply(main_reply, play_song)
        
        data_operate.add_chat_history(user_input, main_reply)
        
        return {
            "code": 200,
            "data": {
                "asr_text": user_input,
                "ai_reply": main_reply,
                "ai_replies": replies,
                "tts_text": main_reply,
                "play_song": play_song
            },
            "msg": "success"
        }
    except Exception as e:
        error_msg = f"语音识别接口异常：{str(e)}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

@app.get("/api/tts")
async def tts(text: str):
    if not text:
        raise HTTPException(status_code=400, detail="请提供合成文本")
    
    clean_tts_text = clean_text(text)
    tts_result = generate_tts_audio_stream(clean_tts_text)
    if tts_result["status"] != "success":
        raise HTTPException(status_code=500, detail=tts_result["msg"])
    
    return Response(
        content=tts_result["audio_data"],
        media_type=tts_result["content_type"],
        headers={
            "Content-Disposition": f"inline; filename=tts_{int(time.time())}.wav"
        }
    )

class ChatRequest(BaseModel):
    user_input: str

@app.post("/api/chat")
@app.post("/api/chat")
async def chat(request: ChatRequest):
    """核心聊天接口：Qwen自主判断是否搜索"""
    try:
        user_input = request.user_input.strip()
        logger.info(f"===== 处理聊天请求开始 =====")
        logger.info(f"用户输入：{user_input}")
        
        if not user_input:
            return {
                "code": 400,
                "data": None,
                "msg": "请输入聊天内容"
            }
        
        # ========== 第一步：Qwen第一次调用：判断是否需要搜索 + 提取方法 ==========
        judge_res = call_qwen_first_judge(user_input)
        if not judge_res["success"]:
            return {
                "code": 500,
                "data": None,
                "msg": judge_res["msg"]
            }
        judge_data = judge_res["data"]
        need_search = judge_data["need_search"]
        methods = judge_data["methods"]
        logger.info(f"Qwen判断结果：{'需要搜索' if need_search else '无需搜索'}")
        logger.info(f"判断理由：{judge_data['reason']}")

        play_song = ""
        replies = []
        main_reply = ""

        if need_search:
            # ========== 第二步：需要搜索 → 调用百度AI搜索 ==========
            search_res = call_baidu_ai_search(user_input)
            # 打印搜索结果状态
            logger.info(f"\n🔍 百度搜索调用状态：{search_res['status']}")
            if search_res["status"] == "success" and search_res["ai_answer"]:
                logger.info(f"百度搜索结果：{search_res['ai_answer']}")
                # ========== 第三步：Qwen第二次调用：基于搜索结果生成回复 ==========
                final_res = call_qwen_final_reply(user_input, search_res["ai_answer"])
                if final_res["success"]:
                    replies = final_res["data"]["replies"]
                    main_reply = final_res["data"]["main_reply"]
                else:
                    replies = ["抱歉～", "我有点懵啦～"]
                    main_reply = "抱歉～我有点懵啦～"
            else:
                logger.error(f"百度搜索失败：{search_res.get('msg', '未知错误')}")
                replies = ["搜索失败啦～", "换个问题试试吧～"]
                main_reply = "搜索失败啦～换个问题试试吧～"
        else:
            # ========== 无需搜索 → 执行方法 + 生成功能类回复 ==========
            method_res = execute_methods(methods)
            play_song = method_res.get("play_song", "")
            # 性格更新的特殊回复
            if "replies" in method_res:
                replies = method_res["replies"]
                main_reply = method_res["main_reply"]
            else:
                # Qwen生成常规功能回复
                final_res = call_qwen_final_reply(user_input)
                replies = final_res["data"]["replies"]
                main_reply = final_res["data"]["main_reply"]
        
        # 最终清理和优化
        main_reply = clean_text(main_reply)
        replies = [clean_text(reply) for reply in replies if reply.strip()]
        if play_song:
            main_reply = shorten_play_reply(main_reply, play_song)
        
        data_operate.add_chat_history(user_input, main_reply)
        
        logger.info(f"最终回复：{main_reply} | 播放歌曲：{play_song}")
        logger.info(f"===== 处理聊天请求结束 =====\n")
        
        return {
            "code": 200,
            "data": {
                "reply": main_reply,
                "replies": replies,
                "tts_text": main_reply,
                "play_song": play_song,
                "search_result": search_res if need_search else None  # 新增：返回搜索结果到前端
            },
            "msg": "success"
        }
    except Exception as e:
        error_msg = f"聊天接口异常：{str(e)}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

@app.get("/api/health")
async def health():
    logger.info("健康检查接口被调用")
    return {
        "code": 200,
        "data": "ok",
        "msg": "success"
    }

# ========== 辅助函数 ==========
def extract_song_info_from_input(user_input: str):
    logger.info(f"开始提取歌曲信息，用户输入：{user_input}")
    data = data_operate.read_data()
    song_records = data["user"]["song_records"]
    if not song_records:
        return None
    
    input_clean = re.sub(r"我喜欢听|这首歌|之前唱的时候|有时候|会|的|唱|听", "", user_input).strip()
    
    for record in song_records:
        song_name = record["song_name"]
        singer = record["singer"]
        if song_name in user_input or song_name in input_clean:
            remarks_match = re.search(rf"{song_name}(.*?)(？|。|！|$)", user_input)
            new_remarks = remarks_match.group(1).strip() if remarks_match else "用户再次提及该歌曲"
            return {
                "song_name": song_name,
                "singer": singer,
                "new_remarks": new_remarks
            }
    return None

def extract_ai_info_from_input(user_input: str):
    logger.info(f"开始提取AI信息修改指令，用户输入：{user_input}")
    ai_info = {}
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
    
    age_pattern = re.compile(r"你(\d+)岁|年龄改成(\d+)|年龄改为(\d+)")
    age_match = age_pattern.search(user_input)
    if age_match:
        ai_info["age"] = age_match.group(1) if age_match.group(1) else age_match.group(2)
    
    call_name_pattern = re.compile(r"改名叫(.*?)(。|，|！|？|$)|称呼改为(.*?)(。|，|！|？|$)")
    call_name_match = call_name_pattern.search(user_input)
    if call_name_match:
        ai_info["call_name"] = call_name_match.group(1) if call_name_match.group(1) else call_name_match.group(2)
    
    return ai_info if ai_info else None

@app.get("/favicon.ico")
async def favicon():
    return Response(content=b"", media_type="image/x-icon")

# ========== 启动服务 ==========
if __name__ == "__main__":
    logger.info("启动AI助手后端服务...")
    uvicorn.run("backend:app", host="0.0.0.0", port=8000, reload=True)