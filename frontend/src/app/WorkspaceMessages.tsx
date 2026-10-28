import type {WorkspaceModel} from './useWorkspace';
import type {WorkbenchUi} from './useWorkbench';
import {Icon} from './Icon';
import {WorkspaceProgress} from './WorkspaceProgress';
import {count} from './format';
import styles from './App.module.css';
function DirectoryNotice({model,ui}: {model:WorkspaceModel;ui:WorkbenchUi}) {
  const {workspace,restoredAccess,needsVerification,busy,running}=ui;
  if (!workspace || restoredAccess && !needsVerification) return null;
  return <div className={styles.notice}><Icon name="folder"/><span>{restoredAccess ? '仍有已保存结果待核验，请确认相关文件夹。' : '任务进度已保留。继续前需要核验文件夹访问权限。'}</span><button className={styles.secondaryButton} disabled={busy || running} onClick={() => void model.restoreAccess()}>{restoredAccess ? '核验已有结果' : '恢复访问'}</button><button className={styles.textButton} disabled={busy || running} onClick={() => void model.replaceInput()}>更换来源</button></div>;
}
export function WorkspaceMessages({model,ui}: {model:WorkspaceModel;ui:WorkbenchUi}) {
  const {workspace,running,resumable}=ui;
  return <>{model.error && <div className={styles.errorBanner} role="alert"><Icon name="warning"/><span>{model.error}</span><button className={styles.textButton} onClick={() => {model.setError(''); model.refresh();}}>重新核对状态</button></div>}
      <DirectoryNotice model={model} ui={ui}/>
      {workspace && workspace.save_pending > 0 && <div className={styles.notice} data-testid="pending-audio"><Icon name="download"/><span>{count(workspace.save_pending)} 通完整音频待保存。{running ? '队列会优先保存这些音频。' : '可重试保存，无需重新合成。'}</span></div>}
      {workspace && (running || resumable || model.filter === 'running') && <WorkspaceProgress workspace={workspace}/>}</>;
}
