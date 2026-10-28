import type {WorkspaceSummary} from '../types/contracts';
import {Icon} from './Icon';
import {count} from './format';
import type {WorkspaceModel} from './useWorkspace';
import styles from './App.module.css';
const statusText = {scanning: '正在扫描', ready: '准备就绪', running: '正在生成', pausing: '正在保存进度', paused: '已暂停', interrupted: '上次运行中断', done: '本次已完成', cancelled: '扫描已取消', error: '需要处理'};
interface Props {model: WorkspaceModel; consent: boolean; setConsent: (value: boolean) => void; busy: boolean; running: boolean; restoredAccess: boolean; onStart: () => Promise<void>}
function footerState({model, consent, busy, running, restoredAccess}: Props, workspace: WorkspaceSummary) {
  const resumable = ['paused', 'interrupted'].includes(workspace.status);
  const scanResume = resumable && ['recovery_scan','recovery_settings','recovery_checking'].includes(workspace.phase);
  const offlineAction = ({recovery_scan:'继续扫描',recovery_settings:'继续更新设置',recovery_checking:'继续核验'} as Record<string,string>)[workspace.phase];
  const canStart = !busy && !running && !model.safety?.busy && (scanResume || consent && !!model.output) && (restoredAccess || workspace.phase==='recovery_checking') && (resumable || !!workspace.selected && !!workspace.selection_id);
  let help = '';
  if (workspace.status === 'scanning') help = '扫描完成后可生成';
  else if (!restoredAccess) help = '请先恢复目录访问权限';
  else if (scanResume) help = '离线检查不会发送正文';
  else if (!model.output) help = '请选择结果文件夹';
  else if (!workspace.selected && !resumable) help = '勾选需要生成的通话';
  else if (!consent) help = '勾选联网授权后可生成';
  return {resumable, scanResume, canStart, help, title: running ? statusText[workspace.status] : resumable ? '进度已保留，可继续生成' : `已选 ${count(workspace.selected)} 通`, action: scanResume ? offlineAction : resumable ? '继续生成' : `生成 ${count(workspace.selected)} 通`};
}
function PrimaryAction({props, workspace, state}: {props: Props; workspace: WorkspaceSummary; state: ReturnType<typeof footerState>}) {
  const {model,busy,onStart} = props;
  if (workspace.status === 'scanning') return <button className={styles.secondaryButton} disabled={busy} onClick={() => void model.mutate('停止扫描', () => model.api.cancelWorkspaceScan(workspace.id))}>停止扫描</button>;
  if (workspace.status === 'running' || workspace.status === 'pausing') return <button data-testid="pause" className={styles.secondaryButton} disabled={busy || workspace.status === 'pausing'} onClick={() => void model.mutate('保存进度', () => model.api.pauseWorkspace(workspace.id))}><Icon name="pause"/>{workspace.status === 'pausing' ? '保存进度中…' : '暂停'}</button>;
  return <button data-testid={state.resumable ? 'resume' : 'generate'} className={styles.primaryButton} disabled={!state.canStart} aria-describedby={state.help ? 'generate-help' : undefined} onClick={() => void onStart()}><Icon name="play"/>{state.action}</button>;
}
function RetryActions({props, workspace}: {props: Props; workspace: WorkspaceSummary}) {
  const {model, busy, restoredAccess, running, consent, setConsent} = props;
  if (running) return null;
  return <>{!!(workspace.save_error || workspace.counts?.save_pending) && <button className={styles.secondaryButton} disabled={busy || !restoredAccess} onClick={() => void model.mutate('重试保存', () => model.api.retryWorkspace(workspace.id, 'save'))}>重试保存</button>}{!!workspace.synthesis_error && <button className={styles.secondaryButton} disabled={busy || !restoredAccess || !consent} onClick={() => {void model.mutate('重试合成失败项', () => model.api.retryWorkspace(workspace.id, 'synthesis')); setConsent(false);}}>重试合成</button>}</>;
}
export function WorkspaceFooter(props: Props) {
  const {model,consent,setConsent,running} = props;
  const workspace = model.workspace;
  if (!workspace) return null;
  const state = footerState(props,workspace);
  return <footer className={styles.footer}><div className={styles.footerInfo}><strong>{state.title}</strong>{!running && <label className={styles.consent}><input data-testid="consent" type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)}/>允许将本次文本发送给微软合成语音</label>}{state.help && !running && <span className={styles.disabledReason} id="generate-help">{state.help}</span>}</div><div className={styles.footerActions}><PrimaryAction props={props} workspace={workspace} state={state}/><RetryActions props={props} workspace={workspace}/></div></footer>;
}
