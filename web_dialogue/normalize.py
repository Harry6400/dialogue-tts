"""Validate a user's JSON without mutating it; no network or upstream imports."""
import math

VOICES = [
    {"id": "zh-CN-XiaoxiaoNeural", "label": "晓晓 · 普通话女声 · 温和"},
    {"id": "zh-CN-XiaoyiNeural", "label": "晓伊 · 普通话女声 · 明快"},
    {"id": "zh-CN-YunxiNeural", "label": "云希 · 普通话男声"},
    {"id": "zh-CN-YunjianNeural", "label": "云健 · 普通话男声"},
]
EXAMPLE = [
    {"speaker": "A", "text": "你好，我想订一个明天下午两点的会议室。"},
    {"speaker": "B", "text": "可以，请问大约有几位参加，需要投影设备吗？"},
    {"speaker": "A", "text": "一共六位，需要投影，另外准备一些饮用水就好。"},
    {"speaker": "B", "text": "好的，已经记下。会议预计持续多久呢？"},
    {"speaker": "A", "text": "大概一个小时，如果提前结束，我会通知你。"},
    {"speaker": "B", "text": "没问题，明天下午见。"},
]

class InputError(ValueError):
    pass


def resolve(data, path, label):
    if not isinstance(path, str) or len(path) > 200:
        raise InputError(f"{label}必须是长度不超过 200 的字段路径。")
    if not path.strip():
        return data
    value = data
    for key in path.strip().split("."):
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isdecimal() and int(key) < len(value):
            value = value[int(key)]
        else:
            raise InputError(f"找不到{label}：{path}。请检查字段映射（区分大小写）。")
    return value


def normalize(payload):
    if isinstance(payload, dict) and payload.get("format") == "xml_pair":
        from .xml_pair import XMLImportError, parse_xml_pair
        try:
            imported = parse_xml_pair(payload)
        except XMLImportError as exc:
            raise InputError(str(exc)) from exc
        safe_rows = [row for row in imported["rows"] if not row["muted"]]
        configured = payload.get("voices", {})
        if not isinstance(configured, dict):
            raise InputError("角色音色映射必须是对象。")
        mapping = {"AGENT": VOICES[0]["id"], "USER": VOICES[1]["id"], **configured}
        adapted = {"data": safe_rows, "rate": payload.get("rate", 1),
                   "pause_ms": payload.get("pause_ms", 250), "voices": mapping}
        result = normalize(adapted)
        for line, row in zip(result["script"]["lines"], safe_rows):
            line["id"] = row["id"]
        result["rows"] = imported["rows"]
        result["warnings"] = imported["warnings"] + result["warnings"]
        result["sources"] = imported["sources"]
        result["muted_count"] = imported["muted_count"]
        result["format"] = "xml_pair"
        return result
    if not isinstance(payload, dict) or "data" not in payload:
        raise InputError("请提供 JSON 内容。")
    data = payload["data"]
    rows = resolve(data, payload.get("array_path", ""), "对话数组路径")
    if not isinstance(rows, list):
        raise InputError("对话位置不是数组。顶层为数组时路径留空；例如对象中的 data.lines 填 data.lines。")
    if not 1 <= len(rows) <= 100:
        raise InputError("一次需要 1–100 条对话；更长文件请拆分后处理。")
    sk, tk = payload.get("speaker_key", "speaker"), payload.get("text_key", "text")
    if not isinstance(sk, str) or not sk.strip() or not isinstance(tk, str) or not tk.strip():
        raise InputError("说话人字段与文本字段不能为空。")
    rate = payload.get("rate", 1.0)
    pause = payload.get("pause_ms", 250)
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or not 0.5 <= rate <= 2:
        raise InputError("语速须为 0.5–2.0 倍的数字。")
    if isinstance(pause, bool) or not isinstance(pause, int) or not 0 <= pause <= 3000:
        raise InputError("段间停顿须为 0–3000 毫秒的整数。")
    mapping = payload.get("voices", {})
    if not isinstance(mapping, dict):
        raise InputError("角色音色映射必须是对象。")
    allowed = {v["id"] for v in VOICES}
    speakers, clean, total = [], [], 0
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise InputError(f"第 {i} 条不是对象，不能读取说话人与文本。")
        try:
            speaker, text = resolve(row, sk, "说话人字段"), resolve(row, tk, "文本字段")
        except InputError as exc:
            raise InputError(f"第 {i} 条：{exc}") from exc
        if isinstance(speaker, bool) or not isinstance(speaker, (str, int)):
            raise InputError(f"第 {i} 条的说话人必须是字符串或整数。")
        speaker = str(speaker).strip()
        if not speaker or len(speaker) > 80:
            raise InputError(f"第 {i} 条的说话人不能为空且最多 80 个字符。")
        if not isinstance(text, str) or not text.strip():
            raise InputError(f"第 {i} 条的文本必须是非空字符串。")
        if len(text) > 5000:
            raise InputError(f"第 {i} 条超过 5000 字符，请先拆分。")
        total += len(text)
        if total > 20000:
            raise InputError("总文本超过 20000 字符，请分批处理。")
        if speaker not in speakers:
            speakers.append(speaker)
        default_index = {"AGENT": 0, "A": 0, "USER": 1, "B": 1}.get(speaker, speakers.index(speaker) % 2)
        voice = mapping.get(speaker, VOICES[default_index]["id"])
        if not isinstance(voice, str) or voice not in allowed:
            raise InputError(f"角色 {speaker} 的音色不在当前支持列表，请重新选择。")
        clean.append({"id": i, "speaker": speaker, "text": text, "voice": voice,
                      "pause_after_ms": pause, "speech_rate": float(rate)})
    warnings = []
    if len(speakers) > 2:
        warnings.append("超过两个角色时默认循环分配两种女声，可逐个修改；不同角色可能使用同一音色。")
    script = {"lesson_id": "dialogue", "title": "对话配音", "language": "zh-CN", "lines": clean,
              "settings": {"speech_rate": float(rate), "initial_silence_ms": 0, "default_pause_ms": pause}}
    return {"script": script, "rows": [{k: r[k] for k in ("id", "speaker", "text")} for r in clean],
            "speakers": speakers, "warnings": warnings}
