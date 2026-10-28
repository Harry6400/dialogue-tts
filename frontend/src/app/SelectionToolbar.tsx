import {canEdit, type WorkspaceModel} from './useWorkspace';
import {count} from './format';
import styles from './App.module.css';
export function SelectionToolbar({model,busy}: {model: WorkspaceModel; busy: boolean}) {
  const workspace = model.workspace;
  if (!workspace) return null;
  return <div className={styles.selectionTools}>{workspace.selected ? <><span>已选 {count(workspace.selected)} 通{model.hiddenSelection ? `，其中 ${count(model.hiddenSelection)} 通不在当前筛选` : ''}</span><button className={styles.textButton} disabled={busy || !canEdit(workspace)} onClick={() => void model.select({mode: 'all'})} aria-label="选择筛选中全部通话">全选匹配项</button><button className={styles.textButton} onClick={() => model.setFilter('selected')}>查看已选</button><button className={styles.textButton} disabled={busy || !canEdit(workspace)} onClick={() => void model.select({mode: 'clear'})}>清空选择</button></> : <span>0 通已选</span>}</div>;
}
