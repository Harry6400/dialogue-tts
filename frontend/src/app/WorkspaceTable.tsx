import {useEffect, useRef} from 'react';
import {canEdit, type WorkspaceModel} from './useWorkspace';
import {Icon} from './Icon';
import styles from './App.module.css';

import {statusLabels, selectable, count, itemTitle, shortVoiceName, voiceSource} from './format';

function emptyCopy(model: WorkspaceModel) {
  if(model.loading)return {message:'正在读取通话…',hint:'',clear:false};
  if(model.workspace?.status==='scanning')return {message:'正在扫描文件夹，通话会逐步显示。',hint:'',clear:false};
  if(model.query)return {message:'没有符合条件的通话',hint:'可调整状态筛选，或尝试其他通话编号。',clear:true};
  if(model.filter==='running')return {message:'当前没有正在生成的通话',hint:'选好通话并开始生成后，进度会显示在这里。',clear:true};
  if(model.filter==='saved')return {message:'当前批次尚无已保存结果',hint:'每通生成并保存成功后，结果会显示在这里。',clear:true};
  if(model.filter==='error')return {message:'当前没有需要处理的问题',hint:'可返回全部通话继续查看。',clear:true};
  return {message:'没有符合条件的通话',hint:'可调整状态筛选，或尝试其他通话编号。',clear:true};
}
function EmptyList({model,onClearFilters}: {model:WorkspaceModel;onClearFilters:()=>void}) {
  const copy=emptyCopy(model);
  return <div className={styles.listEmpty}><Icon name={model.loading?'refresh':'search'}/><p>{copy.message}</p>{copy.hint&&<span className={styles.emptyHint}>{copy.hint}</span>}{copy.clear&&<button className={styles.textButton} onClick={onClearFilters}>清除筛选</button>}</div>;
}

export function WorkspaceTable({model, onReview, context = 'overview', onClearFilters}: {model: WorkspaceModel; onReview: () => void; context?: string; onClearFilters: () => void}) {
  const {items, workspace, pending, activeId, matchedTotal, page} = model;
  const checkbox = useRef<HTMLInputElement>(null);
  const eligible = items.filter(selectable);
  const checked = eligible.filter(item => item.selected).length;
  const allChecked = eligible.length > 0 && checked === eligible.length;
  useEffect(() => {if (checkbox.current) checkbox.current.indeterminate = checked > 0 && !allChecked;}, [checked, allChecked]);
  const voices = new Map(model.config?.voices.map(voice => [voice.id, voice.label]));
  const pages = Math.max(1, Math.ceil(matchedTotal / 100));
  function move(index: number, direction: number) {
    const next = items[Math.max(0, Math.min(items.length - 1, index + direction))];
    if (!next) return;
    model.setActiveId(next.id);
    const target = document.getElementById(context + '-call-' + next.id);
    target?.focus({preventScroll: true}); target?.scrollIntoView({block: 'nearest', behavior: 'auto'});
  }
  return <>
    <div className={styles.tableScroll} aria-busy={model.loading}>
      <table className={styles.callTable} aria-label="通话列表">
        <thead><tr><th className={styles.checkCell}><input ref={checkbox} type="checkbox" aria-label="选择当前页可生成通话" checked={allChecked} disabled={!eligible.length || !!pending || !canEdit(workspace)} onChange={event => void model.select({mode: 'page', item_ids: eligible.map(item => item.id), selected: event.target.checked})}/></th><th>输出文件</th><th className={styles.segmentColumn}>段数</th><th className={styles.voiceColumn}>客户声音</th><th>状态</th><th><span className={styles.srOnly}>操作</span></th></tr></thead>
        <tbody>{items.map((item, index) => <tr key={item.id} className={activeId === item.id ? styles.activeRow : ''}>
          <td className={styles.checkCell}><input type="checkbox" aria-label={'选择 ' + itemTitle(item)} checked={item.selected} disabled={!selectable(item) || !!pending || !canEdit(workspace)} onChange={event => void model.select({mode: 'toggle', item_id: item.id, selected: event.target.checked})}/></td>
          <td><button id={context + '-call-' + item.id} className={styles.fileButton} onClick={() => model.setActiveId(item.id)} onDoubleClick={onReview} onKeyDown={event => {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {event.preventDefault(); move(index, event.key === 'ArrowDown' ? 1 : -1);}
            if (event.key === 'Enter') {event.preventDefault(); model.setActiveId(item.id); onReview();}
          }} aria-pressed={activeId === item.id}>{itemTitle(item)}</button>{item.error && <span className={styles.rowError} title={item.error}>{item.error}</span>}</td>
          <td className={styles.segmentColumn}>{item.line_count===undefined?'—':count(item.line_count)}</td>
          <td className={styles.voiceColumn}><span>{shortVoiceName(item.customer_voice ? voices.get(item.customer_voice) : undefined)}</span>{item.voice_source && <span className={styles.cellSub} title={item.voice_source}>{voiceSource(item.voice_source)}</span>}</td>
          <td><span className={`${styles.status} ${['invalid', 'synthesis_error', 'save_error', 'conflict'].includes(item.status) ? styles.statusWarning : ''} ${item.status === 'saved' ? styles.statusSaved : ''}`}>{item.status === 'saved' && <Icon name="check"/>}{item.status === 'running' && <span className={styles.activityDot}/>} {statusLabels[item.status] || item.status}</span></td>
          <td><button className={styles.iconButton} aria-label={'核对 ' + (item.filename || item.id)} onClick={() => {model.setActiveId(item.id); onReview();}}><Icon name="arrow"/></button></td>
        </tr>)}</tbody>
      </table>
      {!items.length && <EmptyList model={model} onClearFilters={onClearFilters}/>}
    </div>
    <div className={styles.pagination}><span>{count(matchedTotal)} 通{model.loading ? ' · 更新中' : ''}</span><div><button className={styles.iconButton} aria-label="上一页" disabled={page <= 1 || model.loading} onClick={() => model.setPage(page - 1)}><Icon name="back"/></button><span>{page} / {pages}</span><button className={styles.iconButton} aria-label="下一页" disabled={page >= pages || model.loading} onClick={() => model.setPage(page + 1)}><Icon name="arrow"/></button></div></div>
  </>;
}
