"""Validate a user's JSON without mutating it; no network or upstream imports."""
import math

VOICES = [
    {"id": "zh-CN-XiaoxiaoNeural", "label": "晓晓 · 普通话女声 · 温和", "locale": "zh-CN", "gender": "Female"},
    {"id": "zh-CN-XiaoyiNeural", "label": "晓伊 · 普通话女声 · 明快", "locale": "zh-CN", "gender": "Female"},
    {"id": "zh-CN-YunxiNeural", "label": "云希 · 普通话男声", "locale": "zh-CN", "gender": "Male"},
    {"id": "zh-CN-YunjianNeural", "label": "云健 · 普通话男声", "locale": "zh-CN", "gender": "Male"},
    {"id": "zh-CN-YunxiaNeural", "label": "云夏 · 普通话男声", "locale": "zh-CN", "gender": "Male"},
    {"id": "zh-CN-YunyangNeural", "label": "云扬 · 普通话男声", "locale": "zh-CN", "gender": "Male"},
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


def normalize(payload, catalog=None):
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
        from .honorific import direct_address
        auto = payload.get('auto_voice', True)
        manual = payload.get('manual_user_voice', False)
        if not isinstance(auto, bool) or not isinstance(manual, bool):
            raise InputError('音色匹配设置无效。')
        address = direct_address(imported['rows']) if auto else 'undetermined'
        voices_catalog = catalog if catalog is not None else VOICES
        current = mapping['USER']
        if not isinstance(current, str): raise InputError('USER 音色无效，请重新选择。')
        genders = {v['id']:v.get('gender') for v in voices_catalog}
        for i,v in enumerate(VOICES):
            if v['id'] in genders and not genders[v['id']]: genders[v['id']] = 'Female' if i < 2 else 'Male'
        if current not in genders: raise InputError('已选 USER 音色不在当前目录，请重新选择。')
        reason = '手动选择' if manual else ('自动匹配关闭' if not auto else '称呼未判定')
        if auto and not manual and address in ('female','male'):
            wanted = 'Female' if address == 'female' else 'Male'
            if genders.get(current) != wanted:
                proposed = 'zh-CN-XiaoyiNeural' if address == 'female' else 'zh-CN-YunxiNeural'
                if proposed in genders: mapping['USER'] = proposed
                else: raise InputError('自动匹配所需的默认音色不可用，请手动选择。')
            reason = '称呼：女士' if address == 'female' else '称呼：先生'
        adapted = {"data": safe_rows, "rate": payload.get("rate", 1),
                   "pause_ms": payload.get("pause_ms", 250), "voices": mapping}
        result = normalize(adapted, catalog)
        for line, row in zip(result["script"]["lines"], safe_rows):
            line["id"] = row["id"]
        result["rows"] = imported["rows"]
        result["warnings"] = imported["warnings"] + result["warnings"]
        result["info_notes"] = imported["info_notes"]
        result["sources"] = imported["sources"]
        result["muted_count"] = imported["muted_count"]
        from .fingerprints import dialogue_fingerprint
        result["fingerprint"] = dialogue_fingerprint(imported["_call_reference"], result["script"])
        result["voice_choice"] = {"user": mapping["USER"], "reason": reason, "address": address}
        result["format"] = "xml_pair"
        result["speakers"] = ["AGENT", "USER"]
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
    allowed = {v["id"] for v in VOICES} & {v["id"] for v in (catalog if catalog is not None else VOICES)}
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
