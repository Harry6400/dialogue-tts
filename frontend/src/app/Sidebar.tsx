import {Icon} from './Icon';
import brandImage from '../../../assets/dialogue.png';
import type {WorkspaceModel} from './useWorkspace';
import {TaskSwitcher} from './TaskSwitcher';
import styles from './App.module.css';

interface Props {model: WorkspaceModel; section: 'batch' | 'tasks' | 'results'; busy: boolean; running: boolean; importFolder: () => Promise<void>; navigate: (value: 'batch' | 'tasks' | 'results') => void; theme: 'light' | 'dark'; changeTheme: () => Promise<void>; onSettings: () => void; resetView: () => void}
export function Sidebar({model, section, busy, running, importFolder, navigate, theme, changeTheme, onSettings, resetView}: Props) {
  const workspace = model.workspace;
  return <aside data-testid="sidebar" className={styles.sidebar} aria-label="主导航">
      <div className={styles.brand}><img className={styles.brandImage} src={brandImage} alt=""/><span>对白</span></div>
      <button data-testid="import-folder" className={styles.newBatch} onClick={() => void importFolder()} disabled={busy || running}><Icon name="plus"/>新建批次</button>
      <nav className={styles.nav}>{([['batch', 'file', '当前批次'], ['tasks', 'play', '生成进度'], ['results', 'folder', '已保存结果']] as const).map(([key, icon, label]) => <button key={key} aria-current={section === key ? 'page' : undefined} onClick={() => navigate(key)}><Icon name={icon}/><span>{label}</span>{key === 'tasks' && running && <span className={styles.navCount}>1</span>}</button>)}</nav>
      {!!model.workspaces.length && <TaskSwitcher workspaces={model.workspaces} value={workspace?.id} disabled={busy || running || !!model.safety?.busy} onChange={value => {model.chooseWorkspace(value); resetView();}}/>}
      <div className={styles.sidebarBottom}><button className={styles.navButton} onClick={onSettings}><Icon name="settings"/><span>应用设置</span></button><button data-testid="theme-toggle" className={styles.navButton} onClick={() => void changeTheme()} aria-label={theme === 'light' ? '切换深色主题' : '切换浅色主题'}><Icon name={theme === 'light' ? 'moon' : 'sun'}/><span>{theme === 'light' ? '深色外观' : '浅色外观'}</span></button><span className={styles.version}>本机工作台</span></div>
    </aside>;
}
