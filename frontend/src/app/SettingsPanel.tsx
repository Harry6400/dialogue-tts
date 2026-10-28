import type {SoundSettings, VoiceId} from '../types/contracts';
import {canEdit, isWorking, type WorkspaceModel} from './useWorkspace';
import {Icon} from './Icon';
import {RangeSetting} from './RangeSetting';
import {VoiceSelect} from './VoiceSelect';
import {itemTitle, shortPath, shortVoiceName, voiceSource} from './format';
import styles from './App.module.css';
interface Props {model: WorkspaceModel; scope: 'batch' | 'call'; setScope: (value: 'batch' | 'call') => void; onClose: () => void; onHelp: (kind: 'match' | 'names') => void; onAudition: (settings: SoundSettings) => void; auditionBusy?: boolean}
function soundContext(props: Props) {
  const {model,scope,auditionBusy}=props;
  const workspace=model.workspace,settings=workspace?.settings;
  const current=model.items.find(item=>item.id===model.activeId)||model.detail?.item;
  const effectiveCustomer=model.detail?.voice_choice?.user||current?.customer_voice;
  const disabled=!!model.pending||!canEdit(workspace);
  const auditionDisabled=!settings||!!model.pending||isWorking(workspace)||!!auditionBusy;
  const auditionSettings=settings&&{...settings,voices:{...settings.voices,...(scope==='call'&&effectiveCustomer?{USER:effectiveCustomer}:{})}};
  async function update(patch: Partial<SoundSettings>) {
    if (!workspace||!settings) return;
    await model.mutate({kind: 'settings', label: '保存声音设置'},()=>model.api.setWorkspaceSettings(workspace.id,{settings:{...settings,...patch}}),true);
  }
  async function override(value:string,selected=false) {
    if (!workspace||(!selected&&!current)) return;
    await model.mutate({kind: 'settings', label: '保存客户声音'},()=>model.api.setWorkspaceSettings(workspace.id,{...(selected?{selection_id:workspace.selection_id}:{item_id:current!.id}),customer_voice:value?value as VoiceId:null}),true);
  }
  return {model,workspace,settings,current,effectiveCustomer,disabled,auditionDisabled,auditionSettings,update,override,props};
}
type Context=ReturnType<typeof soundContext>;
function VoicePicker({ctx,id,label,value,onChange,inherited=false,bulk=false,disabled=false}:{ctx:Context;id:string;label:string;value?:string;onChange:(value:string)=>void;inherited?:boolean;bulk?:boolean;disabled?:boolean}) {
  return <VoiceSelect id={id} label={label} value={value} voices={ctx.model.config?.voices||[]} disabled={ctx.disabled||disabled||!ctx.workspace} inherited={inherited} bulk={bulk} onChange={onChange}/>;
}

function AuditionButton({ctx,label}:{ctx:Context;label:string}) {
  return <button className={styles.auditionButton} aria-label={label} disabled={ctx.auditionDisabled} onClick={()=>ctx.auditionSettings&&ctx.props.onAudition(ctx.auditionSettings)}><Icon name="play"/><span>试听</span></button>;
}
function CallSettings({ctx}:{ctx:Context}) {
  const {model,current,settings,effectiveCustomer,override,props}=ctx;
  const manual=current?.voice_source==='手动选择';
  return <>
    <div className={styles.settingGroup}><span className={styles.scopeName}>{itemTitle(current)}</span><div className={styles.readonlyField}><span>客服</span><strong>{shortVoiceName(model.config?.voices.find(voice=>voice.id===settings?.voices.AGENT)?.label)}</strong></div><label htmlFor="call-voice" className={styles.fieldLabel}>客户声音</label><div className={styles.voiceInput}><VoicePicker ctx={ctx} id="call-voice" label="当前通话客户声音" value={manual?current?.customer_voice:''} onChange={value=>void override(value)} inherited disabled={current?.status!=='ready'}/><AuditionButton ctx={ctx} label="试听当前通话的有效声音"/></div>{current?.customer_voice&&<p className={styles.resolved}>{shortVoiceName(model.config?.voices.find(voice=>voice.id===effectiveCustomer)?.label)} · {voiceSource(model.detail?.voice_choice?.reason||current.voice_source||'default')}</p>}</div>
    <div className={styles.settingGroup}><div className={styles.readonlyField}><span>语速</span><strong>{(settings?.rate||1).toFixed(2)}×</strong></div><div className={styles.readonlyField}><span>段间停顿</span><strong>{settings?.pause_ms??250} ms</strong></div><button className={styles.textButton} onClick={()=>props.setScope('batch')}>调整批次设置 <Icon name="arrow"/></button></div>
  </>;
}
function BatchSettings({ctx}:{ctx:Context}) {
  const {settings,workspace,disabled,update,props}=ctx;
  return <>
    <div className={styles.settingGroup}>{(['AGENT','USER'] as const).map(role=><div className={styles.voiceField} key={role}><label htmlFor={'voice-'+role} className={styles.fieldLabel}>{role==='AGENT'?'客服声音':'客户声音'}</label><div className={styles.voiceInput}><VoicePicker ctx={ctx} id={'voice-'+role} label={role==='AGENT'?'客服声音':'客户声音'} value={settings?.voices[role]||(role==='AGENT'?'zh-CN-XiaoxiaoNeural':'zh-CN-XiaoyiNeural')} onChange={value=>void update({voices:{...settings?.voices,[role]:value as VoiceId}})}/><AuditionButton ctx={ctx} label={'试听'+(role==='AGENT'?'客服':'客户')+'声音'}/></div></div>)}<div className={styles.toggleRow}><span>按称呼匹配客户声音 <button className={styles.infoButton} aria-label="称呼匹配规则" onClick={()=>props.onHelp('match')}><Icon name="info"/></button></span><button className={styles.switch} role="switch" aria-checked={settings?.auto_voice??true} aria-label="按明确称呼匹配客户声音" disabled={disabled} onClick={()=>void update({auto_voice:!settings?.auto_voice})}><span/></button></div></div>
    <div className={styles.settingGroup}><RangeSetting key={workspace?.id+'-rate-'+settings?.rate} id="rate" label="语速" value={settings?.rate||1} min={0.5} max={2} step={0.05} unit="×" decimals={2} disabled={disabled} onCommit={value=>void update({rate:value})}/><RangeSetting key={workspace?.id+'-pause-'+settings?.pause_ms} id="pause" label="段间停顿" value={settings?.pause_ms??250} min={0} max={3000} step={50} unit="ms" disabled={disabled} onCommit={value=>void update({pause_ms:value})}/><button className={styles.textButton} disabled={disabled} onClick={()=>void update({rate:1,pause_ms:250})}>恢复默认</button></div>
  </>;
}
function OutputSettings({ctx}:{ctx:Context}) {
  const {model,workspace,props}=ctx;
  return <div className={styles.settingGroup}><span className={styles.fieldLabel}>批次输出目录</span><button data-testid="output-folder" className={styles.folderButton} disabled={!!model.pending||isWorking(workspace)} onClick={()=>void model.chooseOutput()}><Icon name="folder"/><span title={model.output?.path}>{shortPath(model.output?.path)}</span><span className={styles.folderAction}>更换</span></button><p className={styles.fieldHint}>整个批次均在每通生成后自动保存到此处。</p><div className={styles.metaAction}><span>文件名按 XML 编号</span><button className={styles.infoButton} aria-label="固定命名规则" onClick={()=>props.onHelp('names')}><Icon name="info"/></button></div>{model.output&&<button className={styles.textButton} onClick={()=>void model.perform({kind: 'reveal', label: '打开结果文件夹'},async()=>{if(model.native.current)model.nativeValue(await model.native.current.call('reveal_output',{folder_id:model.output!.folder_id}));})}>打开文件夹 <Icon name="arrow"/></button>}</div>;
}
function SelectionSettings({ctx}:{ctx:Context}) {
  const {workspace,override}=ctx;
  if (!workspace?.selected) return null;
  return <div className={styles.settingGroup}><label className={styles.fieldLabel} htmlFor="selected-voice">所选 {workspace.selected.toLocaleString('zh-CN')} 通客户声音</label><VoicePicker ctx={ctx} id="selected-voice" label="所选通话客户声音" onChange={value=>void override(value,true)} inherited bulk/><p className={styles.quiet}>统一选择声音，或在菜单中恢复跟随批次。</p></div>;
}
export function SettingsPanel(props:Props) {
  const ctx=soundContext(props);
  return <aside className={styles.settings} aria-label="声音与批次设置"><header className={styles.settingsHead}><h2>声音与输出</h2><button className={styles.iconButton} aria-label="收起设置" onClick={props.onClose}><Icon name="close"/></button></header><div className={styles.scopeTabs} aria-label="设置范围"><button aria-pressed={props.scope==='batch'} onClick={()=>props.setScope('batch')}>整个批次</button><button aria-pressed={props.scope==='call'} onClick={()=>props.setScope('call')} disabled={!ctx.current}>当前通话</button></div><div className={styles.settingsBody}><p className={styles.scopeHint}>{props.scope==='batch'?'设置批次默认声音，单通选择可覆盖。':'仅调整当前通话的客户声音。'}</p>{props.scope==='call'?<CallSettings ctx={ctx}/>:<BatchSettings ctx={ctx}/>}<OutputSettings ctx={ctx}/>{props.scope==='batch'&&<SelectionSettings ctx={ctx}/>} {ctx.disabled&&ctx.workspace&&<p className={styles.quiet}>{ctx.workspace.input_folder.valid===false?'恢复文件夹访问后可调整声音与输出。':ctx.workspace.has_run_plan?'当前生成计划已固定。结束任务后可调整。':'扫描或操作完成后可调整设置。'}</p>}</div></aside>;
}
