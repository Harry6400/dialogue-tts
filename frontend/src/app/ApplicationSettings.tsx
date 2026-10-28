import {useState} from 'react';
import {Icon} from './Icon';
import {shortPath} from './format';
import {messageOf, type WorkspaceModel} from './useWorkspace';
import styles from './ApplicationSettings.module.css';

interface Props {
  model: WorkspaceModel;
  theme: 'light' | 'dark';
  changeTheme: () => Promise<void>;
  onPauseExit: () => Promise<void>;
  onFinish: () => void;
  onClose: () => void;
  busy: boolean;
  running: boolean;
}

export function ApplicationSettings({model, theme, changeTheme, onPauseExit, onFinish, onClose, busy, running}: Props) {
  const {workspace, config} = model;
  const [updateStatus, setUpdateStatus] = useState<{text: string; error: boolean} | null>(null);
  const [updateAvailable, setUpdateAvailable] = useState(false);
  const [currentVersion, setCurrentVersion] = useState('');
  const [checking, setChecking] = useState<'check' | 'install' | null>(null);

  async function checkUpdate(install = false) {
    if (!model.native.current) {
      setUpdateStatus({text: '请在对白桌面应用中检查更新。', error: true});
      return;
    }
    setChecking(install ? 'install' : 'check');
    await model.perform(install ? '准备更新' : '检查更新', async () => {
      try {
        const result = model.nativeValue(await model.native.current!.call(install ? 'install_update' : 'check_for_update', {}));
        if (!result) {
          setUpdateStatus({text: '已取消更新。', error: false});
          return;
        }
        setUpdateAvailable(result.state === 'available');
        if (result.state === 'latest' || result.state === 'available') {
          setCurrentVersion(result.current_version);
          setUpdateStatus({text: result.state === 'latest' ? '当前已是最新版本' : '可更新至 ' + result.latest_version, error: false});
        } else {
          setUpdateStatus({text: ('message' in result && result.message) || (result.state === 'cancelled' ? '已取消更新。' : '正在准备更新…'), error: result.state === 'error'});
        }
      } catch (issue) {
        setUpdateAvailable(false);
        setUpdateStatus({text: messageOf(issue), error: true});
      }
    });
    setChecking(null);
  }

  async function requestClose() {
    try {
      if (model.native.current) model.nativeValue(await model.native.current.call('request_close', {}));
    } catch (issue) {setUpdateStatus({text: messageOf(issue), error: true});}
  }

  return <div className={styles.settings}>
    <section className={styles.section} aria-labelledby="application-preferences">
      <h3 id="application-preferences">使用偏好</h3>
      <div className={styles.row}>
        <span className={styles.rowIcon}><Icon name="sun"/></span>
        <div className={styles.description}><strong>外观</strong><p>选择适合当前环境的界面</p></div>
        <div className={styles.appearance} role="group" aria-label="应用外观">
          <button aria-pressed={theme === 'light'} onClick={() => {if (theme !== 'light') void changeTheme();}}><Icon name="sun"/>浅色</button>
          <button aria-pressed={theme === 'dark'} onClick={() => {if (theme !== 'dark') void changeTheme();}}><Icon name="moon"/>深色</button>
        </div>
      </div>
      <div className={styles.row}>
        <span className={styles.rowIcon}><Icon name="refresh"/></span>
        <div className={styles.description}><strong>版本更新</strong><p>{currentVersion ? '当前版本 ' + currentVersion : '检查可用的正式版本'}</p></div>
        <button className={styles.action} disabled={busy || running || !!checking} onClick={() => void checkUpdate()}>{checking === 'check' ? '正在检查…' : '检查更新'}</button>
      </div>
      {updateStatus && <div className={`${styles.updateStatus} ${updateStatus.error ? styles.updateError : ''}`} role={updateStatus.error ? 'alert' : 'status'}><Icon name={updateStatus.error ? 'warning' : 'info'}/><span>{updateStatus.text}</span>{updateAvailable && <button className={styles.install} disabled={busy || running || !!checking} onClick={() => void checkUpdate(true)}><Icon name="download"/>{checking === 'install' ? '正在准备…' : '下载并安装更新'}</button>}</div>}
    </section>

    {workspace && <section className={`${styles.section} ${styles.taskSection}`} aria-labelledby="application-current-batch">
      <div className={styles.sectionHeading}><h3 id="application-current-batch">当前批次</h3><span>{shortPath(workspace.input_folder.path)}</span></div>
      <div className={styles.row}>
        <span className={styles.rowIcon}><Icon name="exit"/></span>
        <div className={styles.description}><strong>保存进度并退出</strong><p>下次打开可恢复，继续生成需再次授权</p></div>
        <button className={styles.action} disabled={busy} onClick={() => {onClose(); void onPauseExit();}}>保存并退出</button>
      </div>
      <div className={styles.row}>
        <span className={styles.rowIcon}><Icon name="file"/></span>
        <div className={styles.description}><strong>结束当前批次</strong><p>清理本机任务记录，保留已保存的 MP3</p></div>
        <button className={`${styles.action} ${styles.dangerAction}`} disabled={busy || running} onClick={onFinish}>结束并清理…</button>
      </div>
    </section>}
    {!workspace && config?.can_shutdown && <div className={styles.quit}><button className={styles.action} disabled={busy} onClick={() => void requestClose()}><Icon name="exit"/>退出对白</button></div>}
  </div>;
}
