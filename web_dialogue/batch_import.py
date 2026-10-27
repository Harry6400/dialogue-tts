"""Bounded, local-only batch preview. Identity metadata never leaves this module."""
import hashlib
import json
import xml.etree.ElementTree as ET

from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException

from .normalize import InputError, VOICES, normalize

MAX_FILES = 40
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_BATCH_BYTES = 6 * 1024 * 1024


def _metadata(content):
    try:
        root = fromstring(content, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (DefusedXmlException, ET.ParseError, ValueError):
        raise InputError('XML格式无效，或含禁止的DTD/实体/外部引用。')
    if root.tag != 'analyze' or sum(1 for _ in root.iter()) > 10000:
        raise InputError('XML结构不支持或节点过多。')
    fields = tuple((root.findtext('./callInfo/' + key) or '').strip()
                   for key in ('callReference', 'startTime', 'duration', 'role'))
    if not fields[3] or len(fields[3]) > 80:
        raise InputError('XML缺少有效role声道标记，不能猜测说话人。')
    return fields


def preview_batch(payload):
    if not isinstance(payload, dict):
        raise InputError('批次参数必须是对象。')
    files = payload.get('files')
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise InputError('每批请选择1–40份JSON或XML文件。')
    total = 0
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not isinstance(item.get('content'), str):
            raise InputError('文件名称或内容无效。')
        size = len(item['content'].encode('utf-8'))
        if size > MAX_FILE_BYTES:
            raise InputError('每份文件最多2MB。')
        total += size
    if total > MAX_BATCH_BYTES:
        raise InputError('每批文件总计最多6MB。')
    options = {key: payload[key] for key in ('array_path', 'speaker_key', 'text_key', 'rate', 'pause_ms', 'xml_time_unit', 'voices') if key in payload}
    configured = options.get('voices', {})
    if not isinstance(configured, dict):
        raise InputError('角色音色映射必须是对象。')
    metadata, errors, hashes, identities = {}, {}, {}, {}
    for i, item in enumerate(files):
        if item['name'].lower().endswith('.xml'):
            digest = hashlib.sha256(item['content'].encode('utf-8')).digest()
            hashes.setdefault(digest, []).append(i)
            try:
                metadata[i] = _metadata(item['content'])
                ref, start, _, _ = metadata[i]
                if ref and start:
                    identities.setdefault((ref, start), []).append(i)
            except InputError as exc:
                errors[i] = str(exc)
        elif not item['name'].lower().endswith('.json'):
            errors[i] = '仅支持JSON或XML文件。'
    for indices in hashes.values():
        if len(indices) > 1:
            for i in indices:
                errors[i] = '发现重复XML文件，请排除重复项后重新导入。'
    for indices in identities.values():
        if len(indices) > 2:
            for i in indices:
                errors[i] = '同一通话标识对应超过两份XML，不能自动选择声道。'
    pairs = payload.get('pairs', [])
    if not isinstance(pairs, list) or len(pairs) > MAX_FILES // 2:
        raise InputError('手工配对列表无效。')
    used, specs = set(), []
    for pair in pairs:
        if (not isinstance(pair, list) or len(pair) != 2 or
                any(type(i) is not int or i < 0 or i >= len(files) for i in pair) or
                pair[0] == pair[1] or any(i in used for i in pair)):
            raise InputError('手工配对必须选择两份不同XML，每份文件只能使用一次。')
        if any(not files[i]['name'].lower().endswith('.xml') for i in pair):
            raise InputError('手工配对仅适用于XML。')
        used.update(pair)
        specs.append(sorted(pair))
    for i, item in enumerate(files):
        if i in used:
            continue
        if i in metadata and i not in errors:
            ref, start, _, _ = metadata[i]
            candidates = identities.get((ref, start), []) if ref and start else []
            if len(candidates) == 2 and not any(j in used or j in errors for j in candidates):
                specs.append(sorted(candidates)); used.update(candidates)
                continue
        specs.append([i]); used.add(i)
    groups = []
    for indices in sorted(specs, key=lambda x: x[0]):
        group = {'id': 'g' + '_'.join(map(str, indices)), 'name': f'对话{len(groups) + 1:03d}',
                 'file_indices': indices, 'status': 'error'}
        groups.append(group)
        try:
            if any(i in errors for i in indices):
                raise InputError(next(errors[i] for i in indices if i in errors))
            if len(indices) == 2:
                a, b = (metadata[i] for i in indices)
                if a[3] == b[3]:
                    raise InputError('两份XML的role相同，请检查是否重复声道。')
                if any(x and y and x != y for x, y in zip(a[:3], b[:3])):
                    raise InputError('两份XML的已知通话元数据不一致，不能配对。')
                candidate = {**options, 'format': 'xml_pair', 'xml_files': [
                    {'name': f'声道{j + 1}.xml', 'content': files[i]['content']} for j, i in enumerate(indices)]}
            else:
                i = indices[0]
                if i in metadata:
                    raise InputError('XML缺少配对声道或完整通话时间标识；请明确配对或排除此项。')
                try:
                    data = json.loads(files[i]['content'].lstrip('\ufeff'))
                except (ValueError, RecursionError):
                    raise InputError('JSON格式无效。')
                candidate = {**options, 'data': data}
            group['preview'] = normalize(candidate)
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
