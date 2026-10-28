import type {WorkspaceModel} from './useWorkspace';
import type {WorkbenchUi} from './useWorkbench';
import {Icon} from './Icon';
import {WorkspaceProgress} from './WorkspaceProgress';
import styles from './App.module.css';
export function WorkspaceMessages({model,ui}: {model:WorkspaceModel;ui:WorkbenchUi}) {
  const {workspace,restoredAccess,busy,running,resumable}=ui;
  return <>{model.error && <div className={styles.errorBanner} role="alert"><Icon name="warning"/><span>{model.error}</span><button className={styles.textButton} onClick={() => {model.setError(''); model.refresh();}}>重新核对状态</button></div>}
      {workspace && !restoredAccess && <div className={styles.notice}><Icon name="folder"/><span>任务进度已保留。继续前需要核验文件夹访问权限。</span><button className={styles.secondaryButton} disabled={busy} onClick={() => void model.restoreAccess()}>恢复访问</button><button className={styles.textButton} disabled={busy || running} onClick={() => void model.replaceInput()}>更换来源</button></div>}
      {workspace && (running || resumable || model.filter === 'running') && <WorkspaceProgress workspace={workspace}/>}</>;
}
