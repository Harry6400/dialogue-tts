import type {IssueCategory, WorkspaceItem} from '../types/contracts';

export const issueCategories: {key: IssueCategory; label: string; guidance: string}[] = [
  {key: 'pairing', label: '配对异常', guidance: '补齐缺少声道或修正重复声道后，新建批次重新扫描。'},
  {key: 'input', label: '输入文件', guidance: '核对 XML 格式、内容和数字文件名；修正后新建批次重新扫描。'},
  {key: 'changed', label: '输入变化', guidance: '来源文件与扫描时不同；确认正确文件后，新建批次重新扫描。'},
  {key: 'conflict', label: '同名冲突', guidance: '来源编号重复需修正后重新扫描；输出重名需核对或更换输出目录。'},
  {key: 'synthesis', label: '合成失败', guidance: '检查网络后可重试合成，需要重新勾选联网授权。完整缓存缺失时会重新生成整通。'},
  {key: 'save', label: '保存异常', guidance: '确认输出目录与可用空间后重试保存；若缓存校验不一致，请先核对或明确放弃。'},
  {key: 'other', label: '其他问题', guidance: '查看通话中的具体原因，再核对对应的输入、目录或服务。'},
];

const itemGuidance: Record<string, string> = {
  missing_track: '补齐同一通话的客服与客户 XML 后，新建批次重新扫描。',
  duplicate_track: '检查同一通话的重复声道；修正后新建批次重新扫描。',
  invalid_xml: '检查文件是否为有效且可读取的 XML；修正后新建批次重新扫描。',
  invalid_content: '核对通话标识、角色与可合成正文；修正后新建批次重新扫描。',
  invalid_filename: '核对数字前缀与末位 0／1；修正文件名后新建批次重新扫描。',
  input_changed: '来源文件已变化；确认正确文件后，新建批次重新扫描。',
  duplicate_filename: '同一目录出现重复输出名；修正来源编号后，新建批次重新扫描。',
  output_conflict: '核对已有同名 MP3，或更换批次输出目录；应用不会覆盖已有文件。',
  synthesis_failed: '检查网络和服务状态，勾选联网授权后可重试合成。',
  missing_cache: '完整音频缓存缺失；勾选联网授权后，可重新合成整通。',
  save_failed: '检查输出目录权限与可用空间，再重试保存；会使用现有完整音频。',
  cache_changed: '缓存校验不一致；请核对缓存，或在结束批次时明确放弃该音频。',
};

export function guidanceForItem(item?: WorkspaceItem) {
  return item?.error_code && itemGuidance[item.error_code] || '请按具体原因核对输入、目录或服务，再处理此通。';
}
