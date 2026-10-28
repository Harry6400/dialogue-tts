import type {WorkspaceActiveItem, WorkspaceItem} from '../types/contracts';
import {Icon} from './Icon';
import {statusLabels} from './format';
import styles from './CallProgress.module.css';
const phases: Record<string,string> = {connecting:'连接中',waiting_audio:'等待音频',receiving:'接收音频',retrying:'连接重试',merging:'合并音频',saving:'正在保存',done:'合成完成'};
export function CallProgress({item,progress,pausing=false}: {item:WorkspaceItem;progress?:WorkspaceActiveItem|null;pausing?:boolean}) {
  const current = progress?.id === item.id && ['running','saving'].includes(item.status) ? progress : null;
  if (!current) return <div className={styles.idle}><span className={`${styles.status} ${item.status==='saved'?styles.saved:''} ${item.error?styles.warning:''}`}>{item.status==='saved'&&<Icon name="check"/>}{statusLabels[item.status] || item.status}</span></div>;
  const total=Math.max(1,current.total), completed=Math.max(0,Math.min(total,current.completed));
  const phase=item.status==='saving'?'saving':current.phase;
  const label=pausing?'本通结束后暂停':phases[phase] || '正在处理';
  const route=current.connection_mode==='system_proxy'?'系统代理':'直连';
  const waiting=['connecting','waiting_audio','retrying'].includes(phase);
  const note=waiting?`${Math.floor((current.phase_elapsed_ms||0)/1000)}秒`:'';
  const attemptNumber=Math.max(1,current.attempt||1), attemptLimit=Math.max(1,current.attempt_limit||5);
  const attempt=phase==='retrying'?` · 已试 ${attemptNumber}/${attemptLimit} 次`:phase==='connecting'?` · 第 ${attemptNumber}/${attemptLimit} 次`:'';
  const title=`${label}${attempt}${waiting?` · 已等待 ${note}`:''}`;
  return <div className={styles.progress} aria-label={item.filename+'逐通进度'}>
    <span className={styles.label} title={title}>{label}{attempt}{waiting&&<span className={styles.wait}> · {note}</span>}</span>
    <progress aria-label={item.filename+'合成段数'} max={total} value={completed}/>
    <span className={styles.detail} title={`${completed}/${total} 段 · ${route}`}>{completed}/{total} 段 · {route}</span>
  </div>;
}
