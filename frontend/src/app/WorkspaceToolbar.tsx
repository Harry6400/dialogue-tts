import {WorkspaceFooter} from './WorkspaceFooter';
import type {WorkspaceFilter} from '../types/contracts';
import type {WorkspaceModel} from './useWorkspace';
import type {WorkbenchUi} from './useWorkbench';
import {count,shortPath} from './format';
import {Icon} from './Icon';
import {BatchActions} from './BatchActions';
import styles from './App.module.css';
const filters: [WorkspaceFilter,string][]=[['all','全部'],['ready','待生成'],['running','进行中'],['error','需处理'],['saved','已保存']];
export function WorkspaceHeader({model,ui}: {model:WorkspaceModel;ui:WorkbenchUi}) {
  const {mode,setMode,setScope,settingsTrigger,settingsOpen,setNarrowOpen,setSettingsOpen,setModal,busy,running,reports}=ui;
  const workspace=model.workspace;
  return <header className={styles.mainHeader}><div className={styles.heading}>{mode === 'review' ? <button className={styles.backButton} onClick={() => {setMode('overview'); setScope('batch');}}><Icon name="back"/>批次总览</button> : <div className={styles.headingCopy}><h1 title={workspace ? shortPath(workspace.input_folder.path) : undefined}>{workspace ? shortPath(workspace.input_folder.path) || '当前批次' : '当前批次'}</h1>{workspace && <span>{count(workspace.total)} 通</span>}</div>}{ui.actionHelp&&!running&&<p id="generate-help" className={styles.actionHelp}>{ui.actionHelp}</p>}</div><div className={styles.toolbar}>{workspace && <BatchActions name={shortPath(workspace.input_folder.path)} disabled={busy} actions={[
    {id:'report',label:reports.manifest?'继续处理临时 CSV 清单…':'导出本批次完整清单（CSV）',icon:'download',disabled:busy||!reports.canCreate&&!reports.manifest,onSelect:()=>setModal('report')},
    {id:'cancel-reports',label:'取消未保存的临时 CSV 清单…',icon:'close',disabled:busy,onSelect:()=>setModal('report-cleanup')},
    {id:'finish',label:'结束并清理批次…',icon:'file',danger:true,disabled:busy||running,onSelect:()=>setModal('finish')},
  ]}/>}<button ref={settingsTrigger} className={styles.iconButton} aria-label="显示声音与输出" aria-expanded={ui.isNarrow?ui.narrowOpen:settingsOpen} onClick={() => {if (ui.isNarrow) setNarrowOpen(true); else setSettingsOpen(true);}}><Icon name="settings"/><span>声音与输出</span></button>{workspace&&<WorkspaceFooter model={model} busy={busy} running={running} onStart={ui.startOrResume}/>}</div></header>;
}
export function WorkspaceSearchField({ui}: {ui:WorkbenchUi}) {
  const {searchRef,search,setSearch}=ui;
  const shortcut=/Mac|iPhone|iPad/.test(navigator.platform)?'⌘ F':'Ctrl F';
  return <label className={styles.searchField}><Icon name="search"/><input ref={searchRef} type="search" name="call-number" autoComplete="off" spellCheck={false} aria-label="搜索通话编号" placeholder="搜索编号" value={search} onChange={event=>setSearch(event.target.value)}/>{search?<button className={styles.iconButton} aria-label="清除搜索" onClick={()=>setSearch('')}><Icon name="close"/></button>:<kbd>{shortcut}</kbd>}</label>;
}
export function WorkspaceSearch({model,ui}: {model:WorkspaceModel;ui:WorkbenchUi}) {
  const {mode,setMode,setScope,review}=ui;
  const workspace=model.workspace;
  if(!workspace)return null;
  return <div className={styles.workspaceTools}>
    <div className={styles.filters} aria-label="通话状态筛选">{filters.map(([key,label])=><button key={key} aria-pressed={model.filter===key} onClick={()=>model.setFilter(key)}>{label}<span>{key==='all'?count(workspace.total):key==='ready'?count(workspace.ready):key==='error'?count(workspace.error):key==='saved'?count(workspace.saved):''}</span></button>)}</div>
    <div className={styles.viewSwitch} aria-label="工作方式"><button aria-pressed={mode==='overview'} onClick={()=>{setMode('overview');setScope('batch');}}>列表总览</button><button aria-pressed={mode==='review'} disabled={!model.activeId} onClick={review}>正文核对</button></div>
  </div>;
}
