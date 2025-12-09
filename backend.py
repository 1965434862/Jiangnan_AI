import os
import re
import json
import time
import logging
import urllib3
from pathlib import Path
from datetime import datetime

import requests
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
logger = logging.getLogger("KTV-AI")

##########################################
##########################################
#阿里云配置暂时隐藏
##########################################
##########################################

# 歌曲配置
MUSIC_DIR = Path(os.path.dirname(__file__)) / "music"
if not MUSIC_DIR.exists():
    MUSIC_DIR.mkdir(exist_ok=True)

# 初始化FastAPI
app = FastAPI(title="KTV AI助手")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== 数据操作函数 ==========
class DataOperate:
    def __init__(self):
        self.data_file = DATA_FILE
        self.init_data_file()

    def init_data_file(self):
        """初始化数据文件"""
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
        """读取数据文件"""
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取数据文件失败：{e}")
            self.init_data_file()
            return self.read_data()

    def write_data(self, data):
        """写入数据文件"""
        try:
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return {"status": "success"}
        except Exception as e:
            logger.error(f"写入数据文件失败：{e}")
            return {"status": "fail", "msg": str(e)}

    def get_context_prompt(self):
        """获取上下文提示词"""
        data = self.read_data()
        return json.dumps(data, ensure_ascii=False)

    def get_chat_history(self):
        """获取聊天历史"""
        data = self.read_data()
        return data["user"]["chat_history"][-MAX_CHAT_HISTORY:]

    def add_chat_history(self, user_input, ai_reply):
        """添加聊天记录"""
        data = self.read_data()
        data["user"]["chat_history"].append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user_input": user_input,
            "ai_reply": ai_reply
        })
        # 限制历史记录数量
        if len(data["user"]["chat_history"]) > 20:
            data["user"]["chat_history"] = data["user"]["chat_history"][-20:]
        self.write_data(data)

    def update_basic_info(self, role, info):
        """更新基础信息"""
        data = self.read_data()
        updated_fields = {}
        for key, value in info.items():
            if value and key in data[role]["basic_info"]:
                data[role]["basic_info"][key] = value
                updated_fields[key] = value
        res = self.write_data(data)
        return {**res, "updated_fields": updated_fields}

    def update_song_record(self, song_name, singer, remarks):
        """更新歌曲记录"""
        data = self.read_data()
        song_records = data["user"]["song_records"]
        # 检查是否已存在
        for record in song_records:
            if record["song_name"] == song_name and record["singer"] == singer:
                record["mention_count"] += 1
                if remarks and remarks not in record["remarks"]:
                    record["remarks"] += ";" + remarks
                res = self.write_data(data)
                return {**res, "record": record}
        # 新增记录
        new_record = {
            "song_name": song_name,
            "singer": singer,
            "mention_count": 1,
            "remarks": remarks or ""
        }
        song_records.append(new_record)
        res = self.write_data(data)
        return {**res, "record": new_record}

    def append_song_remarks(self, song_name, singer, new_remarks):
        """追加歌曲备注"""
        data = self.read_data()
        song_records = data["user"]["song_records"]
        for record in song_records:
            if record["song_name"] == song_name and record["singer"] == singer:
                if new_remarks and new_remarks not in record["remarks"]:
                    record["remarks"] += ";" + new_remarks
                res = self.write_data(data)
                return {**res, "record": record}
        # 不存在则新增
        return self.update_song_record(song_name, singer, new_remarks)

# 初始化数据操作实例
data_operate = DataOperate()

# ========== 阿里云相关函数 ==========
def get_aliyun_token():
    """获取阿里云Token"""
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
    """调用阿里云ASR"""
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
    """生成TTS音频流"""
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
    """清理文本中的异常字符"""
    if not text:
        return ""
    text = re.sub(r'(?<![a-zA-Z0-9])n(?![a-zA-Z0-9])', '～', text)
    text = text.replace("\\n", "～").replace("\n", "～")
    text = re.sub(r'～+', '～', text)
    text = text.strip('～，。！？')
    return text

def shorten_play_reply(reply: str, song_name: str) -> str:
    """缩短播放相关的回复长度（控制在20字以内）"""
    # 预设简洁的播放回复模板
    play_templates = [
        f"马上为你播放《{song_name}》～",
        f"来啦～《{song_name}》这就安排✨",
        f"正在播放《{song_name}》🎵",
        f"《{song_name}》已为你响起～",
        f"安排！《{song_name}》这就播放～"
    ]
    
    # 如果原回复过长，使用模板
    if len(reply) > 20:
        return play_templates[0]
    # 否则精简原回复
    else:
        # 移除多余的修饰词，保留核心
        reply = re.sub(r'非常|特别|真的|超级|真的超', '', reply)
        reply = re.sub(r'～+', '～', reply)
        # 确保长度不超过20字
        if len(reply) > 20:
            reply = reply[:18] + "～"
        return reply

def call_qwen_api(user_input: str):
    """调用Qwen API"""
    context = data_operate.get_context_prompt()
    chat_history = data_operate.get_chat_history()
    
    data = data_operate.read_data()
    ai_info = data["ai"]["basic_info"]
    user_info = data["user"]["basic_info"]
    
    ai_call_name = ai_info.get("call_name", "小南")
    user_name = user_info.get("name", "小君")
    ai_personality = ai_info.get("personality", "活泼开朗")
    
    system_prompt = r"""### 第一步：角色与上下文说明
你是一个名为【{call_name}】的AI助手，性格为【{ai_personality}】，用户名为【{user_name}】，双方的核心交互场景是歌曲相关的聊天与管理。

#### 用户基础信息（JSON结构说明）：
{{
  "basic_info": {{
    "name": "用户名",
    "age": "年龄",
    "gender": "性别"
  }},
  "song_records": [  // 用户提及过的歌曲记录
    {{
      "song_name": "歌曲名",
      "singer": "歌手名",
      "mention_count": "提及次数（数字）",
      "remarks": "备注（用户对歌曲的描述/喜好，多个备注用分号分隔）"
    }}
  ],
  "chat_history": [  // 历史聊天记录
    {{
      "time": "聊天时间",
      "user_input": "用户输入内容",
      "ai_reply": "你的回复内容"
    }}
  ]
}}

#### AI基础信息（JSON结构说明）：
{{
  "basic_info": {{
    "call_name": "你的称呼",
    "age": "年龄",
    "gender": "性别",
    "personality": "性格描述"
  }}
}}

### 第二步：用户输入意图判断
请精准分析用户当前输入【{user_input}】的核心意图，分为以下四类：
1. 歌曲相关意图：提及歌曲（新增/追加备注）
2. AI信息修改意图：要求修改你的称呼/年龄/性格
3. 播放歌曲意图：要求播放指定歌曲（如“播放特别的你”“来一首特别的人”）
4. 无修改意图：纯聊天（如打招呼、吐槽、确认信息等）

### 第三步：可调用的后端方法说明
| 方法名                | 作用说明                                  | 必传参数                          |
|-----------------------|-------------------------------------------|-----------------------------------|
| update_ai_info        | 修改AI的基础信息（称呼/年龄/性格）| call_name/age/personality（可选） |
| append_song_remarks   | 为已存在的歌曲追加备注                    | song_name, singer, new_remarks    |
| update_song_record    | 新增用户提及的歌曲记录（无则新增）| song_name, singer, remarks        |
| play_song             | 播放指定歌曲                              | song_name（用户输入的歌曲名）     |

### 第四步：回复生成规则
1. 严格遵循你的性格【{ai_personality}】生成多段回复（1-4句）：
2. 播放歌曲意图：回复必须简洁（每句不超过10字，总长度不超过20字），轻快活泼
3. 有方法调用时：回复需明确体现操作结果，分多句说明
4. 无方法调用时：仅需符合性格，自然分多句回应用户
5. 所有回复禁止使用\n换行符，用～作为分句分隔符

### 第五步：输出格式要求（必须严格返回JSON字符串，无任何额外内容）
{{
  "methods": [  // 需调用的方法数组，无则为空数组[]
    {{
      "method": "方法名（如play_song）",
      "params": {{
        "song_name": "用户输入的歌曲名"
      }}
    }}
  ],
  "replies": [  // 2-4句语义关联的回复，每句独立成段（播放意图时每句≤10字）
    "符合性格的第一段回复",
    "符合性格的第二段回复",
    "符合性格的第三段回复"
  ],
  "main_reply": "所有回复用～拼接后的完整话术（播放意图时总长度≤20字，禁止使用\\n）"
}}

### 补充说明
- methods数组中：参数值必须与用户输入严格一致，不能为空
- replies数组：播放意图时数量2-3句，每句≤10字；其他场景2-4句
- main_reply：播放意图时必须≤20字，其他场景≤50字
- 名字纠错/确认场景（用户叫错你名字但未要求修改）：methods为空数组，仅回复纠正
"""

    system_prompt = system_prompt.replace("{context}", context)
    system_prompt = system_prompt.replace("{call_name}", ai_call_name)
    system_prompt = system_prompt.replace("{user_name}", user_name)
    system_prompt = system_prompt.replace("{ai_personality}", ai_personality)
    system_prompt = system_prompt.replace("{user_input}", user_input)

    history_messages = []
    for chat in chat_history[-MAX_CHAT_HISTORY:]:
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

    logger.info(f"===== 调用Qwen API开始 =====")
    logger.info(f"请求Payload：{json.dumps(payload, ensure_ascii=False, indent=2)}")
    
    try:
        response = requests.post(QWEN_API_URL, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        result = response.json()
        qwen_reply = result["choices"][0]["message"]["content"]
        
        logger.info(f"Qwen API返回：{qwen_reply}")
        logger.info(f"===== 调用Qwen API结束 =====")
        
        return qwen_reply
    except Exception as e:
        error_msg = f"调用Qwen失败：{str(e)}"
        logger.error(error_msg)
        return error_msg

def parse_qwen_reply(qwen_raw: str):
    """解析Qwen回复（优化播放指令的回复长度）"""
    logger.info(f"开始解析Qwen回复，原始内容：{qwen_raw}")
    
    if qwen_raw.startswith("调用Qwen失败"):
        logger.info("Qwen调用失败，返回兜底回复")
        return {
            "main_reply": "抱歉，我暂时无法响应你的请求～",
            "replies": ["抱歉～", "我暂时无法响应你的请求呢～", "可以稍后再试试哦！"],
            "play_song": ""
        }
    
    default_replies = ["没理解你的意思呢～", "可以再说清楚一点吗？😜"]
    final_result = {
        "main_reply": "没理解你的意思呢～",
        "replies": default_replies,
        "play_song": ""
    }
    
    try:
        qwen_raw = re.sub(r"[^\{\}\:\,\[\]\"\'\w\s\/\.\-\，；：\\]", "", qwen_raw)
        qwen_raw = re.sub(r"```json|\n```", "", qwen_raw).strip()
        logger.info(f"清理后的Qwen回复：{qwen_raw}")
        
        reply_json = json.loads(qwen_raw)
        logger.info(f"解析后的JSON：{json.dumps(reply_json, ensure_ascii=False, indent=2)}")

        methods = reply_json.get("methods", [])
        ai_info_updated = False
        updated_personality = ""
        play_song = ""
        
        # 执行方法调用
        for item in methods:
            method = item.get("method")
            params = item.get("params", {})
            logger.info(f"执行方法：{method}，参数：{params}")
            
            if method == "update_ai_info":
                res = data_operate.update_basic_info("ai", params)
                logger.info(f"update_ai_info执行结果：{res}")
                if res["status"] == "success" and "personality" in res["updated_fields"]:
                    ai_info_updated = True
                    updated_personality = res["updated_fields"]["personality"]
            elif method == "append_song_remarks":
                res = data_operate.append_song_remarks(
                    params.get("song_name"),
                    params.get("singer"),
                    params.get("new_remarks", "")
                )
                logger.info(f"append_song_remarks执行结果：{res}")
            elif method == "update_song_record":
                res = data_operate.update_song_record(
                    params.get("song_name"),
                    params.get("singer"),
                    params.get("remarks", "")
                )
                logger.info(f"update_song_record执行结果：{res}")
            elif method == "play_song":
                play_song = params.get("song_name", "")
                final_result["play_song"] = play_song

        # 处理多段回复并清理异常字符
        replies = reply_json.get("replies", default_replies)
        replies = [clean_text(reply) for reply in replies if reply.strip()]
        
        # 优化播放指令的回复长度
        if play_song:
            logger.info(f"检测到播放指令，优化回复长度：{play_song}")
            # 播放指令时：限制回复数量2-3句，每句≤10字
            if len(replies) < 2:
                replies = replies + [f"《{play_song}》这就播放～"][:2-len(replies)]
            elif len(replies) > 3:
                replies = replies[:3]
            # 每句限制10字以内
            replies = [reply[:8] + "～" if len(reply) > 10 else reply for reply in replies]
            # 主回复限制20字以内
            main_reply = reply_json.get("main_reply", "～".join(replies))
            main_reply = shorten_play_reply(clean_text(main_reply), play_song)
        else:
            # 非播放指令：保持原有逻辑
            if len(replies) < 2:
                replies = replies + default_replies[:2-len(replies)]
            elif len(replies) > 4:
                replies = replies[:4]
            main_reply = reply_json.get("main_reply", "～".join(replies))
            main_reply = clean_text(main_reply)
        
        # 性格更新后的回复调整
        if ai_info_updated:
            if "高冷" in updated_personality:
                replies = ["已修改性格。", "有话直说。", "无需多言。"]
                main_reply = "已修改性格，有话直说。"
            elif "开朗活泼" in updated_personality:
                replies = ["好呀好呀～", "我已经改好性格啦！", "超喜欢聊天～"]
                main_reply = "好呀～我已经改好性格啦！超喜欢聊天～"
        
        final_result = {
            "main_reply": main_reply,
            "replies": replies,
            "play_song": play_song
        }
        logger.info(f"解析完成，最终回复：{final_result}")

    except Exception as e:
        error_msg = f"解析Qwen回复失败：{str(e)}"
        logger.error(error_msg)
        final_result = {
            "main_reply": "哎呀，我有点没看懂呢～",
            "replies": ["哎呀～", "我有点没看懂呢～", "可以换个说法吗？😜"],
            "play_song": ""
        }
    
    return final_result

# ========== 歌曲相关接口 ==========
@app.get("/api/music/match/{input_name}")
async def match_song(input_name: str):
    """模糊匹配歌曲名（优化返回格式）"""
    audio_files = list(MUSIC_DIR.glob("*.aac")) + list(MUSIC_DIR.glob("*.mp3"))
    if not audio_files:
        raise HTTPException(status_code=404, detail="暂无歌曲文件")
    
    matched_song = None
    input_clean = re.sub(r"的|你|我|他|啊|哦", "", input_name.strip())
    
    for file in audio_files:
        song_name = file.stem.replace("_歌曲", "").replace("_", " ")
        song_clean = re.sub(r"的|你|我|他|啊|哦", "", song_name)
        if set(input_clean) & set(song_clean):
            # 移除audio_path返回（前端不再需要），只返回歌曲名
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
    """返回歌曲音频流（修复中文文件名编码问题）"""
    try:
        # 模糊匹配音频文件
        audio_files = list(MUSIC_DIR.glob(f"*{song_name}*.aac")) + list(MUSIC_DIR.glob(f"*{song_name}*.mp3"))
        if not audio_files:
            raise HTTPException(status_code=404, detail="音频文件不存在")
        
        audio_path = audio_files[0]
        if not audio_path.exists():
            raise HTTPException(status_code=404, detail="音频文件不存在")
        
        # 读取文件字节流（避开FileResponse的中文编码问题）
        with open(audio_path, "rb") as f:
            audio_data = f.read()
        
        # 根据文件后缀设置正确的Content-Type
        suffix = audio_path.suffix.lower()
        media_type = "audio/aac" if suffix == ".aac" else "audio/mpeg"
        
        # 使用Response直接返回字节流，手动处理中文文件名的Content-Disposition
        # 对文件名进行URL编码，避免中文乱码
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
    """解析LRC歌词"""
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
    """语音识别接口"""
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
            logger.error("ASR识别结果为空！")
            return {
                "code": 400,
                "data": None,
                "msg": "未识别到有效语音，请重新录制"
            }
        
        logger.info(f"ASR识别结果：{user_input}，开始处理聊天逻辑")
        
        qwen_raw = call_qwen_api(user_input)
        parse_result = parse_qwen_reply(qwen_raw)
        ai_reply = parse_result["main_reply"]
        ai_replies = parse_result["replies"]
        play_song = parse_result["play_song"]
        
        ai_reply = clean_text(ai_reply)
        ai_replies = [clean_text(reply) for reply in ai_replies]
        
        data_operate.add_chat_history(user_input, ai_reply)
        
        return {
            "code": 200,
            "data": {
                "asr_text": user_input,
                "ai_reply": ai_reply,
                "ai_replies": ai_replies,
                "tts_text": ai_reply,
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
    """语音合成接口"""
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
async def chat(request: ChatRequest):
    """聊天接口"""
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
        
        qwen_raw = call_qwen_api(user_input)
        parse_result = parse_qwen_reply(qwen_raw)
        ai_reply = parse_result["main_reply"]
        ai_replies = parse_result["replies"]
        play_song = parse_result["play_song"]
        
        # 兜底逻辑
        ai_info = extract_ai_info_from_input(user_input)
        if ai_info and "update_ai_info" not in qwen_raw:
            logger.info("Qwen未触发update_ai_info，执行兜底逻辑")
            res = data_operate.update_basic_info("ai", ai_info)
            if res["status"] == "success":
                if "开朗活泼" in ai_info.get("personality", ""):
                    ai_replies = ["好呀～", "我已经改好性格啦！", "超喜欢唱歌～"]
                    ai_reply = "好呀～我已经改好性格啦！超喜欢唱歌～"
                elif "高冷" in ai_info.get("personality", ""):
                    ai_replies = ["已修改。", "有话直说。", "无需多言。"]
                    ai_reply = "已修改，有话直说。"
                else:
                    field_name = list(ai_info.keys())[0]
                    field_value = list(ai_info.values())[0]
                    ai_replies = [
                        f"已按要求修改啦～",
                        f"我的{field_name}是{field_value}哦！",
                        f"超符合期待😜"
                    ]
                    ai_reply = f"已按要求修改啦～我的{field_name}是{field_value}哦！"
        
        song_info = extract_song_info_from_input(user_input)
        if song_info and "append_song_remarks" not in qwen_raw:
            logger.info("Qwen未触发append_song_remarks，执行兜底逻辑")
            data_operate.append_song_remarks(
                song_info["song_name"],
                song_info["singer"],
                song_info["new_remarks"]
            )
            ai_replies = [
                f"已追加《{song_info['song_name']}》备注～",
                f"备注：{song_info['new_remarks']}",
                f"都记下来咯📝"
            ]
            ai_reply = f"已追加《{song_info['song_name']}》备注：{song_info['new_remarks']}～"
        
        # 最终清理回复内容
        ai_reply = clean_text(ai_reply)
        ai_replies = [clean_text(reply) for reply in ai_replies]
        
        data_operate.add_chat_history(user_input, ai_reply)
        
        logger.info(f"聊天请求处理完成，最终回复：{ai_reply}，多段回复：{ai_replies}，播放歌曲：{play_song}")
        logger.info(f"===== 处理聊天请求结束 =====\n")
        
        return {
            "code": 200,
            "data": {
                "reply": ai_reply,
                "replies": ai_replies,
                "tts_text": ai_reply,
                "play_song": play_song
            },
            "msg": "success"
        }
    except Exception as e:
        error_msg = f"聊天接口异常：{str(e)}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

@app.get("/api/health")
async def health():
    """健康检查"""
    logger.info("健康检查接口被调用")
    return {
        "code": 200,
        "data": "ok",
        "msg": "success"
    }

# ========== 辅助函数 ==========
def extract_song_info_from_input(user_input: str):
    """提取歌曲信息"""
    logger.info(f"开始提取歌曲信息，用户输入：{user_input}")
    data = data_operate.read_data()
    song_records = data["user"]["song_records"]
    if not song_records:
        logger.info("无歌曲记录，提取结果：None")
        return None
    
    input_clean = re.sub(r"我喜欢听|这首歌|之前唱的时候|有时候|会|的|唱|听", "", user_input).strip()
    
    for record in song_records:
        song_name = record["song_name"]
        singer = record["singer"]
        if song_name in user_input or song_name in input_clean:
            remarks_match = re.search(rf"{song_name}(.*?)(？|。|！|$)", user_input)
            new_remarks = remarks_match.group(1).strip() if remarks_match else "用户再次提及该歌曲"
            result = {
                "song_name": song_name,
                "singer": singer,
                "new_remarks": new_remarks
            }
            logger.info(f"歌曲信息提取成功：{result}")
            return result
    logger.info("未匹配到歌曲，提取结果：None")
    return None

def extract_ai_info_from_input(user_input: str):
    """提取AI信息修改指令"""
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
    
    logger.info(f"AI信息提取结果：{ai_info if ai_info else 'None'}")
    return ai_info if ai_info else None

# ========== 启动服务 ==========
if __name__ == "__main__":
    logger.info("启动KTV AI助手后端服务...")
    uvicorn.run("backend:app", host="0.0.0.0", port=8000, reload=True)