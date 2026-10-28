import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {RangeSetting} from '../app/RangeSetting';
import {AudioPlayer} from '../app/AudioPlayer';
import {VoiceSelect} from '../app/VoiceSelect';
import type {Voice} from '../types/contracts';

afterEach(()=>vi.restoreAllMocks());

it('retains invalid numeric input when Tab is released over its slider',()=>{
  const commit=vi.fn();
  render(<RangeSetting id="speed" label="语速" value={1} min={0.5} max={2} step={0.05} unit="×" decimals={2} disabled={false} onCommit={commit}/>);
  const numeric=screen.getByRole('spinbutton',{name:'语速数值'});
  fireEvent.change(numeric,{target:{value:'2.5'}});fireEvent.blur(numeric);
  fireEvent.keyUp(screen.getByRole('slider',{name:'语速'}),{key:'Tab'});
  expect(numeric).toHaveValue(2.5);
  expect(numeric).toHaveAttribute('aria-invalid','true');
  expect(screen.getByRole('alert')).toHaveTextContent('0.5–2');
  expect(commit).not.toHaveBeenCalled();
});

it('commits valid slider navigation and precise numeric input',()=>{
  const commit=vi.fn();
  render(<RangeSetting id="speed" label="语速" value={1} min={0.5} max={2} step={0.05} unit="×" decimals={2} disabled={false} onCommit={commit}/>);
  const numeric=screen.getByRole('spinbutton',{name:'语速数值'});
  fireEvent.change(numeric,{target:{value:'1.23'}});fireEvent.blur(numeric);
  expect(commit).toHaveBeenLastCalledWith(1.23);
  const slider=screen.getByRole('slider',{name:'语速'});
  fireEvent.change(slider,{target:{value:'1.5'}});fireEvent.keyUp(slider,{key:'ArrowRight'});
  expect(commit).toHaveBeenLastCalledWith(1.5);
});

it('explicitly pauses the audio element when its reader becomes inactive',()=>{
  const pause=vi.spyOn(HTMLMediaElement.prototype,'pause').mockImplementation(()=>{});
  const mounted=render(<AudioPlayer src="/api/example/audio" label="测试结果" active/>);
  mounted.rerender(<AudioPlayer src="/api/example/audio" label="测试结果" active={false}/>);
  expect(pause).toHaveBeenCalledOnce();
  expect(screen.getByRole('button',{name:'播放 测试结果'})).toBeDisabled();
});

it('uses the accessible voice menu and returns focus after keyboard selection',async()=>{
  const voices:Voice[]=[{id:'zh-CN-XiaoxiaoNeural',label:'晓晓 · 普通话女声 · 温和',gender:'Female',locale:'zh-CN'},{id:'zh-CN-YunxiNeural',label:'云希 · 普通话男声 · 清晰',gender:'Male',locale:'zh-CN'}];
  const change=vi.fn();
  render(<VoiceSelect id="voice" label="客户声音" value={voices[0].id} voices={voices} disabled={false} onChange={change}/>);
  const trigger=screen.getByRole('combobox',{name:'客户声音'});trigger.focus();
  fireEvent.keyDown(trigger,{key:'Enter'});
  const option=await screen.findByRole('option',{name:/云希/});
  fireEvent.click(option);
  expect(change).toHaveBeenCalledWith('zh-CN-YunxiNeural');
  await waitFor(()=>expect(trigger).toHaveFocus());
});
