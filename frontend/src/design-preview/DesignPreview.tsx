import {useEffect, useState} from 'react';
import {Icon} from '../app/Icon';
import {Dialog, SettingsDrawer} from '../app/Dialog';
import {VoiceSelect} from '../app/VoiceSelect';
import {WorkspaceProgress} from '../app/WorkspaceProgress';
import brand from '../../../assets/dialogue.png';
import {calls, scanWorkspace, voices} from './fixtures';
import styles from './DesignPreview.module.css';

type Scene = 'overview' | 'review' | 'scan' | 'issues';
export function DesignPreview() {
  const [variant,setVariant]=useState<'A'|'B'>(new URLSearchParams(location.search).get('variant')==='B'?'B':'A');
  const [theme,setTheme]=useState<'light'|'dark'>('light');
  const [scene,setScene]=useState<Scene>('overview');
  const [selected,setSelected]=useState<string[]>(calls.slice(0,3).map(c=>c.id));
  const [current,setCurrent]=useState(calls[0].id);
  const [query,setQuery]=useState('');
  const [filter,setFilter]=useState('all');
  const [settingsOpen,setSettingsOpen]=useState(false);
  const [modal,setModal]=useState<'settings'|'rules'|'import'|null>(null);
  const [scope,setScope]=useState('batch');
  const [agent,setAgent]=useState('zh-CN-XiaoxiaoNeural');
  const [customer,setCustomer]=useState('zh-CN-XiaoyiNeural');
  const [overrides,setOverrides]=useState<Record<string,string>>({});
  const [rate,setRate]=useState('1.00');
  const [pause,setPause]=useState('250');
  const [autoVoice,setAutoVoice]=useState(true);
  const [scanPhase,setScanPhase]=useState('reading');
  useEffect(()=>{document.documentElement.dataset.designPreview='true';document.documentElement.dataset.theme=theme;return()=>{delete document.documentElement.dataset.designPreview;};},[theme]);
  const active=calls.find(c=>c.id===current)!;
  const visible=calls.filter(c=>c.id.includes(query)&&(scene==='issues'?c.status==='error':filter==='all'||filter==='selected'&&selected.includes(c.id)||c.status===filter));
  const ready=calls.filter(c=>c.status==='ready');
  const voiceFor=(call:typeof calls[number])=>voices.find(v=>v.id===(overrides[call.id]||(autoVoice&&call.source==='称呼匹配'?'zh-CN-YunxiNeural':customer)))?.label.split(' · ')[0];
  const toggle=(id:string)=>setSelected(value=>value.includes(id)?value.filter(v=>v!==id):[...value,id]);
  const changeScene=(next:Scene)=>{setScene(next);setFilter('all');};
  const showCall=(id:string)=>{setCurrent(id);setScope('call');};

  const soundSettings=<div className={styles.settingsContent}>
    <header className={styles.panelHead}><h2>声音与输出</h2><button aria-label="关闭声音与输出" className={styles.iconButton} onClick={()=>setSettingsOpen(false)}><Icon name="close"/></button></header>
    <div className={styles.segmented} aria-label="设置范围"><button aria-pressed={scope==='batch'} onClick={()=>setScope('batch')}>整个批次</button><button aria-pressed={scope==='call'} onClick={()=>setScope('call')}>当前通话</button></div>
    <div className={styles.settingsScroll}>
      {scope==='call'&&<div className={styles.scopeInfo}><Icon name="file"/><span>{current}.mp3</span></div>}
      <section className={styles.settingGroup}><h3>声音</h3>{scope==='batch'&&<label className={styles.field}><span>客服</span><VoiceSelect id="preview-agent" label="客服声音" value={agent} voices={voices} disabled={false} onChange={setAgent}/></label>}<label className={styles.field}><span>客户</span><VoiceSelect id="preview-customer" label="客户声音" value={scope==='call'?overrides[current]:customer} voices={voices} disabled={false} inherited={scope==='call'} onChange={value=>scope==='call'?setOverrides(previous=>({...previous,[current]:value})):setCustomer(value)}/></label><div className={styles.toggleRow}><span>按称呼匹配</span><button className={styles.switch} role="switch" aria-label="按称呼匹配" aria-checked={autoVoice} onClick={()=>setAutoVoice(!autoVoice)}><span/></button></div></section>
      <section className={styles.settingGroup}><h3>节奏</h3><label className={styles.field}><span>语速 <output>{Number(rate).toFixed(2)}×</output></span><input aria-label="语速" type="range" min="0.5" max="2" step="0.05" value={rate} onChange={e=>setRate(e.target.value)}/></label><label className={styles.field}><span>段间停顿 <output>{pause} ms</output></span><input aria-label="段间停顿" type="range" min="0" max="3000" step="50" value={pause} onChange={e=>setPause(e.target.value)}/></label></section>
      <section className={styles.settingGroup}><h3>输出文件夹</h3><button className={styles.folder} onClick={()=>setModal('rules')}><Icon name="folder"/><span>配音结果</span><Icon name="chevron"/></button><button className={styles.textButton} onClick={()=>setModal('rules')}>保存与命名规则 <Icon name="info"/></button></section>
    </div><div className={styles.settingsEnd}><Icon name="check"/><span>设置仅用于此交互样例</span></div>
  </div>;

  const reader=<div className={styles.reader}><header className={styles.readerHead}><div><span className={styles.eyebrow}>当前通话</span><h2>{current}.mp3</h2></div><button className={styles.iconButton} aria-label="查看来源信息" onClick={()=>setModal('rules')}><Icon name="info"/></button></header><div className={styles.readerMeta}><span>{active.segments} 段</span><span>客户 · {voiceFor(active)}</span><span className={styles.badge}>虚构正文</span></div><div className={styles.transcript}>{[['客服','您好，这里是预约服务中心。请问有什么可以帮您？'],['客户','您好，我想预约明天下午的会议室，大约需要一个小时。'],['客服','好的，先生。明天下午两点到三点的会议室可以预约，您看方便吗？'],['客户','可以的，麻烦帮我预留一下。'],['客服','已经为您记录。请提前十分钟到场，有变更可以随时联系我们。'],['客户','好的，谢谢。']].map(([role,text],i)=><div key={i} className={styles.line}><span className={role==='客服'?styles.agent:styles.customer}>{role}</span><div><time>00:{String(i*4).padStart(2,'0')}.000</time><p>{text}</p></div></div>)}</div><footer className={styles.readerFoot}><Icon name="info"/>此样例不调用语音服务</footer></div>;

  return <div className={styles.preview}><a className={styles.skipLink} href="#preview-main">跳到批次内容</a>
    <div className={styles.previewBar}><div className={styles.previewIdentity}><strong>界面方案比较</strong><span>虚构数据 · 不连接服务</span></div><div className={styles.segmented} aria-label="布局方案"><button aria-pressed={variant==='A'} onClick={()=>setVariant('A')}>A 三栏工作台</button><button aria-pressed={variant==='B'} onClick={()=>setVariant('B')}>B 内容优先</button></div><select aria-label="样例场景" value={scene} onChange={e=>changeScene(e.target.value as Scene)}><option value="overview">批次总览</option><option value="review">正文核对</option><option value="scan">扫描阶段</option><option value="issues">需处理</option></select><button className={styles.iconButton} aria-label="切换主题" onClick={()=>setTheme(theme==='light'?'dark':'light')}><Icon name={theme==='light'?'moon':'sun'}/></button></div>
    <div className={`${styles.shell} ${variant==='B'?styles.contentFirst:styles.threeColumn}`}>
      <aside className={styles.sidebar}><div className={styles.brand}><img src={brand} alt="对白" width={32} height={32}/><strong>对白</strong></div><button className={styles.newBatch} aria-label="新建批次" onClick={()=>setModal('import')}><Icon name="plus"/><span>新建批次</span></button><div className={styles.sidebarLabel}>本机批次</div><button className={styles.batchNav} aria-current="page" onClick={()=>changeScene('overview')}><Icon name="folder"/><span><strong>预约回访 · 十月</strong><small>12 通 · 2 通需处理</small></span></button><div className={styles.sidebarBottom}><button onClick={()=>setModal('settings')}><Icon name="settings"/><span>应用设置</span></button><span className={styles.localLabel}>本机工作台</span></div></aside>
      <main id="preview-main" tabIndex={-1} className={styles.main}>
        <header className={styles.mainHead}><div>{variant==='B'&&<span className={styles.eyebrow}>当前批次</span>}<h1>预约回访 · 十月 <span>12 通</span></h1></div><div className={styles.headActions}><button className={styles.secondary} aria-expanded={settingsOpen} onClick={()=>setSettingsOpen(true)}><Icon name="settings"/><span>声音与输出</span></button><button className={styles.iconButton} aria-label="批次操作" onClick={()=>setModal('rules')}><Icon name="more"/></button></div></header>
        {scene==='scan'&&<div className={styles.scan}><WorkspaceProgress workspace={{...scanWorkspace,phase:scanPhase}}/><div className={styles.scanPhases} aria-label="演示扫描阶段">{[['enumerating','发现'],['reading','读取'],['pairing','配对'],['validating','核验']].map(([phase,label])=><button key={phase} aria-pressed={scanPhase===phase} onClick={()=>setScanPhase(phase)}>{label}</button>)}</div></div>}
        {scene==='issues'&&<div className={styles.issueBanner}><Icon name="warning"/><span><strong>2 通需要处理</strong> · 已有文件冲突与缺失声道</span><button onClick={()=>setModal('rules')}>查看处理方式<Icon name="arrow"/></button></div>}
        <div className={styles.workspaceSurface}>
          <div className={styles.toolbar}><div className={styles.filters} aria-label="通话状态筛选">{[['all','全部',12],['ready','待生成',8],['error','需处理',2],['saved','已保存',2]].map(([key,label,n])=><button key={key} aria-pressed={(scene==='issues'?'error':filter)===key} onClick={()=>{setFilter(String(key));if(scene==='issues')setScene('overview');}}>{label}<span>{n}</span></button>)}</div><div className={styles.segmented} aria-label="工作方式"><button aria-pressed={scene!=='review'} onClick={()=>changeScene('overview')}><Icon name="selected"/><span>总览</span></button><button aria-pressed={scene==='review'} onClick={()=>changeScene('review')}><Icon name="file"/><span>核对</span></button></div></div>
          <div className={styles.selectionBar}><label className={styles.search}><Icon name="search"/><input type="search" name="call-number" autoComplete="off" spellCheck={false} aria-label="搜索通话编号" placeholder="搜索通话编号" value={query} onChange={e=>setQuery(e.target.value)}/>{query&&<button className={styles.iconButton} aria-label="清除搜索" onClick={()=>setQuery('')}><Icon name="close"/></button>}</label><span>已选 <strong>{selected.length}</strong> 通</span><button className={styles.iconButton} aria-label="全选可生成通话" onClick={()=>setSelected(ready.map(c=>c.id))}><Icon name="selectAll"/></button><button className={styles.iconButton} aria-label="查看已选" aria-pressed={filter==='selected'} onClick={()=>setFilter(filter==='selected'?'all':'selected')}><Icon name="selected"/></button><button className={styles.iconButton} aria-label="清空选择" onClick={()=>setSelected([])}><Icon name="clearSelection"/></button></div>
          <div className={`${styles.workArea} ${scene==='review'?styles.review:''}`}><div className={styles.list}><div className={styles.tableScroll}><table aria-label="虚构通话列表"><thead><tr><th><input type="checkbox" aria-label="选择当前页" checked={selected.length===ready.length} onChange={()=>setSelected(selected.length===ready.length?[]:ready.map(c=>c.id))}/></th><th>通话编号</th><th>段数</th><th>客户声音</th><th>状态</th><th/></tr></thead><tbody>{visible.map(c=><tr key={c.id} className={current===c.id?styles.activeRow:''}><td><input type="checkbox" aria-label={`选择 ${c.id}`} checked={selected.includes(c.id)} disabled={c.status!=='ready'} onChange={()=>toggle(c.id)}/></td><td><button className={styles.filename} onClick={()=>showCall(c.id)}>{c.id}.mp3</button>{c.status==='error'&&<small className={styles.errorText}>{c.error}</small>}</td><td>{c.segments}</td><td>{voiceFor(c)}<small>{overrides[c.id]?'手动选择':autoVoice&&c.source==='称呼匹配'?'称呼匹配':'批次设置'}</small></td><td><span className={c.status==='error'?styles.warning:c.status==='saved'?styles.saved:styles.ready}><Icon name={c.status==='error'?'warning':c.status==='saved'?'check':'file'}/>{c.status==='error'?'需处理':c.status==='saved'?'已保存':'待生成'}</span></td><td><button className={styles.iconButton} aria-label={`核对 ${c.id}`} onClick={()=>{showCall(c.id);setScene('review');}}><Icon name="arrow"/></button></td></tr>)}</tbody></table>{!visible.length&&<div className={styles.empty}><Icon name="search"/><strong>没有匹配的通话</strong><button className={styles.textButton} onClick={()=>{setQuery('');setFilter('all');setScene('overview');}}>清除筛选</button></div>}</div><div className={styles.listFoot}><span>{visible.length} 通</span><span>每页 100 通</span></div></div>{scene==='review'&&reader}</div>
          {scene!=='review'&&<div className={styles.quickPreview}><div><Icon name="file"/><strong>{current}.mp3</strong><span>{active.segments} 段</span><button className={styles.textButton} onClick={()=>setScene('review')}>完整核对<Icon name="arrow"/></button></div><p><span>客服</span>您好，这里是预约服务中心。请问有什么可以帮您？</p></div>}
        </div>
        <footer className={styles.footer}><div><span className={styles.statusDot}/><strong>{scene==='scan'?'正在扫描':'准备就绪'}</strong><span>{scene==='scan'?'完成扫描后可生成':'已保存 2 通'}</span></div>{scene==='scan'&&<button className={styles.secondary} onClick={()=>setScene('overview')}>停止扫描演示</button>}<button className={styles.primary} disabled={!selected.length||scene==='scan'} onClick={()=>setModal('rules')}><Icon name="play"/>生成 {selected.length} 通</button></footer>
      </main>
      {variant==='A'&&!settingsOpen&&<aside className={styles.settings}>{soundSettings}</aside>}
    </div>
    {settingsOpen&&<SettingsDrawer onClose={()=>setSettingsOpen(false)}>{soundSettings}</SettingsDrawer>}
    {modal&&<Dialog title={modal==='settings'?'应用设置':modal==='import'?'新建批次':'交互样例说明'} onClose={()=>setModal(null)}><div className={styles.modalContent}>{modal==='settings'?<><label>外观<select aria-label="外观" value={theme} onChange={e=>setTheme(e.target.value as typeof theme)}><option value="light">浅色</option><option value="dark">深色</option></select></label><div>版本<span>设计评审样例</span></div></>:<><p>此页面使用虚构批次与正文，仅用于比较布局和组件。选择、筛选、声音及主题变化保存在当前页面，不读取文件、不生成或保存音频。</p><p>正式应用继续采用固定数字文件名，不覆盖已有结果；保存成功由真实回执确认。</p></>}<button className={styles.primary} onClick={()=>setModal(null)}>知道了</button></div></Dialog>}
  </div>;
}
