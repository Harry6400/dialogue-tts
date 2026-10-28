import {useEffect,useRef,useState,type CSSProperties} from 'react';
import {Icon} from './Icon';
import styles from './App.module.css';
interface Props {src:string;label:string;active?:boolean}
function time(value:number) {const seconds=Math.max(0,Math.floor(value));return Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0');}
function useAudioPlayer(active:boolean) {
  const audio=useRef<HTMLAudioElement>(null);
  const [playing,setPlaying]=useState(false);
  const [duration,setDuration]=useState(0);
  const [position,setPosition]=useState(0);
  const [muted,setMuted]=useState(false);
  const [error,setError]=useState('');
  useEffect(()=>{if(!active)audio.current?.pause();},[active]);
  async function toggle() {
    const element=audio.current;if(!element||!active)return;
    if(!element.paused){element.pause();return;}
    setError('');
    try{await element.play();}catch{setError('音频暂时无法播放，请核对结果文件和目录访问权限。');}
  }
  function seek(value:string){const element=audio.current;if(!element)return;const next=Number(value);if(!Number.isFinite(next))return;element.currentTime=next;setPosition(next);}
  function readDuration(){const value=audio.current?.duration;if(value&&Number.isFinite(value))setDuration(value);}
  function toggleMute(){if(audio.current){audio.current.muted=!audio.current.muted;setMuted(audio.current.muted);}}
  return {audio,playing,duration,position,muted,error,setPlaying,setPosition,setError,toggle,seek,readDuration,toggleMute};
}
export function AudioPlayer({src,label,active=true}:Props) {
  const player=useAudioPlayer(active);
  const progress=player.duration?Math.min(100,player.position/player.duration*100):0;
  return <div className={styles.audioControl}>
    <audio ref={player.audio} src={src} preload={active?'metadata':'none'} aria-label={label} onPlay={()=>player.setPlaying(true)} onPause={()=>player.setPlaying(false)} onEnded={()=>player.setPlaying(false)} onLoadedMetadata={player.readDuration} onDurationChange={player.readDuration} onTimeUpdate={event=>player.setPosition(event.currentTarget.currentTime)} onError={()=>player.setError('音频暂时无法播放，请核对结果文件和目录访问权限。')}/>
    <button className={styles.audioPlay} disabled={!active} aria-label={(player.playing?'暂停 ':'播放 ')+label} onClick={()=>void player.toggle()}><Icon name={player.playing?'pause':'play'}/></button>
    <span className={styles.audioTime}>{time(player.position)}</span><input className={styles.audioSeek} type="range" aria-label={label+'播放位置'} min={0} max={player.duration||1} step={0.1} value={Math.min(player.position,player.duration||0)} disabled={!player.duration||!active} onChange={event=>player.seek(event.target.value)} style={{'--range-progress':progress+'%'} as CSSProperties}/><span className={styles.audioTime}>{time(player.duration)}</span>
    <button className={styles.iconButton} disabled={!active} aria-label={player.muted?'取消静音':'静音'} aria-pressed={player.muted} onClick={player.toggleMute}><Icon name={player.muted?'muted':'volume'}/></button>
    {player.error&&<span className={styles.audioError} role="alert">{player.error}</span>}
  </div>;
}
