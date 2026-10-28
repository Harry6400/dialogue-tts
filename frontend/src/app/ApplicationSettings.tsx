import {Icon} from './Icon';
import {messageOf, type WorkspaceModel} from './useWorkspace';
import {useEffect, useState, type FormEvent} from 'react';
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

function parseRetryAttempts(value: string) {
  const attempts = Number(value);
  return Number.isInteger(attempts) && attempts >= 1 && attempts <= 30 ? attempts : null;
}

function RetryAttemptSettings({model, disabled, networkPending, setNetworkPending}: {
  model: WorkspaceModel; disabled: boolean; networkPending: boolean; setNetworkPending: (pending: boolean) => void;
}) {
  const configuredAttempts = model.config?.retry_attempts ?? 5;
  const [retryDraft, setRetryDraft] = useState(String(configuredAttempts));
  const [retryError, setRetryError] = useState('');
  const [retryStatus, setRetryStatus] = useState('');
  useEffect(() => {setRetryDraft(String(configuredAttempts));}, [configuredAttempts]);
  async function saveRetryAttempts(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const attempts = parseRetryAttempts(retryDraft);
    if (attempts === null) {
      setRetryError('请输入 1–30 之间的整数。'); setRetryStatus(''); return;
    }
    setNetworkPending(true); setRetryError(''); setRetryStatus('');
    try {
      const result = await model.api.setRetryAttempts(attempts);
      if (!result.ok || result.retry_attempts !== attempts) throw new Error('设置未能保存，请重试。');
      model.setConfig(previous => previous ? {...previous, retry_attempts: attempts} : previous);
      setRetryStatus('已保存。');
    } catch (issue) {setRetryError(messageOf(issue));}
    finally {setNetworkPending(false);}
  }
  return <>
      <form className={`${styles.row} ${styles.retryRow}`} onSubmit={event => void saveRetryAttempts(event)}>
        <span className={styles.rowIcon}><Icon name="refresh"/></span>
        <div className={styles.description}>
          <label className={styles.retryLabel} htmlFor="speech-retry-attempts">连接尝试次数</label>
          <p id="speech-retry-help">每段合成，包含首次连接；范围 1–30。高次数会延长单段等待，最多 5 分钟。</p>
        </div>
        <div className={styles.retryControl}>
          <input id="speech-retry-attempts" className={styles.retryInput} type="number" min={1} max={30} step={1} inputMode="numeric" required
            value={retryDraft} aria-describedby={retryError ? 'speech-retry-error' : 'speech-retry-help'} aria-invalid={!!retryError}
            disabled={disabled || networkPending || !model.config}
            onChange={event => {setRetryDraft(event.currentTarget.value); setRetryError(''); setRetryStatus('');}} />
          <button className={styles.action} type="submit" disabled={disabled || networkPending || !model.config || parseRetryAttempts(retryDraft) === configuredAttempts}>保存</button>
        </div>
      </form>
      {retryError && <p className={styles.retryFeedback} id="speech-retry-error" role="alert">{retryError}</p>}
      {retryStatus && <p className={styles.retryFeedback} role="status">{retryStatus}</p>}
  </>;
}

function ConnectionSettings({model, disabled}: {model: WorkspaceModel; disabled: boolean}) {
  const [networkPending, setNetworkPending] = useState(false);
  const [networkError, setNetworkError] = useState('');
  async function changeConnection(connection_mode: 'direct' | 'system_proxy') {
    setNetworkPending(true); setNetworkError('');
    try {await model.api.setConnectionMode(connection_mode); model.setConfig(previous => previous ? {...previous, connection_mode} : previous);}
    catch (issue) {setNetworkError(messageOf(issue));}
    finally {setNetworkPending(false);}
  }
  return <>
    <div className={styles.row}>
      <span className={styles.rowIcon}><Icon name="info"/></span>
      <div className={styles.description}><strong id="speech-connection">语音连接</strong><p id="speech-connection-help">默认直连；系统代理使用电脑当前配置。仅影响语音合成和试听。</p></div>
      <div className={styles.appearance} role="group" aria-labelledby="speech-connection" aria-describedby="speech-connection-help">
        <button disabled={disabled || networkPending || !model.config} aria-pressed={(model.config?.connection_mode ?? 'direct') === 'direct'} onClick={() => void changeConnection('direct')}>直连</button>
        <button disabled={disabled || networkPending || !model.config} aria-pressed={model.config?.connection_mode === 'system_proxy'} onClick={() => void changeConnection('system_proxy')}>系统代理</button>
      </div>
    </div>
    <RetryAttemptSettings model={model} disabled={disabled} networkPending={networkPending} setNetworkPending={setNetworkPending}/>
    {networkError && <p className={styles.updateError} role="alert">{networkError}</p>}
  </>;
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
        <div className={styles.description}><strong>外观</strong></div>
        <div className={styles.appearance} role="group" aria-label="应用外观">
          <button disabled={busy} aria-pressed={theme === 'light'} onClick={() => {if (theme !== 'light') void changeTheme();}}><Icon name="sun"/>浅色</button>
          <button disabled={busy} aria-pressed={theme === 'dark'} onClick={() => {if (theme !== 'dark') void changeTheme();}}><Icon name="moon"/>深色</button>
        </div>
      </div>
      <div className={styles.row}>
        <span className={styles.rowIcon}><Icon name="refresh"/></span>
        <div className={styles.description}><strong>版本更新</strong><p>{versionLabel}</p></div>
        <button className={styles.action} disabled={busy || running || pending} onClick={() => void updates.check()}>{feedback.state === 'checking' ? '正在检查…' : '检查更新'}</button>
      </div>
      <UpdateFeedback updates={updates} disabled={busy || running}/>
      <ConnectionSettings model={model} disabled={busy || running}/>
    </section>

    {workspace && <section className={`${styles.section} ${styles.taskSection}`} aria-labelledby="application-exit">
      <div className={styles.sectionHeading}><h3 id="application-exit">退出应用</h3></div>
      <div className={styles.row}>
        <span className={styles.rowIcon}><Icon name="exit"/></span>
        <div className={styles.description}><strong>保存进度并退出</strong><p>下次打开可恢复任务</p></div>
        <button className={styles.action} disabled={busy} onClick={() => {onClose(); void onPauseExit();}}>保存并退出</button>
      </div>
    </section>}
    {!workspace && config?.can_shutdown && <div className={styles.quit}><button className={styles.action} disabled={busy} onClick={() => void requestClose()}><Icon name="exit"/>退出对白</button></div>}
    {exitError && <p className={styles.updateError} role="alert">{exitError}</p>}
  </div>;
}
