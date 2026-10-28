"""Pure input and fixed-sample normalization shared by service consumers."""
from .normalize import InputError, normalize
from .xml_example import XML_EXAMPLE

def normalize_source(payload, catalog=None):
    if not isinstance(payload, dict) or payload.get("format") != "xml_pair" or "data" in payload:
        raise InputError("仅支持双声道 XML 输入。")
    result = normalize(payload, catalog)
    from .output_names import pair_filename
    try:
        result['filename'] = pair_filename(payload['xml_files'])
    except ValueError as exc:
        raise InputError(str(exc)) from exc
    return result

def audition_script(payload, catalog=None):
    if payload.get("consent") is not True:
        raise InputError("请先允许发送试听文本至微软语音服务。")
    sample = {"format": "xml_pair", "xml_files": XML_EXAMPLE,
              "voices": payload.get("voices", {}), "rate": payload.get("rate", 1),
              "pause_ms": payload.get("pause_ms", 250)}
    script = normalize_source(sample, catalog)["script"]
    script["lines"] = script["lines"][:2]
    return script
