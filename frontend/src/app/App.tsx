import {AppDialogs} from './AppDialogs';
import {SettingsDrawer} from './Dialog';
import {Icon} from './Icon';
import {Reader} from './Reader';
import {SettingsPanel} from './SettingsPanel';
import {Sidebar} from './Sidebar';
import {WorkspaceFooter} from './WorkspaceFooter';
import {WorkspaceMessages} from './WorkspaceMessages';
import {SelectionToolbar} from './SelectionToolbar';
import {WorkspaceTable} from './WorkspaceTable';
import {useWorkspace} from './useWorkspace';
import styles from './App.module.css';
import {useWorkbench} from './useWorkbench';
import {WorkspaceHeader,WorkspaceSearch} from './WorkspaceToolbar';

export function App() {
  const model = useWorkspace();
  const ui=useWorkbench(model);
  const {workspace,config,mode,section,scope,setScope,settingsOpen,setSettingsOpen,modal,setModal,consent,setConsent,theme,previewOpen,setPreviewOpen,auditionSettings,auditionJob,setAuditionJob,auditionRunning,busy,running,restoredAccess,settingsTrigger,narrowOpen,setNarrowOpen,updateLocked,importFolder,navigate,resetView,changeTheme,review,startOrResume,pauseAndExit,startAudition}=ui;
  const settingsPanel = <SettingsPanel auditionBusy={auditionRunning} model={model} scope={scope} setScope={setScope} onClose={() => {setSettingsOpen(false); settingsTrigger.current?.focus();}} onHelp={setModal} onAudition={settings => {auditionSettings.current = settings; setAuditionJob(null); setModal('audition');}}/>;

  return <div data-testid="app-shell" className={`${styles.app} ${!settingsOpen ? styles.settingsCollapsed : ''}`} aria-busy={updateLocked}>
    <Sidebar model={model} section={section} busy={busy} running={running} importFolder={importFolder} navigate={navigate} theme={theme} changeTheme={changeTheme} onSettings={() => setModal('settings')} resetView={resetView}/>

    <main className={styles.main}>
      <WorkspaceHeader model={model} ui={ui}/>
      <WorkspaceMessages model={model} ui={ui}/>
      {!workspace ? <div className={styles.empty}><Icon name="file"/><h2>{model.loading ? '正在连接本机服务' : '选择来源文件夹'}</h2><p>批量配对 XML、核对声音并生成 MP3。</p><button className={styles.primaryButton} disabled={!config || busy} onClick={() => void importFolder()}><Icon name="folder"/>选择 XML 文件夹</button><span>原始文件保持原样，生成前需明确联网授权。</span></div> : <>
        {workspace.status !== 'scanning' && <span data-testid="workspace-ready" className={styles.srOnly}>批次已载入</span>}
        <WorkspaceSearch model={model} ui={ui}/>
        <SelectionToolbar model={model} busy={busy}/>
        <div className={styles.overview} hidden={mode !== 'overview'}><WorkspaceTable model={model} onReview={review} onClearFilters={ui.clearFilters}/>{previewOpen && <Reader model={model} active={mode === 'overview'} compact onReview={review} onSources={() => setModal('sources')} onMatchHelp={() => setModal('match')}/>}<button className={styles.previewToggle} onClick={() => setPreviewOpen(!previewOpen)} aria-expanded={previewOpen}>{previewOpen ? '收起预览' : '显示预览'}<Icon name="chevron"/></button></div>
        <div className={styles.reviewWorkspace} hidden={mode !== 'review'}><div className={styles.reviewList}><WorkspaceTable model={model} onReview={review} onClearFilters={ui.clearFilters} context="review"/></div><Reader model={model} active={mode === 'review'} onReview={review} onSources={() => setModal('sources')} onMatchHelp={() => setModal('match')}/></div>
        <WorkspaceFooter model={model} consent={consent} setConsent={setConsent} busy={busy} running={running} restoredAccess={restoredAccess} onStart={startOrResume}/>

      </>}
    </main>
    <div className={styles.desktopSettings}>{settingsOpen && !narrowOpen && settingsPanel}</div>
    {narrowOpen && <SettingsDrawer onClose={() => {setNarrowOpen(false); settingsTrigger.current?.focus();}}><SettingsPanel auditionBusy={auditionRunning} model={model} scope={scope} setScope={setScope} onClose={() => setNarrowOpen(false)} onHelp={kind => {setNarrowOpen(false); setModal(kind);}} onAudition={settings => {setNarrowOpen(false); auditionSettings.current = settings; setAuditionJob(null); setModal('audition');}}/></SettingsDrawer>}

    {modal && <AppDialogs key={modal} model={model} modal={modal} onClose={() => setModal(null)} onModal={setModal} theme={theme} changeTheme={changeTheme} onPauseExit={pauseAndExit} auditionJob={auditionJob} auditionRunning={auditionRunning} busy={busy} running={running} onStartAudition={startAudition}/>}

  </div>;
}
