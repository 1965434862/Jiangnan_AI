import json
import os
from datetime import datetime

# JSON文件路径
DATA_FILE = os.path.join(os.path.dirname(__file__), "data.json")

# 初始化数据文件
def init_data_file():
    default_data = {
        "user": {
            "basic_info": {"name": "未知", "age": None, "gender": None},
            "song_records": [],
            "chat_history": []  # 新增：历史对话记录
        },
        "ai": {
            "basic_info": {"call_name": "江南AI", "age": "21", "gender": "女", "personality": "温柔、耐心，擅长聊K歌技巧"}
        }
    }
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(default_data, f, ensure_ascii=False, indent=2)

# 读取数据
def read_data():
    init_data_file()
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

# 写入数据
def write_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# 通用更新基础信息
def update_basic_info(role: str, update_dict: dict):
    """
    通用更新基础信息函数（支持多字段批量更新）
    :param role: "user" 或 "ai"
    :param update_dict: 要更新的字段字典，如 {"call_name": "冷姐", "age": "25", "personality": "高冷、简洁"}
    :return: 更新结果
    """
    data = read_data()
    
    # 定义各角色支持的字段
    valid_fields = {
        "user": ["name", "age", "gender"],
        "ai": ["call_name", "age", "gender", "personality"]
    }
    
    # 过滤无效字段
    valid_updates = {}
    for key, value in update_dict.items():
        if key in valid_fields[role] and value is not None and value.strip() != "":
            valid_updates[key] = value.strip()
    
    if not valid_updates:
        return {
            "status": "fail",
            "msg": f"无有效更新字段（支持的{role}字段：{','.join(valid_fields[role])}）"
        }
    
    # 批量更新有效字段
    for key, value in valid_updates.items():
        data[role]["basic_info"][key] = value
    
    write_data(data)
    return {
        "status": "success",
        "msg": f"{role}信息更新成功：{', '.join([f'{k}={v}' for k, v in valid_updates.items()])}",
        "updated_fields": valid_updates
    }

# 维护歌曲记录（重构核心逻辑：优先匹配歌曲名，补充歌手/备注）
def update_song_record(song_name: str, singer: str, remarks: str = ""):
    data = read_data()
    song_records = data["user"]["song_records"]
    
    # 清理歌曲名和歌手名（忽略大小写和空格）
    clean_song = song_name.strip().lower()
    clean_singer = singer.strip().lower() if singer else ""
    
    # 第一步：按歌曲名匹配（忽略歌手）
    song_matches = [
        s for s in song_records 
        if s["song_name"].strip().lower() == clean_song
    ]
    
    target = None
    if song_matches:
        # 第二步：优先匹配「歌曲名+歌手名」（精准匹配）
        for s in song_matches:
            if s["singer"].strip().lower() == clean_singer:
                target = s
                break
        
        # 第三步：若没有精准匹配（如歌手为空/不一致），取第一个匹配的歌曲名记录
        if not target:
            target = song_matches[0]
        
        # 更新计数
        target["mention_count"] += 1
        
        # 更新歌手名（如果新歌手名非空且和原歌手名不一致）
        update_singer = False
        if clean_singer and target["singer"].strip().lower() != clean_singer:
            old_singer = target["singer"]
            target["singer"] = singer.strip()
            update_singer = True
        
        # 更新备注（如果有新备注且非空）
        update_remarks = False
        if remarks and remarks.strip():
            old_remarks = target["remarks"]
            target["remarks"] = remarks.strip()
            update_remarks = True
        
        # 构造提示信息
        msg_parts = [f"更新《{song_name}》记录，次数+1（当前{target['mention_count']}）"]
        if update_singer:
            msg_parts.append(f"歌手从「{old_singer}」修正为「{singer.strip()}」")
        if update_remarks:
            msg_parts.append(f"备注更新为：{remarks.strip()}")
        msg = "；".join(msg_parts)
    
    else:
        # 无匹配歌曲名，新增记录
        song_records.append({
            "song_name": song_name.strip(),
            "singer": singer.strip() if singer else "",
            "mention_count": 1,
            "remarks": remarks.strip() if remarks else "暂无备注"
        })
        msg = f"新增《{song_name}》记录，歌手：{singer.strip() if singer else '未知'}，备注：{remarks.strip() if remarks else '暂无备注'}"
    
    write_data(data)
    return {"status": "success", "msg": msg}

# 新增：单独更新歌曲备注（追加逻辑）
def append_song_remarks(song_name: str, singer: str, new_remarks: str):
    """追加歌曲备注（而非覆盖），同时累加提及次数"""
    if not song_name or not new_remarks:
        return {"status": "fail", "msg": "歌曲名和备注内容不能为空"}
    
    data = read_data()
    song_records = data["user"]["song_records"]
    
    # 清理匹配条件
    clean_song = song_name.strip().lower()
    clean_singer = singer.strip().lower() if singer else ""
    
    # 第一步：按歌曲名匹配
    song_matches = [
        s for s in song_records 
        if s["song_name"].strip().lower() == clean_song
    ]
    
    if song_matches:
        # 第二步：优先匹配歌手，无则取第一个
        target = None
        for s in song_matches:
            if s["singer"].strip().lower() == clean_singer:
                target = s
                break
        if not target:
            target = song_matches[0]
        
        # 追加备注逻辑
        old_remarks = target["remarks"].strip()
        if old_remarks == "暂无备注" or not old_remarks:
            target["remarks"] = new_remarks.strip()
        else:
            target["remarks"] = f"{old_remarks}；{new_remarks.strip()}"
        
        # 累加提及次数
        target["mention_count"] += 1
        
        # 修正歌手名（如果新歌手名非空且不一致）
        update_singer = False
        if clean_singer and target["singer"].strip().lower() != clean_singer:
            old_singer = target["singer"]
            target["singer"] = singer.strip()
            update_singer = True
        
        write_data(data)
        
        # 构造返回信息
        msg_parts = [
            f"《{song_name}》备注已追加：{new_remarks.strip()}",
            f"提及次数+1（当前{target['mention_count']}）"
        ]
        if update_singer:
            msg_parts.append(f"歌手从「{old_singer}」修正为「{singer.strip()}」")
        msg = "；".join(msg_parts)
        
        return {
            "status": "success",
            "msg": msg,
            "new_remarks": target["remarks"]
        }
    else:
        return {"status": "fail", "msg": f"未找到《{song_name}》的记录，无法追加备注"}

# 精简歌曲记录（取最近/Top N条）
def get_simplified_song_records(top_n: int = 5):
    data = read_data()
    sorted_records = sorted(
        data["user"]["song_records"],
        key=lambda x: x["mention_count"],
        reverse=True
    )[:top_n]
    if not sorted_records:
        return "暂无歌曲记录"
    record_text = "\n".join([
        f"- 《{s['song_name']}》（{s['singer'] or '未知歌手'}）：提及{s['mention_count']}次，备注：{s['remarks']}"
        for s in sorted_records
    ])
    return record_text

# 生成Prompt用的上下文文本
def get_context_prompt():
    data = read_data()
    # 用户基础信息
    user_info = data["user"]["basic_info"]
    user_text = f"""用户基础信息：
- 姓名：{user_info['name']}
- 年龄：{user_info['age'] or '未知'}
- 性别：{user_info['gender'] or '未知'}"""
    
    # AI基础信息
    ai_info = data["ai"]["basic_info"]
    ai_text = f"""AI基础信息：
- 称呼：{ai_info['call_name']}
- 年龄：{ai_info['age']}
- 性别：{ai_info['gender']}
- 性格：{ai_info['personality']}"""
    
    # 精简后的歌曲记录
    song_text = f"""用户歌曲聊记（最近{5}条）：
{get_simplified_song_records(5)}"""
    
    context = f"""【上下文信息】
{user_text}

{ai_text}

{song_text}"""
    return context

# 新增：保存历史对话（限制最多5轮）
def add_chat_history(user_input: str, ai_reply: str):
    data = read_data()
    max_history = 5  # 限制最多5轮历史对话
    # 追加新对话
    new_chat = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user_input": user_input,
        "ai_reply": ai_reply
    }
    data["user"]["chat_history"].append(new_chat)
    # 超过最大条数则删除最早的
    if len(data["user"]["chat_history"]) > max_history:
        data["user"]["chat_history"].pop(0)
    write_data(data)
    return {"status": "success", "msg": "历史对话已保存"}

# 新增：读取历史对话（用于拼接Prompt）
def get_chat_history():
    data = read_data()
    return data["user"]["chat_history"]