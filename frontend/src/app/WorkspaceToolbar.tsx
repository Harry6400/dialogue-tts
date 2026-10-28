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
  return <header className={styles.mainHeader}><div className={styles.heading}>{mode === 'review' ? <button className={styles.backButton} onClick={() => {setMode('overview'); setScope('batch');}}><Icon name="back"/>批次总览</button> : <div className={styles.headingCopy}><div><h1>当前批次</h1>{workspace && <span>批次共 {count(workspace.total)} 通</span>}</div><p>选择通话，核对正文后批量生成</p></div>}</div><div className={styles.toolbar}>{workspace && <BatchActions name={shortPath(workspace.input_folder.path)} disabled={busy} actions={[
    {id:'report',label:reports.manifest?'继续处理临时 CSV 清单…':'导出本批次完整清单（CSV）',icon:'download',disabled:busy||!reports.canCreate&&!reports.manifest,onSelect:()=>setModal('report')},
    {id:'cancel-reports',label:'取消未保存的临时 CSV 清单…',icon:'close',disabled:busy,onSelect:()=>setModal('report-cleanup')},
    {id:'finish',label:'结束并清理批次…',icon:'file',danger:true,disabled:busy||running,onSelect:()=>setModal('finish')},
  ]}/>}<button ref={settingsTrigger} className={styles.iconButton} aria-label="显示声音与输出" aria-expanded={window.innerWidth<=1100?ui.narrowOpen:settingsOpen} onClick={() => {if (window.innerWidth <= 1100) setNarrowOpen(true); else setSettingsOpen(true);}}><Icon name="settings"/><span>声音与输出</span></button></div></header>;
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
