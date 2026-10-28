import type {WorkspaceSummary} from '../types/contracts';
import {count} from './format';
import styles from './App.module.css';
import progressStyles from './WorkspaceProgress.module.css';
const statusText = {scanning: '正在扫描', ready: '准备就绪', running: '正在生成', pausing: '正在保存进度', paused: '已暂停', interrupted: '上次运行中断', done: '本次已完成', cancelled: '扫描已取消', error: '需要处理'};
const phaseText: Record<string, string> = {
  enumerating: '正在发现 XML 文件', reading: '正在读取 XML', pairing: '正在准备通话配对',
  validating: '正在配对与核验通话', settings: '正在重算声音设置', checking: '正在核验已有输出',
};

export function WorkspaceProgress({workspace}: {workspace: WorkspaceSummary}) {
  const scanning = workspace.status === 'scanning';
  const label = scanning ? phaseText[workspace.phase] || statusText.scanning : statusText[workspace.status];
  const detail = scanning
    ? ['settings', 'checking'].includes(workspace.phase) ? '处理中' : `已扫描 ${count(workspace.scanned)} 份文件`
    : `已保存 ${count(workspace.saved)} 通 · 需处理 ${count(workspace.error)} 通`;
  return <div className={styles.progressSummary} data-testid={scanning ? 'scan-progress' : 'task-progress'}>
    <div><strong role="status">{label}</strong><span>{detail}</span></div>
    {scanning
      ? <div className={progressStyles.track} role="progressbar" aria-label="扫描进度" aria-valuetext={`${label}，${detail}`}><span className={progressStyles.activity}/></div>
      : <progress aria-label="生成进度" max={Math.max(1, workspace.total)} value={workspace.processed}/>}
    {workspace.message && <p>{workspace.message}</p>}
    {workspace.active_item && <p>当前：{workspace.active_item.filename || '正在处理通话'}</p>}
  </div>;
}
