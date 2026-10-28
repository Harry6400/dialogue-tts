import {useEffect, useRef, useState} from 'react';
import type {PreviewRow, WorkspaceItem} from '../types/contracts';
import type {WorkspaceModel} from './useWorkspace';
import {Icon} from './Icon';
import {AudioPlayer} from './AudioPlayer';
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
  return <header className={styles.readerHead}><div><h2>{current ? itemTitle(current) : '选择一通对话'}</h2>{current && <p className={styles.readerMeta}><span>客户 {voice}</span>{source==='称呼匹配'?<button className={styles.evidenceButton} aria-label={canLocate?'定位称呼匹配依据':'查看称呼匹配规则'} onClick={canLocate?onEvidence:props.onMatchHelp}>称呼匹配<Icon name={canLocate?'arrow':'info'}/></button>:<span>{source}</span>}<span>{statusLabels[current.status]}</span></p>}</div><div className={styles.readerActions}>{current && <button className={styles.iconButton} onClick={onSources} aria-label="查看真实源文件"><Icon name="info"/></button>}</div></header>;
}
function TranscriptLine({row,highlight=false}: {row: PreviewRow; highlight?: boolean}) {
  return <div data-row-id={row.id} tabIndex={-1} className={`${styles.transcript} ${highlight?styles.evidenceRow:''}`}><span className={`${styles.role} ${row.speaker === 'USER' ? styles.userRole : ''}`}>{row.speaker === 'AGENT' ? '客服' : '客户'}</span><div>{row.start_ms !== undefined && <span className={styles.sourceTime}>源时间 {timestamp(row.start_ms)}{row.end_ms !== undefined ? ' – ' + timestamp(row.end_ms) : ''}</span>}<p>{row.text}</p></div></div>;
}
function ReaderContent({props,current,evidenceId}: {props: Props; current?: WorkspaceItem; evidenceId?: number}) {
  const {model}=props;
  if (model.detailLoading) return <p className={styles.quiet} role="status">正在读取正文…</p>;
  if (current?.error) return <div className={styles.readerError}><Icon name="warning"/><p>{current.error}</p><span>{guidanceForItem(current)}</span></div>;
  const rows = model.detail?.rows.filter(row => !row.muted) || [];
  if (!rows.length) return <p className={styles.quiet}>{current ? '此通对话暂无可显示正文。' : '点击通话查看正文和声音。'}</p>;
  return <>{rows.map(row => <TranscriptLine row={row} highlight={row.id===evidenceId} key={row.id}/>)}</>;
}
function ReaderNotes({model}: {model: WorkspaceModel}) {
  const detail = model.detail;
  return <>{detail?.result_settings_changed && <p className={styles.warningText}>已有结果对应先前设置；当前正文预览使用新设置。</p>}{!!detail?.muted_count && <p className={styles.quiet}>已跳过 {detail.muted_count} 段静音，不显示或发送静音原文。</p>}{Array.from(new Set(detail?.warnings)).map(warning => <p className={styles.warningText} key={warning}>{warning}</p>)}</>;
}
export function Reader(props: Props) {
  const {model,active}=props;
  const current = model.items.find(item => item.id === model.activeId) || model.detail?.item;
  const scroll = useRef<HTMLDivElement>(null);
  const positions = useRef(new Map<string, number>());
  const [evidence,setEvidence]=useState<{itemId:string;rowId:number}|null>(null);
  const evidenceId=model.detail?.voice_choice?.evidence_row_id;
  const canLocate=typeof evidenceId==='number' && !!model.detail?.rows.some(row=>row.id===evidenceId&&row.speaker==='AGENT'&&!row.muted);
  function locateEvidence(){if(!canLocate||!model.activeId)return;setEvidence({itemId:model.activeId,rowId:evidenceId!});const target=scroll.current?.querySelector<HTMLElement>('[data-row-id="'+evidenceId+'"]');target?.scrollIntoView({block:'center',behavior:'auto'});target?.focus({preventScroll:true});}
  useEffect(() => {if (scroll.current && model.activeId) scroll.current.scrollTop = positions.current.get(model.activeId) || 0;}, [model.activeId]);
  const canPlay = !!current?.has_audio;
  return <section className={styles.reader} aria-label="完整核对"><ReaderHead props={props} current={current} onEvidence={locateEvidence} canLocate={canLocate}/><div className={styles.readerBody} ref={scroll} onScroll={event => {if (model.activeId) positions.current.set(model.activeId,event.currentTarget.scrollTop);}}><ReaderNotes model={model}/><ReaderContent props={props} current={current} evidenceId={evidence?.itemId===model.activeId?evidence.rowId:undefined}/></div>{canPlay && model.workspace && current && <div className={styles.player}><AudioPlayer key={current.id} src={model.api.workspaceAudio(model.workspace.id,current.id)} label={itemTitle(current)} active={active}/></div>}</section>;
}
