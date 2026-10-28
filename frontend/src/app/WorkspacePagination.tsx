import {useState} from 'react';
import {Icon} from './Icon';
import {count} from './format';
import styles from './App.module.css';

interface Props {page:number;total:number;loading:boolean;onPage:(page:number)=>void;compact?:boolean}
function PageInput({page,pages,loading,onPage}: {page:number;pages:number;loading:boolean;onPage:(page:number)=>void}) {
  const [draft,setDraft]=useState(String(page));
  const [error,setError]=useState(false);
  function commit() {
    const next=Number(draft);
    if (!/^\d+$/.test(draft)||!Number.isSafeInteger(next)||next<1||next>pages) {setError(true);return;}
    setError(false); if(next!==page)onPage(next);
  }
  return <label className={styles.pageJump}><span className={styles.srOnly}>跳转页码</span><input aria-label="跳转页码" type="text" inputMode="numeric" autoComplete="off" value={draft} disabled={loading} aria-invalid={error||undefined} title={error?`请输入 1–${count(pages)} 之间的页码`:'输入页码，按 Enter 跳转'} onChange={event=>{setDraft(event.target.value);setError(false);}} onKeyDown={event=>{if(event.key==='Enter'){event.preventDefault();commit();}if(event.key==='Escape'){event.preventDefault();event.stopPropagation();setDraft(String(page));setError(false);}}}/><span>/ {count(pages)} 页</span>{error&&<span className={styles.pageError} role="alert">请输入 1–{count(pages)} 的页码</span>}</label>;
}
export function WorkspacePagination({page,total,loading,onPage,compact=false}:Props) {
  const pages=Math.max(1,Math.ceil(total/100));
  const start=total?Math.min(total,(page-1)*100+1):0;
  const end=Math.min(total,page*100);
  return <nav className={`${styles.pagination} ${compact?styles.compactPagination:''}`} aria-label="通话分页">
    <span className={styles.pageSummary}>{count(start)}–{count(end)} / {count(total)} 通{loading?' · 更新中':''}</span>
    <div className={styles.pageControls}>
      <button className={`${styles.iconButton} ${styles.pageEdge}`} aria-label="第一页" title="第一页" disabled={loading||page<=1} onClick={()=>onPage(1)}><Icon name="first"/></button>
      <button className={styles.iconButton} aria-label="上一页" title="上一页" disabled={loading||page<=1} onClick={()=>onPage(page-1)}><Icon name="back"/></button>
      <PageInput key={page+'-'+pages} page={page} pages={pages} loading={loading} onPage={onPage}/>
      <button className={styles.iconButton} aria-label="下一页" title="下一页" disabled={loading||page>=pages} onClick={()=>onPage(page+1)}><Icon name="arrow"/></button>
      <button className={`${styles.iconButton} ${styles.pageEdge}`} aria-label="最后一页" title="最后一页" disabled={loading||page>=pages} onClick={()=>onPage(pages)}><Icon name="last"/></button>
    </div>
  </nav>;
}
