import {useEffect, useRef, useState} from 'react';
import type {PreviewRow, WorkspaceItem} from '../types/contracts';
import type {WorkspaceModel} from './useWorkspace';
import {Icon} from './Icon';
import {AudioPlayer} from './AudioPlayer';
import {usePlaybackFollow} from './usePlaybackFollow';
import {CallProgress} from './CallProgress';
import {itemTitle, shortVoiceName, statusLabels, voiceSource} from './format';
import {guidanceForItem} from './issues';
import styles from './App.module.css';
interface Props {model: WorkspaceModel; active: boolean; onSources: () => void; onMatchHelp: () => void}
function timestamp(value?: number) {
  if (!Number.isFinite(value)) return '';
  const seconds = Math.max(0, value!) / 1000;
  return Math.floor(seconds / 60).toString().padStart(2, '0') + ':' + (seconds % 60).toFixed(3).padStart(6, '0');
}
function ReaderHead({props,current,onEvidence,canLocate}: {props: Props; current?: WorkspaceItem; onEvidence: () => void; canLocate: boolean}) {
  const {model,onSources}=props;
  const source=voiceSource(model.detail?.voice_choice?.reason || current?.voice_source || 'default');
  const voice = shortVoiceName(model.config?.voices.find(item => item.id === (model.detail?.voice_choice?.user || current?.customer_voice))?.label);
  return <header className={styles.readerHead}><div><h2>{current ? itemTitle(current) : '选择一通对话'}</h2>{current && <p className={styles.readerMeta}><span>客户 {voice}</span>{source==='称呼匹配'?<button className={styles.evidenceButton} aria-label={canLocate?'定位称呼匹配依据':'查看称呼匹配规则'} onClick={canLocate?onEvidence:props.onMatchHelp}>称呼匹配<Icon name={canLocate?'arrow':'info'}/></button>:<span>{source}</span>}<span>{statusLabels[current.status]}</span></p>}{current&&['running','saving'].includes(current.status)&&<CallProgress item={current} progress={model.workspace?.active_item} pausing={model.workspace?.status==='pausing'}/>}</div><div className={styles.readerActions}>{current && <button className={styles.iconButton} onClick={onSources} aria-label="查看通话信息"><Icon name="info"/></button>}</div></header>;
}
function TranscriptLine({row,highlight=false,playing=false,onSeek}: {row: PreviewRow; highlight?: boolean; playing?:boolean;onSeek?:()=>void}) {
  return <div data-row-id={row.id} tabIndex={-1} className={`${styles.transcript} ${highlight?styles.evidenceRow:''} ${playing?styles.playingRow:''}`}><span className={`${styles.role} ${row.speaker === 'USER' ? styles.userRole : ''}`}>{row.speaker === 'AGENT' ? '客服' : '客户'}</span><div>{row.start_ms !== undefined && <span className={styles.sourceTime}>源时间 {timestamp(row.start_ms)}{row.end_ms !== undefined ? ' – ' + timestamp(row.end_ms) : ''}</span>}<p>{onSeek?<button className={styles.transcriptSeek} aria-label={'跳播第 '+row.id+' 句：'+row.text} aria-current={playing?'true':undefined} onClick={()=>{if(!window.getSelection()?.toString())onSeek();}}>{row.text}</button>:row.text}</p></div></div>;
}
function ReaderContent({props,current,evidenceId,playback}: {props: Props; current?: WorkspaceItem; evidenceId?: number;playback:ReturnType<typeof usePlaybackFollow>}) {
  const {model}=props;
  if (model.detailLoading) return <p className={styles.quiet} role="status">正在读取正文…</p>;
  if (current?.error && !current.has_audio) return <div className={styles.readerError}><Icon name="warning"/><p>{current.error}</p><span>{guidanceForItem(current)}</span></div>;
  const rows = model.detail?.rows.filter(row => !row.muted) || [];
  if (!rows.length) return <p className={styles.quiet}>{current ? '此通对话暂无可显示正文。' : '点击通话查看正文和声音。'}</p>;
  return <>{rows.map(row => <TranscriptLine row={row} highlight={row.id===evidenceId} playing={row.id===playback.rowId} onSeek={playback.timeline?.available?()=>playback.seek(row.id):undefined} key={row.id}/>)}</>;
}
function ReaderNotes({model,current}: {model: WorkspaceModel;current?:WorkspaceItem}) {
  const detail = model.detail;
  const information=new Set(detail?.info_notes || []);
  return <>{current?.has_audio&&current.error&&<p className={styles.warningText}>{current.error}</p>}{detail?.result_settings_changed && <p className={styles.warningText}>已有结果对应先前设置；当前正文预览使用新设置。</p>}{!!detail?.muted_count && <span className={styles.mutedNote}>跳过 {detail.muted_count} 段静音</span>}{Array.from(new Set(detail?.warnings)).filter(warning=>!information.has(warning)).map(warning => <p className={styles.warningText} key={warning}>{warning}</p>)}</>;
}
export function Reader(props: Props) {
  const {model,active}=props;
  const current = model.items.find(item => item.id === model.activeId) || model.detail?.item;
  const playback=usePlaybackFollow(model,current,active);
  const scroll = playback.scroll;
  const positions = useRef(new Map<string, number>());
  const [evidence,setEvidence]=useState<{itemId:string;rowId:number}|null>(null);
  const evidenceId=model.detail?.voice_choice?.evidence_row_id;
  const canLocate=typeof evidenceId==='number' && !!model.detail?.rows.some(row=>row.id===evidenceId&&row.speaker==='AGENT'&&!row.muted);
  function locateEvidence(){playback.suspend();if(!canLocate||!model.activeId)return;setEvidence({itemId:model.activeId,rowId:evidenceId!});const target=scroll.current?.querySelector<HTMLElement>('[data-row-id="'+evidenceId+'"]');target?.scrollIntoView({block:'center',behavior:'auto'});target?.focus({preventScroll:true});}
  useEffect(() => {if (scroll.current && model.activeId) scroll.current.scrollTop = positions.current.get(model.activeId) || 0;}, [model.activeId,scroll]);
  const canPlay = !!current?.has_audio;
  return <section className={styles.reader} aria-label="完整核对"><ReaderHead props={props} current={current} onEvidence={locateEvidence} canLocate={canLocate}/><div className={styles.readerBody} ref={scroll} onWheel={playback.suspend} onTouchMove={playback.suspend} onPointerDown={event=>{if(event.target===event.currentTarget)playback.suspend();}} onKeyDown={event=>{if(['ArrowUp','ArrowDown','PageUp','PageDown','Home','End'].includes(event.key))playback.suspend();}} onScroll={event => {if (model.activeId) positions.current.set(model.activeId,event.currentTarget.scrollTop);}}><ReaderNotes model={model} current={current}/><ReaderContent props={props} current={current} evidenceId={evidence?.itemId===model.activeId?evidence.rowId:undefined} playback={playback}/></div>{canPlay && model.workspace && current && <div className={styles.player}>{playback.notice&&<span className={styles.playbackNotice} role="status">{playback.notice}</span>}{playback.timeline?.available&&!playback.following&&<button className={styles.textButton} onClick={playback.resume}>回到正在播放</button>}<AudioPlayer ref={playback.audio} onPosition={playback.update} onFailure={playback.fail} key={current.id+':'+(model.detail?.fingerprint||'')} src={model.api.workspaceAudio(model.workspace.id,current.id)} label={itemTitle(current)} active={active}/></div>}</section>;
}
