import {render, screen} from '@testing-library/react';
import {expect, it} from 'vitest';
import {WorkspaceProgress} from '../app/WorkspaceProgress';
import type {WorkspaceSummary} from '../types/contracts';

function summary(patch: Partial<WorkspaceSummary> = {}): WorkspaceSummary {
  return {id:'progress',status:'scanning',phase:'enumerating',revision:1,discovered:0,scanned:0,total:0,ready:0,error:0,saved:0,selected:0,ready_selected:0,processed:0,save_pending:0,issues:{total:0,pairing:0,input:0,changed:0,conflict:0,synthesis:0,save:0,other:0},selection_query:{q:'',filter:'all',issue:''},input_folder:{folder_id:'fictional',path:'/fictional'},settings:{voices:{},rate:1,pause_ms:250,auto_voice:true,manual_user_voice:false},...patch};
}

it('starts with unknown progress and follows real counts and phases without inventing a percentage', () => {
  const {rerender} = render(<WorkspaceProgress workspace={summary()}/>);
  expect(screen.getByRole('status')).toHaveTextContent('正在发现 XML 文件');
  const track = screen.getByRole('progressbar', {name:'扫描进度'});
  expect(track).not.toHaveAttribute('aria-valuenow');
  expect(track).toHaveAttribute('aria-valuetext','正在发现 XML 文件，已扫描 0 份文件');
  rerender(<WorkspaceProgress workspace={summary({phase:'reading',discovered:1200,scanned:1200})}/>);
  expect(track).toHaveAttribute('aria-valuetext','正在读取 XML，已扫描 1,200 份文件');
  expect(track).not.toHaveAttribute('aria-valuenow');
  for (const [phase, text] of [['pairing','正在准备通话配对'],['validating','正在配对与核验通话'],['settings','正在重算声音设置'],['checking','正在核验已有输出']]) {
    rerender(<WorkspaceProgress workspace={summary({phase})}/>);
    expect(screen.getByRole('status')).toHaveTextContent(text);
    expect(screen.getByRole('progressbar', {name:'扫描进度'})).toHaveAttribute('aria-valuetext', expect.stringContaining(text));
  }
});

it.each(['ready','cancelled','error','interrupted','done'] as const)('removes scan activity when scanning ends as %s', status => {
  const {rerender} = render(<WorkspaceProgress workspace={summary()}/>);
  rerender(<WorkspaceProgress workspace={summary({status,phase:status,message:'真实结果说明'})}/>);
  expect(screen.queryByRole('progressbar', {name:'扫描进度'})).not.toBeInTheDocument();
  expect(screen.getByText('真实结果说明')).toBeVisible();
});

it('keeps generation progress determinate and based on the existing processed count', () => {
  render(<WorkspaceProgress workspace={summary({status:'running',phase:'generating',total:50,processed:12,saved:10,error:2})}/>);
  const track = screen.getByRole('progressbar', {name:'生成进度'});
  expect(track).toHaveAttribute('max','50'); expect(track).toHaveAttribute('value','12');
  expect(screen.getByText('已保存 10 通 · 需处理 2 通')).toBeVisible();
});
