import {WorkspaceContent} from './WorkspaceContent';
import {AppDialogs} from './AppDialogs';
import {SettingsDrawer} from './Dialog';
import {Icon} from './Icon';
import {SettingsPanel} from './SettingsPanel';
import {Sidebar} from './Sidebar';
import {WorkspaceMessages} from './WorkspaceMessages';
import {useWorkspace} from './useWorkspace';
import styles from './App.module.css';
import {useWorkbench} from './useWorkbench';
import {WorkspaceHeader} from './WorkspaceToolbar';

export function App() {
  const model = useWorkspace();
  const ui=useWorkbench(model);
  const {workspace,config,scope,setScope,settingsOpen,setSettingsOpen,modal,setModal,theme,auditionSettings,auditionJob,setAuditionJob,auditionRunning,busy,running,settingsTrigger,narrowOpen,setNarrowOpen,updateLocked,importFolder,resetView,changeTheme,pauseAndExit,startAudition}=ui;
  const settingsPanel = <SettingsPanel auditionBusy={auditionRunning} model={model} scope={scope} setScope={setScope} onClose={() => {setSettingsOpen(false); settingsTrigger.current?.focus();}} onHelp={setModal} onAudition={settings => {auditionSettings.current = settings; setAuditionJob(null); setModal('audition');}}/>;

  return <div data-testid="app-shell" className={`${styles.app} ${!settingsOpen ? styles.settingsCollapsed : ''}`} aria-busy={updateLocked}>
    <Sidebar model={model} busy={busy} running={running} importFolder={importFolder} theme={theme} changeTheme={changeTheme} onSettings={() => setModal('settings')} resetView={resetView}/>

    <main className={styles.main}>
      <WorkspaceHeader model={model} ui={ui}/>
      <WorkspaceMessages model={model} ui={ui}/>
      {!workspace ? <div className={styles.empty}><Icon name="file"/><h2>{model.loading ? '正在连接本机服务' : '选择来源文件夹'}</h2><p>批量配对 XML、核对声音并生成 MP3。</p><button className={styles.primaryButton} disabled={!config || busy} onClick={() => void importFolder()}><Icon name="folder"/>选择 XML 文件夹</button></div> : <>
        {workspace.status !== 'scanning' && <span data-testid="workspace-ready" className={styles.srOnly}>批次已载入</span>}
        <WorkspaceContent model={model} ui={ui}/>

      </>}
    </main>
    <div className={styles.desktopSettings}>{settingsOpen && !narrowOpen && settingsPanel}</div>
    {narrowOpen && <SettingsDrawer onClose={() => {setNarrowOpen(false); settingsTrigger.current?.focus();}}><SettingsPanel auditionBusy={auditionRunning} model={model} scope={scope} setScope={setScope} onClose={() => setNarrowOpen(false)} onHelp={kind => {setNarrowOpen(false); setModal(kind);}} onAudition={settings => {setNarrowOpen(false); auditionSettings.current = settings; setAuditionJob(null); setModal('audition');}}/></SettingsDrawer>}

    {modal && <AppDialogs key={modal} model={model} modal={modal} onClose={() => setModal(null)} theme={theme} changeTheme={changeTheme} onPauseExit={pauseAndExit} auditionJob={auditionJob} auditionRunning={auditionRunning} busy={busy} running={running} onStartAudition={startAudition} updates={ui.updates} reports={ui.reports}/>}

  </div>;
}
