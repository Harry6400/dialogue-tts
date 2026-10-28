import {fireEvent,render,screen} from '@testing-library/react';
import {expect,it,vi,afterEach} from 'vitest';
import {Reader} from '../app/Reader';
import type {WorkspaceModel} from '../app/useWorkspace';
import {activeSegment} from '../app/usePlaybackFollow';
import type {PlaybackTimeline} from '../types/contracts';
const timing:PlaybackTimeline={available:true,fingerprint:'same',audio_sha256:'verified',duration_ms:6000,segments:[{id:1,start_ms:0,end_ms:2000},{id:3,start_ms:2500,end_ms:6000}]};
function setup(timeline:PlaybackTimeline=timing) {
  const api={getWorkspaceTimeline:vi.fn().mockResolvedValue(timeline),workspaceAudio:()=>'/api/fixture/audio'};
  const item={id:'one',filename:'001.mp3',status:'saved',has_audio:true,selected:true};
  const model={api,items:[item],workspace:{id:'batch'},activeId:'one',config:{voices:[]},detailLoading:false,detail:{item,fingerprint:'same',rows:[{id:1,speaker:'AGENT',text:'虚构第一句'},{id:2,speaker:'USER',text:'不显示的静音',muted:true},{id:3,speaker:'USER',text:'虚构第二句'}]}} as unknown as WorkspaceModel;
  const props={model,active:true,onSources:vi.fn(),onMatchHelp:vi.fn()};
  const mounted=render(<Reader {...props}/>);
  return {...mounted,model,props,api};
}
afterEach(()=>vi.restoreAllMocks());
it('keeps the previous line active during a pause and handles boundaries',()=>{
  expect(activeSegment(timing,-1)).toBeNull();expect(activeSegment(timing,2.2)).toBe(1);expect(activeSegment(timing,2.5)).toBe(3);expect(activeSegment(timing,6)).toBe(3);
});
it('highlights playback, seeks by sentence, suspends follow on manual scrolling and resumes explicitly',async()=>{
  const pause=vi.spyOn(HTMLMediaElement.prototype,'pause').mockImplementation(()=>{});
  const {container,rerender,props,api}=setup();
  const second=await screen.findByRole('button',{name:'跳播第 3 句：虚构第二句'});
  expect(screen.queryByText('不显示的静音')).not.toBeInTheDocument();
  const audio=container.querySelector('audio')!;
  Object.defineProperty(audio,'duration',{value:6,configurable:true});fireEvent.loadedMetadata(audio);
  audio.currentTime=3;fireEvent.timeUpdate(audio);
  expect(second).toHaveAttribute('aria-current','true');
  const body=container.querySelector('[data-row-id="3"]')!.parentElement!;
  fireEvent.wheel(body);
  expect(screen.getByRole('button',{name:'回到正在播放'})).toBeVisible();
  fireEvent.click(screen.getByRole('button',{name:'跳播第 1 句：虚构第一句'}));
  expect(audio.currentTime).toBe(0);expect(screen.queryByRole('button',{name:'回到正在播放'})).not.toBeInTheDocument();
  rerender(<Reader {...props} model={{...props.model,workspace:{...props.model.workspace!,revision:2}}}/>);
  expect(api.getWorkspaceTimeline).toHaveBeenCalledOnce();
  rerender(<Reader {...props} active={false}/>);expect(pause).toHaveBeenCalled();
});
it('preserves a legacy audio player without inventing subtitle times',async()=>{
  const {container}=setup({available:false,reason:'missing'});
  expect(await screen.findByText('此结果没有播放定位信息')).toBeVisible();
  expect(container.querySelector('audio')).not.toBeNull();expect(screen.queryByRole('button',{name:/跳播第/})).not.toBeInTheDocument();
});
it('reports mismatched results and stops following when audio fails',async()=>{
  const {container}=setup();await screen.findByRole('button',{name:/跳播第 1/});
  fireEvent.error(container.querySelector('audio')!);
  expect(await screen.findByText('音频暂不可用，播放跟随已停止。')).toBeVisible();
  expect(screen.queryByRole('button',{name:/跳播第/})).not.toBeInTheDocument();
});

it('shows a complete save failure reason even when recoverable audio remains playable',async()=>{
  const {rerender,props}=setup();
  const error='保存失败：目标目录访问已经失效，请重新授权后重试。';
  rerender(<Reader {...props} model={{...props.model,items:[{...props.model.items[0],status:'save_error',error}]}}/>);
  expect(screen.getByText(error)).toBeVisible();
  expect(await screen.findByRole('button',{name:/跳播第 1/})).toBeVisible();
});
