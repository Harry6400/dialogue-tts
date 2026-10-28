import {Icon} from './Icon';
import {messageOf, type WorkspaceModel} from './useWorkspace';
import {useState} from 'react';
import type {ApplicationUpdate} from './useApplicationUpdate';
import styles from './ApplicationSettings.module.css';

interface Props {
  model: WorkspaceModel;
  theme: 'light' | 'dark';
  changeTheme: () => Promise<void>;
  onPauseExit: () => Promise<void>;
  onClose: () => void;
  busy: boolean;
  running: boolean;
  updates: ApplicationUpdate;
}

function UpdateFeedback({updates, disabled}: {updates: ApplicationUpdate; disabled: boolean}) {
  const {feedback, pending, availableVersion} = updates;
  if (feedback.state === 'idle') return null;
  const isError = feedback.state === 'error';
  return <div className={`${styles.updateStatus} ${isError ? styles.updateError : ''}`} role={isError ? 'alert' : 'status'}><Icon name={isError ? 'warning' : feedback.state === 'latest' ? 'check' : 'info'}/><span>{feedback.message}</span>{availableVersion && <button className={styles.install} disabled={disabled || pending} onClick={() => void updates.install()}><Icon name="download"/>{feedback.state === 'preparing' ? '正在准备…' : '下载并安装更新'}</button>}</div>;
}

export function ApplicationSettings({model, theme, changeTheme, onPauseExit, onClose, busy, running, updates}: Props) {
  const {workspace, config} = model;
  const [exitError, setExitError] = useState('');
  const {feedback, pending} = updates;
  const versionLabel = config?.app_version ? '当前版本 ' + config.app_version + (config.mac_build_number ? ' · Mac 构建 ' + config.mac_build_number : '') : '版本信息暂不可用';

  async function requestClose() {
    try {
      if (model.native.current) model.nativeValue(await model.native.current.call('request_close', {}));
    } catch (issue) {setExitError(messageOf(issue));}
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
        <div className={styles.description}><strong>版本更新</strong><p>{versionLabel}</p></div>
        <button className={styles.action} disabled={busy || running || pending} onClick={() => void updates.check()}>{feedback.state === 'checking' ? '正在检查…' : '检查更新'}</button>
      </div>
      <UpdateFeedback updates={updates} disabled={busy || running}/>
    </section>

    {workspace && <section className={`${styles.section} ${styles.taskSection}`} aria-labelledby="application-exit">
      <div className={styles.sectionHeading}><h3 id="application-exit">退出应用</h3></div>
      <div className={styles.row}>
        <span className={styles.rowIcon}><Icon name="exit"/></span>
        <div className={styles.description}><strong>保存进度并退出</strong><p>下次打开可恢复，继续生成需再次授权</p></div>
        <button className={styles.action} disabled={busy} onClick={() => {onClose(); void onPauseExit();}}>保存并退出</button>
      </div>
    </section>}
    {!workspace && config?.can_shutdown && <div className={styles.quit}><button className={styles.action} disabled={busy} onClick={() => void requestClose()}><Icon name="exit"/>退出对白</button></div>}
    {exitError && <p className={styles.updateError} role="alert">{exitError}</p>}
  </div>;
}
