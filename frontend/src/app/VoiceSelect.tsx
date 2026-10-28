import {useRef,useState} from 'react';
import * as Select from '@radix-ui/react-select';
import type {Voice} from '../types/contracts';
import {Icon} from './Icon';
import {shortVoiceName} from './format';
import styles from './App.module.css';
interface Props {id:string;label:string;value?:string;voices:Voice[];disabled:boolean;inherited?:boolean;bulk?:boolean;onChange:(value:string)=>void}
function voiceDescription(voice:Voice) {
  const parts=voice.label.split(/[·•]/).map(part=>part.trim());
  if(parts.length>1)return parts.slice(1).join(' · ');
  return voice.gender==='Female'?'普通话女声':'普通话男声';
}
function VoiceOption({voice}:{voice:Voice}) {
  return <Select.Item className={styles.selectItem} value={voice.id} textValue={shortVoiceName(voice.label)}><span className={styles.voiceOptionText}><Select.ItemText>{shortVoiceName(voice.label)}</Select.ItemText><small>{voiceDescription(voice)}</small></span><Select.ItemIndicator className={styles.selectIndicator}><Icon name="check"/></Select.ItemIndicator></Select.Item>;
}
export function VoiceSelect({id,label,value,voices,disabled,inherited=false,bulk=false,onChange}:Props) {
  const trigger=useRef<HTMLButtonElement>(null);
  const [container,setContainer]=useState<HTMLElement|null>(null);
  return <Select.Root value={bulk?'':value||'inherit'} disabled={disabled} onValueChange={next=>onChange(next==='inherit'?'':next)} onOpenChange={open=>{if(open)setContainer(trigger.current?.closest('dialog')||document.body);}}>
    <Select.Trigger ref={trigger} id={id} aria-label={label} className={styles.selectTrigger}><Select.Value placeholder={bulk?'批量设置声音':'选择声音'}/><Select.Icon><Icon name="chevron"/></Select.Icon></Select.Trigger>
    <Select.Portal container={container||undefined}><Select.Content className={styles.selectContent} position="popper" sideOffset={6} align="start" collisionPadding={12}><Select.Viewport className={styles.selectViewport}>
      {inherited&&<><Select.Item className={styles.selectItem} value="inherit" textValue={bulk?'恢复跟随批次':'跟随批次'}><Select.ItemText>{bulk?'恢复跟随批次':'跟随批次'}</Select.ItemText><Select.ItemIndicator className={styles.selectIndicator}><Icon name="check"/></Select.ItemIndicator></Select.Item><Select.Separator className={styles.selectSeparator}/></>}
      {(['Female','Male'] as const).map(gender=><Select.Group key={gender}><Select.Label className={styles.selectLabel}>{gender==='Female'?'女声':'男声'}</Select.Label>{voices.filter(voice=>voice.gender===gender).map(voice=><VoiceOption key={voice.id} voice={voice}/>)}</Select.Group>)}
    </Select.Viewport></Select.Content></Select.Portal>
  </Select.Root>;
}
