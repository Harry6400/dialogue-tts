import {render,screen} from '@testing-library/react';
import {expect,it} from 'vitest';
import {CallProgress} from '../app/CallProgress';
import type {WorkspaceActiveItem,WorkspaceItem} from '../types/contracts';
const item:WorkspaceItem={id:'call',filename:'001.mp3',status:'running',selected:true};
const progress:WorkspaceActiveItem={id:'call',phase:'waiting_audio',completed:0,total:29,segment:1,attempt:1,attempt_limit:5,connection_mode:'direct',phase_elapsed_ms:12000};
it('shows real waiting, segment completion, merge and saving without claiming a saved result',()=>{
  const {rerender}=render(<CallProgress item={item} progress={progress}/>);
  expect(screen.getByText('等待音频')).toBeVisible();expect(screen.getByTitle('等待音频 · 已等待 12秒')).toBeVisible();
  expect(screen.getByRole('progressbar')).toHaveAttribute('value','0');
  rerender(<CallProgress item={item} progress={{...progress,phase:'receiving',completed:7}}/>);
  expect(screen.getByRole('progressbar')).toHaveAttribute('value','7');expect(screen.getByText('7/29 段 · 直连')).toBeVisible();
  rerender(<CallProgress item={{...item,status:'saving'}} progress={{...progress,completed:29}}/>);
  expect(screen.getByText('正在保存')).toBeVisible();expect(screen.queryByText('已保存')).not.toBeInTheDocument();
  rerender(<CallProgress item={{...item,status:'saved'}} progress={progress}/>);
  expect(screen.getByText('已保存')).toBeVisible();expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
});
it('does not assign another call progress to a queued item',()=>{
  render(<CallProgress item={{...item,id:'other',status:'queued'}} progress={progress}/>);
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();expect(screen.getByText('排队中')).toBeVisible();
});

it('shows the current connection attempt and configured limit',()=>{
  const {rerender}=render(<CallProgress item={item} progress={{...progress,phase:'connecting',attempt:3}}/>);
  expect(screen.getByText('连接中 · 第 3/5 次')).toBeVisible();
  rerender(<CallProgress item={item} progress={{...progress,phase:'retrying',attempt:3}}/>);
  expect(screen.getByText('连接重试 · 已试 3/5 次')).toBeVisible();
  rerender(<CallProgress item={item} progress={{...progress,phase:'connecting',attempt:30,attempt_limit:30}}/>);
  expect(screen.getByText('连接中 · 第 30/30 次')).toBeVisible();
});

it('retains three progress slots through every active phase and a pause request',()=>{
  const {rerender}=render(<CallProgress item={item} progress={progress}/>);
  for(const phase of ['connecting','waiting_audio','receiving','retrying','merging','saving']){
    rerender(<CallProgress item={item} progress={{...progress,phase,completed:7}}/>);
    expect(screen.getByLabelText('001.mp3逐通进度').children).toHaveLength(3);
    expect(screen.getByText('7/29 段 · 直连')).toBeVisible();
  }
  rerender(<CallProgress item={item} progress={progress} pausing/>);
  expect(screen.getByText('本通结束后暂停')).toBeVisible();
  expect(screen.getByLabelText('001.mp3逐通进度').children).toHaveLength(3);
});
