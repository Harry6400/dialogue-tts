import {useState} from 'react';
import type {JobSnapshot} from '../types/contracts';
import type {WorkspaceModel} from './useWorkspace';
import {Dialog} from './Dialog';
import {Icon} from './Icon';
import {AudioPlayer} from './AudioPlayer';
import {ApplicationSettings} from './ApplicationSettings';
import styles from './App.module.css';
export type Modal = 'settings' | 'match' | 'names' | 'sources' | 'finish' | 'audition';
interface Props {model: WorkspaceModel; modal: Modal; onClose: () => void; onModal: (value: Modal) => void; theme: 'light' | 'dark'; changeTheme: () => Promise<void>; onPauseExit: () => Promise<void>; auditionJob: JobSnapshot | null; auditionRunning: boolean; busy: boolean; running: boolean; onStartAudition: () => Promise<void>}
function FinishContent({props}: {props: Props}) {
  const {model,onClose,busy,running}=props;
  const workspace=model.workspace;
  const [discardUnsaved,setDiscardUnsaved]=useState(false);
  return <><p>将清理此任务的本机记录和未完成队列。已经保存的 MP3 保留在结果文件夹中。</p><p>清理后不能继续当前任务，请先确认已处理所有需要的结果。</p>{!!(workspace?.has_unsaved_audio || workspace?.save_error || workspace?.counts?.save_pending) && <label className={styles.consent}><input type="checkbox" checked={discardUnsaved} onChange={event => setDiscardUnsaved(event.target.checked)}/>我确认放弃尚未保存的音频缓存</label>}<div className={styles.dialogActions}><button className={styles.secondaryButton} onClick={() => onClose()}>取消</button><button className={styles.dangerButton} disabled={busy || running || !!(workspace?.has_unsaved_audio || workspace?.save_error || workspace?.counts?.save_pending) && !discardUnsaved} onClick={() => {if (workspace) void model.mutate('清理任务记录', () => model.api.finishWorkspace(workspace.id, discardUnsaved)); onClose();}}>结束并清理记录</button></div></>;
}
function AuditionContent({props}: {props: Props}) {
  const {auditionJob,auditionRunning,busy,onClose,onStartAudition}=props;
  return <><p>将固定虚构示例发送给微软，试听当前有效声音、语速和停顿。此操作不发送当前通话正文。</p>{auditionJob?.status === 'done' && auditionJob.download_url ? <AudioPlayer key={auditionJob.id} src={auditionJob.download_url} label="固定示例试听"/> : <p role="status">{auditionRunning ? '正在生成固定示例…' : auditionJob?.error || ''}</p>}<div className={styles.dialogActions}><button className={styles.secondaryButton} onClick={() => onClose()}>关闭</button><button className={styles.primaryButton} disabled={busy || auditionRunning} onClick={() => void onStartAudition()}><Icon name="play"/>允许发送示例并试听</button></div></>;
}
function SourcesContent({model}: {model: WorkspaceModel}) {return <>{model.detail?.sources?.length ? <ul className={styles.sources}>{model.detail.sources.map(source => <li key={source.role + source.name}><span className={styles.role}>{source.role === 'AGENT' ? '客服' : source.role === 'USER' ? '客户' : '未知/无效'}</span><span>{source.name}</span><small>{source.segments} 段</small></li>)}</ul> : <p>此通对话尚无可读取的源文件信息。</p>}<p>角色和文件名来自实际解析，不依据末位猜测角色。</p></>;}
function DialogContent({props}: {props: Props}) {
  switch(props.modal) {
    case 'settings': return <ApplicationSettings model={props.model} theme={props.theme} changeTheme={props.changeTheme} onPauseExit={props.onPauseExit} onFinish={() => props.onModal('finish')} onClose={props.onClose} busy={props.busy} running={props.running}/>;
    case 'finish': return <FinishContent props={props}/>;
    case 'audition': return <AuditionContent props={props}/>;
    case 'sources': return <SourcesContent model={props.model}/>;
    case 'match': return <><p>只根据客服对客户的明确“先生／女士”称呼匹配客户声音。无法判断或称呼冲突时，保留批次设置。</p><p>逐通手动选择优先。表格始终显示最终生效的声音。</p></>;
    case 'names': return <><p>先按 XML 内完全一致的通话标识和角色配对，再校验两份文件的数字前缀及末位 0／1。角色以 XML 内容为准。</p><code className={styles.example}>202610270080.xml<br/>＋ 202610270081.xml<br/>→ 20261027008.mp3</code><p>保留前导零。已有文件不覆盖、不自动编号；只有核验一致的结果才跳过。</p></>;
  }
}
export function AppDialogs(props: Props) {
  return <Dialog title={{settings:'应用设置',match:'称呼匹配',names:'固定文件名',sources:'源文件',finish:'结束任务并清理记录',audition:'试听声音'}[props.modal]} compact={props.modal === 'settings'} onClose={props.onClose}><DialogContent props={props}/></Dialog>;
}
