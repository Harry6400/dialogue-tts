import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {expect, it, vi} from 'vitest';
import {TaskSwitcher} from '../app/TaskSwitcher';
import type {WorkspaceSummary} from '../types/contracts';

function workspace(id: string, name: string, status: WorkspaceSummary['status'], total: number): WorkspaceSummary {
  return {id, status, phase: status, revision: 1, discovered: total * 2, scanned: total * 2, total, ready: total, error: 0, saved: 0, selected: 0, processed: 0, input_folder: {folder_id: id + '-input', path: '/fictional/' + name, valid: true}, settings: {voices: {AGENT: 'zh-CN-XiaoxiaoNeural', USER: 'zh-CN-XiaoyiNeural'}, rate: 1, pause_ms: 250, auto_voice: true, manual_user_voice: false}};
}

it('opens a readable batch menu and restores focus after selecting another batch', async () => {
  const current = workspace('one', '会议室来源', 'ready', 60000);
  const other = workspace('two', '需要完整阅读的较长批次名称与中文会议室预约来源', 'interrupted', 128);
  const change = vi.fn();
  render(<TaskSwitcher workspaces={[current, other]} value={current.id} disabled={false} onChange={change}/>);
  const trigger = screen.getByRole('combobox', {name: '切换本机批次'});
  expect(trigger).toHaveTextContent('会议室来源');
  expect(trigger).toHaveTextContent('准备就绪');
  expect(trigger).toHaveTextContent('60,000 通');
  expect(trigger).not.toHaveAttribute('title');
  trigger.focus(); fireEvent.keyDown(trigger, {key: 'Enter'});
  expect(await screen.findByRole('listbox')).toBeVisible();
  expect(screen.getByText('切换批次')).toBeVisible();
  fireEvent.click(screen.getByRole('option', {name: /需要完整阅读的较长批次名称与中文会议室预约来源/}));
  expect(change).toHaveBeenCalledWith(other);
  await waitFor(() => expect(trigger).toHaveFocus());
});

it('blocks switching when the native task state locks the control', () => {
  const current = workspace('one', '扫描中的批次', 'scanning', 0);
  const change = vi.fn();
  render(<TaskSwitcher workspaces={[current]} value={current.id} disabled onChange={change}/>);
  const trigger = screen.getByRole('combobox', {name: '切换本机批次'});
  expect(trigger).toBeDisabled();
  fireEvent.keyDown(trigger, {key: 'Enter'});
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  expect(change).not.toHaveBeenCalled();
});
