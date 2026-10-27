import {useState} from 'react';
import {ApiClient} from '../services/api';
import type {Config} from '../types/contracts';
import styles from './App.module.css';

export function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [status, setStatus] = useState('尚未连接本机服务');
  const [loading, setLoading] = useState(false);
  async function inspect() {
    setLoading(true);
    try {setConfig(await new ApiClient().getConfig()); setStatus('已读取本机配置；没有创建合成任务。');}
    catch (error) {setStatus(error instanceof Error ? error.message : '无法读取本机配置。');}
    finally {setLoading(false);}
  }
  return <main className={styles.shell}>
    <header className={styles.header}><div><span className={styles.brand}>对白</span><h1>开发准备</h1></div><span className={styles.stage}>正式页面尚未切换</span></header>
    <section className={styles.introduction} aria-labelledby="stage-title"><h2 id="stage-title">先建立可靠的开发基础</h2><p>这里验证 React 工程和本机接口。下一阶段将实现以多通处理为主的列表＋详情工作台。</p><button onClick={inspect} disabled={loading}>{loading ? '正在读取…' : '读取本机配置'}</button><p role="status">{status}</p></section>
    <section className={styles.workspace} aria-label="工作台准备区域"><div className={styles.list}><h2>通话列表</h2><p>尚未载入对话。当前准备页不导入或发送用户文本。</p></div><div className={styles.detail}><h2>接口核对</h2>{config ? <><p>本机接口返回 {config.voices.length} 种音色。</p><ul>{config.voices.map(voice => <li key={voice.id}>{voice.label}</li>)}</ul></> : <p>启动 Python 本机服务后，可以读取真实配置。未连接时不会展示模拟成功。</p>}</div></section>
  </main>;
}
