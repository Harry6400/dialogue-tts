import {Icon} from './Icon';
import brandImage from '../../../assets/dialogue.png';
import type {WorkspaceModel} from './useWorkspace';
import {TaskSwitcher} from './TaskSwitcher';
import styles from './App.module.css';

interface Props {model: WorkspaceModel; busy: boolean; running: boolean; importFolder: () => Promise<void>; theme: 'light' | 'dark'; changeTheme: () => Promise<void>; onSettings: () => void; resetView: () => void}
export function Sidebar({model, busy, running, importFolder, theme, changeTheme, onSettings, resetView}: Props) {
  const workspace = model.workspace;
  return <aside data-testid="sidebar" className={styles.sidebar} aria-label="主导航">
      <div className={styles.brand}><img className={styles.brandImage} src={brandImage} alt="" width={32} height={32}/><span>对白</span></div>
      <button data-testid="import-folder" className={styles.newBatch} onClick={() => void importFolder()} disabled={busy || running}><Icon name="plus"/>新建批次</button>
      {!!model.workspaces.length && <TaskSwitcher workspaces={model.workspaces} value={workspace?.id} disabled={busy || running || !!model.safety?.busy} onChange={value => {model.chooseWorkspace(value); resetView();}}/>}
      <div className={styles.sidebarBottom}><button className={styles.navButton} onClick={onSettings}><Icon name="settings"/><span>应用设置</span></button><button data-testid="theme-toggle" className={styles.navButton} onClick={() => void changeTheme()} aria-label={theme === 'light' ? '切换深色主题' : '切换浅色主题'}><Icon name={theme === 'light' ? 'moon' : 'sun'}/><span>{theme === 'light' ? '深色外观' : '浅色外观'}</span></button><span className={styles.version}>本机工作台</span></div>
    </aside>;
}
