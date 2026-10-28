import type {WorkspaceItem} from '../types/contracts';
export const statusLabels: Record<WorkspaceItem['status'], string> = {ready: '待生成', invalid: '需处理', queued: '排队中', running: '合成中', saving: '保存中', save_pending: '待保存', saved: '已保存', synthesis_error: '合成失败', save_error: '保存失败', conflict: '同名冲突', changed: '输入已变化', unverified: '待核验'};
export const selectable = (item: WorkspaceItem) => item.status === 'ready';
export const shortPath = (path?: string) => path?.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || '未选择文件夹';
export const count = (value: number) => Number.isFinite(value) ? value.toLocaleString('zh-CN') : '0';
export const shortVoiceName = (label?: string) => label?.split(/[·•（(]/)[0].trim() || '—';
export const itemTitle = (item?: WorkspaceItem | null) => item?.filename || item?.source_name || '文件待处理';

export function voiceSource(value: string) {
  if (value.startsWith('称呼：')) return '称呼匹配';
  if (value === '称呼未判定' || value === '自动匹配关闭') return '批次设置';
  return ({manual: '手动选择', override: '手动选择', address: '称呼匹配', matched: '称呼匹配', auto: '称呼匹配', default: '批次设置', global: '批次设置', female: '称呼匹配', male: '称呼匹配'} as Record<string, string>)[value] || value;
}
