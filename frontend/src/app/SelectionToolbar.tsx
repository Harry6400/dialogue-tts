import type {VoiceId} from '../types/contracts';
import {VoiceSelect} from './VoiceSelect';
import {canEdit, type WorkspaceModel} from './useWorkspace';
import type {WorkbenchUi} from './useWorkbench';
import {WorkspaceSearchField} from './WorkspaceToolbar';
import {Icon} from './Icon';
import {count} from './format';
import styles from './App.module.css';
export function SelectionToolbar({model,ui}: {model: WorkspaceModel; ui: WorkbenchUi}) {
  const workspace = model.workspace;
  if (!workspace) return null;
  const locked=ui.busy || !canEdit(workspace);
  return <div className={styles.selectionTools}><WorkspaceSearchField ui={ui}/><span className={styles.selectionCount} title={model.hiddenSelection ? `其中 ${count(model.hiddenSelection)} 通不在当前筛选` : undefined}>已选 <strong>{count(workspace.selected)}</strong> 通{model.hiddenSelection > 0 && <span className={styles.hiddenSelection}> · 筛选外 {count(model.hiddenSelection)}</span>}</span>{workspace.selected > 0 && <div className={styles.selectionVoice}><VoiceSelect id="selected-voice" label="所选通话客户声音" voices={model.config?.voices||[]} disabled={locked} inherited bulk onChange={voice=>void model.mutate({kind:'settings',label:'保存所选通话客户声音'},()=>model.api.setWorkspaceSettings(workspace.id,{selection_id:workspace.selection_id,customer_voice:voice?voice as VoiceId:null}),true)}/></div>}<div className={styles.selectionActions} role="group" aria-label="通话选择操作"><button className={styles.selectionButton} disabled={locked || !workspace.ready} onClick={() => void model.select({mode: 'all'})} aria-label="选择筛选中全部通话" title="全选匹配项"><Icon name="selectAll"/></button><button className={styles.selectionButton} aria-label="查看已选" title="查看已选" aria-pressed={model.filter==='selected'} disabled={!workspace.selected && model.filter!=='selected'} onClick={() => model.setFilter(model.filter==='selected'?'all':'selected')}><Icon name="selected"/></button><button className={styles.selectionButton} aria-label="清空选择" title="清空选择" disabled={locked || !workspace.selected} onClick={() => void model.select({mode: 'clear'})}><Icon name="clearSelection"/></button></div></div>;
}
