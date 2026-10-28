import type {WorkspaceSummary} from '../types/contracts';
import {Icon} from './Icon';
import {count} from './format';
import type {WorkspaceModel} from './useWorkspace';
import {workspaceActions} from './workspaceActions';
import styles from './App.module.css';
const statusText = {scanning: '正在扫描', ready: '准备就绪', running: '正在生成', pausing: '正在保存进度', paused: '已暂停', interrupted: '上次运行中断', done: '本次已完成', cancelled: '扫描已取消', error: '需要处理'};
interface Props {model: WorkspaceModel; busy: boolean; running: boolean; onStart: () => Promise<void>}
function footerState({model, busy}: Props, workspace: WorkspaceSummary) {
  const actions = workspaceActions({workspace, output: model.output, busy, loading: model.loading, safety: model.safety});
  const selectedTitle = `已选 ${count(workspace.selected)} 通` + (workspace.selected !== workspace.ready_selected ? ` · 可生成 ${count(workspace.ready_selected)} 通` : '');
  return {...actions, title: actions.running ? statusText[workspace.status] : actions.resumable ? '进度已保留，可继续' : selectedTitle};
}
function PrimaryAction({props, workspace, state}: {props: Props; workspace: WorkspaceSummary; state: ReturnType<typeof footerState>}) {
  const {model,onStart} = props;
  if (state.kind === 'cancel') return <button className={styles.secondaryButton} disabled={!state.enabled} onClick={() => void model.mutate({kind: 'cancel', label: '停止扫描'}, () => model.api.cancelWorkspaceScan(workspace.id))}>停止扫描</button>;
  if (state.kind === 'pause') return <button data-testid="pause" className={styles.secondaryButton} disabled={!state.enabled} onClick={() => void model.mutate({kind: 'pause', label: '保存进度'}, () => model.api.pauseWorkspace(workspace.id))}><Icon name="pause"/>{workspace.status === 'pausing' ? '本通结束后暂停…' : '暂停'}</button>;
  return <button data-testid={state.resumable ? 'resume' : 'generate'} className={styles.primaryButton} disabled={!state.enabled} title={state.help || undefined} aria-describedby={state.help ? 'generate-help' : undefined} onClick={() => void onStart()}><Icon name="play"/>{state.label}</button>;
}
function RetryActions({props, workspace}: {props: Props; workspace: WorkspaceSummary}) {
  const {model} = props;
  const state = footerState(props,workspace);
  if (state.running) return null;
  return <>{!!(workspace.save_error || workspace.save_pending) && <button className={styles.secondaryButton} disabled={!state.retrySave} onClick={() => void model.mutate({kind: 'retry_save', label: '重试保存'}, () => model.api.retryWorkspace(workspace.id, 'save'))}>重试保存</button>}{!!workspace.synthesis_error && <button className={styles.secondaryButton} disabled={!state.retrySynthesis} onClick={() => void model.mutate({kind: 'retry_synthesis', label: '重试合成失败项'}, () => model.api.retryWorkspace(workspace.id, 'synthesis'))}>重试合成</button>}</>;
}
export function WorkspaceFooter(props: Props) {
  const {model} = props;
  const workspace = model.workspace;
  if (!workspace) return null;
  const state = footerState(props,workspace);
  return <div className={styles.runControls} role="group" aria-label={state.title}><RetryActions props={props} workspace={workspace}/><PrimaryAction props={props} workspace={workspace} state={state}/></div>;
}
