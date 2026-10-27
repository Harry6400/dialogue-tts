"""Safe paired-channel XML import. Does not export call metadata or muted text."""
from decimal import Decimal, InvalidOperation
from pathlib import PurePath
from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException
import xml.etree.ElementTree as ET


class XMLImportError(ValueError):
    pass


def number_ms(value, unit):
    try:
        n = Decimal(value.strip())
        if not n.is_finite() or n < 0:
            raise ValueError()
        n = n * (1000 if unit == 's' else 1)
        if n != n.to_integral_value() or n > 86400000:
            raise ValueError()
        return int(n)
    except (InvalidOperation, ValueError, AttributeError):
        raise XMLImportError('时间必须是非负数，精确到毫秒，且不超过24小时。')


def interval(start, end, unit):
    a, b = number_ms(start, unit), number_ms(end, unit)
    if b <= a:
        raise XMLImportError('时间区间的结束必须晚于开始。')
    return a, b


def parse_xml_pair(payload):
    files = payload.get('xml_files')
    unit = payload.get('xml_time_unit', 'ms')
    if unit not in {'ms', 's'}:
        raise XMLImportError('XML时间单位只支持毫秒或秒。')
    if not isinstance(files, list) or len(files) != 2:
        raise XMLImportError('请同时选择同一通对话的两份XML。')
    combined, sources, refs, starts, roles, durations = [], [], [], [], [], []
    for file_index, item in enumerate(files):
        if not isinstance(item, dict) or not isinstance(item.get('content'), str):
            raise XMLImportError('XML文件内容无效。')
        content = item['content']
        if len(content.encode('utf-8')) > 2 * 1024 * 1024:
            raise XMLImportError('每份XML最多2MB。')
        try:
            root = fromstring(content, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        except (DefusedXmlException, ET.ParseError, ValueError):
            raise XMLImportError('XML格式无效，或含禁止的DTD/实体/外部引用。')
        if root.tag != 'analyze':
            raise XMLImportError('当前XML需使用 analyze / subject / function 结构。')
        if sum(1 for _ in root.iter()) > 10000:
            raise XMLImportError('XML节点过多，请缩小文件。')
        role = (root.findtext('./callInfo/role') or '').strip()
        if not role or len(role) > 80:
            raise XMLImportError('XML缺少有效callInfo/role声道标记，不能猜测说话人。')
        if role in roles:
            raise XMLImportError('两份XML的role相同，请检查是否重复选择了同一声道。')
        roles.append(role)
        # Compare metadata only in memory. Never include it in output or errors.
        refs.append((root.findtext('./callInfo/callReference') or '').strip())
        starts.append((root.findtext('./callInfo/startTime') or '').strip())
        durations.append((root.findtext('./callInfo/duration') or '').strip())
        muted_ranges = []
        for node in root.findall('./mutedSegments/muted'):
            try:
                muted_ranges.append(interval(node.get('start'), node.get('end'), unit))
            except XMLImportError:
                # Failing closed is preferable to accidentally speaking protected text.
                raise XMLImportError('静音标记的时间无效；为保护隐私，暂不导入此文件。')
        functions = root.findall('./subject/function')
        if not functions:
            raise XMLImportError('XML中没有 subject/function 对话段。')
        if len(functions) > 100:
            raise XMLImportError('一次最多100段，请拆分后处理。')
        source_name = str(item.get('name') or f'声道{file_index + 1}.xml')
        source_name = source_name.replace('\\', '/').split('/')[-1][:180]
        sources.append({'name': source_name, 'role': role, 'segments': len(functions)})
        for segment_index, node in enumerate(functions):
            raw = node.findtext('time')
            if not raw or len(raw.split(',')) != 2:
                raise XMLImportError('对话段缺少有效起止时间，无法按时间交错排序。')
            start_ms, end_ms = interval(*raw.split(','), unit)
            muted = any(start_ms < end and end_ms > begin for begin, end in muted_ranges)
            if muted:
                text = '已静音片段'
            else:
                text_node = node.find('text')
                text = ''.join(text_node.itertext()) if text_node is not None else ''
                if not text.strip():
                    raise XMLImportError('对话段缺少非空text文本。')
            combined.append({'speaker': role, 'text': text, 'start_ms': start_ms, 'end_ms': end_ms,
                             'muted': muted, '_file_index': file_index, '_segment_index': segment_index})
    if all(refs) and refs[0] != refs[1]:
        raise XMLImportError('两份XML的对话标识不一致，请选择同一通对话的两个声道。')
    for values in (starts, durations):
        if all(values) and values[0] != values[1]:
            raise XMLImportError('两份XML的对话元数据不一致，请检查配对。')
    if len(combined) > 100:
        raise XMLImportError('两份文件合计最多100段，请拆分后处理。')
    combined.sort(key=lambda row: (row['start_ms'], row['_file_index'], row['_segment_index']))
    for index, row in enumerate(combined):
        row['id'] = index + 1
        row['overlap'] = any(row['speaker'] != other['speaker'] and row['start_ms'] < other['end_ms'] and row['end_ms'] > other['start_ms'] for other in combined)
        del row['_file_index']; del row['_segment_index']
    muted_count = sum(row['muted'] for row in combined)
    warnings = [f'XML未声明时间单位，当前按{"毫秒" if unit == "ms" else "秒"}解析；请核对预览。', '按起点排序串行配音，不还原原始时长或重叠说话。']
    if not all(refs):
        warnings.append('有文件缺少对话标识，请自行确认这两份属于同一通对话。')
    if muted_count:
        warnings.append(f'已保护 {muted_count} 个静音片段：预览不显示原文，生成时跳过，不发送微软。')
    if not any(not row['muted'] for row in combined):
        raise XMLImportError('所有片段均被静音保护，没有可合成内容。')
    return {'rows': combined, 'sources': sources, 'warnings': warnings, 'muted_count': muted_count}
