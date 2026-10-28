import {useEffect,useRef,useState} from 'react';
import type {PlaybackTimeline, WorkspaceItem} from '../types/contracts';
import type {WorkspaceModel} from './useWorkspace';
import type {AudioPlayerHandle} from './AudioPlayer';

export function activeSegment(timeline:PlaybackTimeline|null, position:number) {
  if (!timeline?.available) return null;
  const rows=timeline.segments;
  let low=0,high=rows.length-1,found=-1;
  while(low<=high){const middle=(low+high)>>1;if(rows[middle].start_ms<=position*1000){found=middle;low=middle+1;}else high=middle-1;}
  return found<0?null:rows[found].id;
}

export function usePlaybackFollow(model:WorkspaceModel,current:WorkspaceItem|undefined,active:boolean) {
  const [loaded,setLoaded]=useState<{key:string;value:PlaybackTimeline}|null>(null);
  const [playback,setPlayback]=useState({key:'',position:0,following:true,failed:false});
  const audio=useRef<AudioPlayerHandle>(null);
  const scroll=useRef<HTMLDivElement>(null);
  const workspaceId=model.workspace?.id;
  const itemId=current?.id;
  const key=(workspaceId||'')+':'+(current?.id||'')+':'+(model.detail?.fingerprint||'');
  const ready=!!current?.has_audio && !!model.detail && model.detail.item.id===current.id;
  const fetchTimeline=model.api.getWorkspaceTimeline;
  useEffect(()=>{
    if(!ready||!active||!workspaceId||!itemId)return;
    let alive=true;
    void fetchTimeline.call(model.api,workspaceId,itemId).then(value=>{if(alive)setLoaded({key,value});}).catch(()=>{if(alive)setLoaded({key,value:{available:false,reason:'unavailable'}});});
    return ()=>{alive=false;};
  },[key,ready,active,model.api,fetchTimeline,workspaceId,itemId]);
  const timeline=loaded?.key===key?loaded.value:null;
  const state=playback.key===key?playback:{key,position:0,following:true,failed:false};
  const usable=timeline?.available&&!state.failed?timeline:null;
  const rowId=activeSegment(usable,state.position);
  function reveal(){
    const container=scroll.current;
    const target=rowId===null?null:container?.querySelector<HTMLElement>(`[data-row-id="${rowId}"]`);
    if(!container||!target)return;
    const outer=container.getBoundingClientRect(),inner=target.getBoundingClientRect();
    if(inner.top<outer.top||inner.bottom>outer.bottom){
      container.scrollTo({top:container.scrollTop+inner.top-outer.top-24,behavior:window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
    }
  }
  useEffect(()=>{if(active&&state.following)reveal();},[rowId,active,state.following]);
  function update(position:number){setPlayback(previous=>previous.key===key?{...previous,position}:{key,position,following:true,failed:false});}
  function suspend(){setPlayback({...state,following:false});}
  function resume(){setPlayback({...state,following:true});reveal();}
  function seek(id:number){if(!usable?.available)return;const row=usable.segments.find(segment=>segment.id===id);if(row){audio.current?.seek(row.start_ms/1000);setPlayback({...state,position:row.start_ms/1000,following:true});}}
  function fail(){setPlayback({...state,failed:true});}
  return {audio,scroll,timeline:usable,rowId,following:state.following,update,suspend,resume,seek,fail,
    notice:!ready?'':state.failed?'音频暂不可用，播放跟随已停止。':!timeline?'正在读取播放定位…':!timeline.available?timeline.reason==='missing'?'此结果没有播放定位信息':timeline.reason==='mismatch'?'音频与当前正文不匹配，无法跟随。':'播放定位暂不可用，请核对目录访问权限。':''};
}
