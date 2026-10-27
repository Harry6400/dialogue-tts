"""Bounded, local-only batch preview. Identity metadata never leaves this module."""
import xml.etree.ElementTree as ET

from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException

from .normalize import InputError, VOICES, normalize

MAX_FILES = 100
MAX_CALLS = 50
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_BATCH_BYTES = 20 * 1024 * 1024


def _metadata(content):
    try:
        root = fromstring(content, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (DefusedXmlException, ET.ParseError, ValueError):
        raise InputError('XML格式无效，或含禁止的DTD/实体/外部引用。')
    if root.tag != 'analyze' or sum(1 for _ in root.iter()) > 10000:
        raise InputError('XML结构不支持或节点过多。')
    from .xml_pair import call_identity, XMLImportError
    try:
        ref, role = call_identity(root, require_role=False)
    except XMLImportError as exc:
        raise InputError(str(exc))
    return (ref, (root.findtext('./callInfo/startTime') or '').strip(),
            (root.findtext('./callInfo/duration') or '').strip(), role)


def preview_batch(payload):
    if not isinstance(payload, dict):
        raise InputError('批次参数必须是对象。')
    files = payload.get('files')
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise InputError('每批请选择1–100份XML文件。')
    total = 0
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not isinstance(item.get('content'), str):
            raise InputError('文件名称或内容无效。')
        size = len(item['content'].encode('utf-8'))
        if size > MAX_FILE_BYTES:
            raise InputError('每份文件最多2MB。')
        total += size
    if total > MAX_BATCH_BYTES:
        raise InputError('每批文件总计最多20MiB。')
    options = {key: payload[key] for key in ('rate', 'pause_ms', 'xml_time_unit', 'voices') if key in payload}
    configured = options.get('voices', {})
    if not isinstance(configured, dict):
        raise InputError('角色音色映射必须是对象。')
    pairs = payload.get('pairs', [])
    if not isinstance(pairs, list) or pairs:
        raise InputError('手工配对已停用；仅按 callInfo/callReference 自动配对。')
    metadata, errors, identities = {}, {}, {}
    for i, item in enumerate(files):
        if item['name'].lower().endswith('.xml'):
            try:
                metadata[i] = _metadata(item['content'])
                identities.setdefault(metadata[i][0], []).append(i)
            except InputError as exc:
                errors[i] = str(exc)
        else:
            errors[i] = '仅支持XML文件；JSON输入已移除。'
    # IDs remain exact strings. Never use filename, startTime or adjacency.
    specs = list(identities.values()) + [[i] for i in errors]
    groups = []
    for indices in sorted(specs, key=lambda x: x[0]):
        group = {'id': 'g' + '_'.join(map(str, indices)), 'name': f'对话{len(groups) + 1:03d}',
                 'file_indices': indices, 'status': 'error'}
        groups.append(group)
        try:
            if any(i in errors for i in indices):
                raise InputError(next(errors[i] for i in indices if i in errors))
            roles = [metadata[i][3] for i in indices]
            if len(indices) > 2 or len(set(roles)) != len(roles):
                raise InputError('同一 callReference 存在重复声道或超过两份XML；请排除冲突文件。')
            if any(role not in {'AGENT', 'USER'} for role in roles):
                raise InputError('XML声道标记必须为唯一 AGENT 或 USER，不能猜测说话人。')
            if len(indices) != 2:
                group['error_code'] = 'missing_track'
                missing = 'USER' if roles == ['AGENT'] else 'AGENT'
                raise InputError('此通话缺少 ' + missing + ' 声道。')
            candidate = {**options, 'format': 'xml_pair', 'xml_files': [
                {'name': f'声道{j + 1}.xml', 'content': files[i]['content']} for j, i in enumerate(indices)]}
            group['preview'] = normalize(candidate)
            group['fingerprint'] = group['preview']['fingerprint']
            group['status'] = 'ready'
        except (InputError, RecursionError) as exc:
            group['error'] = str(exc) if isinstance(exc, InputError) else '文件嵌套层级过深。'
    # Assign one mapping for the whole batch, independent of per-file line order.
    speakers = sorted({s for g in groups if g['status'] == 'ready' for s in g['preview']['speakers']})
    mapping = {s: VOICES[i % 2]['id'] for i, s in enumerate(speakers)}
    mapping.update({'AGENT': VOICES[0]['id'], 'A': VOICES[0]['id'], 'USER': VOICES[1]['id'], 'B': VOICES[1]['id']})
    mapping.update(configured)
    for group in groups:
        if group['status'] == 'ready':
            for line in group['preview']['script']['lines']:
                line['voice'] = mapping[line['speaker']]
    return {'groups': groups, 'counts': {state: sum(g['status'] == state for g in groups) for state in ('ready', 'error')}}
