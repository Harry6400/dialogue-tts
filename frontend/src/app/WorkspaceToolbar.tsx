import type {WorkspaceFilter} from '../types/contracts';
import type {WorkspaceModel} from './useWorkspace';
import type {WorkbenchUi} from './useWorkbench';
import {count} from './format';
import {Icon} from './Icon';
import styles from './App.module.css';
const filters: [WorkspaceFilter,string][]=[['all','全部'],['ready','待生成'],['running','进行中'],['error','需处理'],['saved','已保存']];
export function WorkspaceHeader({model,ui}: {model:WorkspaceModel;ui:WorkbenchUi}) {
  const {mode,setMode,setScope,section,settingsTrigger,settingsOpen,setNarrowOpen,setSettingsOpen,importFolder,busy,running}=ui;
  const workspace=model.workspace;
  const title=section==='tasks'?'生成进度':section==='results'?'已保存结果':'当前批次';
  const description=section==='tasks'?'当前批次的生成与保存进度':section==='results'?'当前批次已保存的 MP3，可查看与播放':'选择通话，核对正文后批量生成';
  return <header className={styles.mainHeader}><div className={styles.heading}>{mode === 'review' ? <button className={styles.backButton} onClick={() => {setMode('overview'); setScope('batch');}}><Icon name="back"/>批次总览</button> : <div className={styles.headingCopy}><div><h1>{title}</h1>{workspace && <span>{section==='results'?`${count(workspace.saved)} 个 MP3`:`批次共 ${count(workspace.total)} 通`}</span>}</div><p>{description}</p></div>}</div><div className={styles.toolbar}>{workspace && <button className={styles.secondaryButton} onClick={() => void importFolder()} disabled={busy || running}><Icon name="plus"/>添加批次</button>}<button ref={settingsTrigger} className={styles.iconButton} aria-label="显示声音与输出" aria-expanded={window.innerWidth<=1100?ui.narrowOpen:settingsOpen} onClick={() => {if (window.innerWidth <= 1100) setNarrowOpen(true); else setSettingsOpen(true);}}><Icon name="settings"/><span>声音与输出</span></button></div></header>;
}
export function WorkspaceSearch({model,ui}: {model:WorkspaceModel;ui:WorkbenchUi}) {
  const {searchRef,search,setSearch,mode,setMode,setScope,review}=ui;
  const workspace=model.workspace;
  if(!workspace)return null;
  const shortcut=/Mac|iPhone|iPad/.test(navigator.platform)?'⌘ F':'Ctrl F';
  return <div className={styles.workspaceTools}>
    <div className={styles.filters} aria-label="通话状态筛选">{filters.map(([key,label])=><button key={key} aria-pressed={model.filter===key} onClick={()=>model.setFilter(key)}>{label}<span>{key==='all'?count(workspace.total):key==='ready'?count(workspace.ready):key==='error'?count(workspace.error):key==='saved'?count(workspace.saved):''}</span></button>)}</div>
    <label className={styles.searchField}><Icon name="search"/><input ref={searchRef} type="search" aria-label="搜索通话编号" placeholder="搜索编号" value={search} onChange={event=>setSearch(event.target.value)}/>{search?<button className={styles.iconButton} aria-label="清除搜索" onClick={()=>setSearch('')}><Icon name="close"/></button>:<kbd>{shortcut}</kbd>}</label>
    <div className={styles.viewSwitch} aria-label="工作方式"><button aria-pressed={mode==='overview'} onClick={()=>{setMode('overview');setScope('batch');}}>列表总览</button><button aria-pressed={mode==='review'} disabled={!model.activeId} onClick={review}>正文核对</button></div>
  </div>;
}
