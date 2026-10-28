import {fireEvent, render, screen} from '@testing-library/react';
import {expect, it, vi} from 'vitest';
import {ApplicationSettings} from '../app/ApplicationSettings';
import type {WorkspaceModel} from '../app/useWorkspace';

function setup(overrides: {busy?: boolean; running?: boolean; nativeFailure?: string; noWorkspace?: boolean} = {}) {
  const call = vi.fn().mockResolvedValue({kind: 'success', value: {state: 'latest', current_version: '1.2.3', latest_version: '1.2.3'}});
  const model = {
    workspace: overrides.noWorkspace ? null : {input_folder: {path: '/fictional/source'}},
    config: {can_shutdown: true},
    native: {current: {call}},
    perform: vi.fn(async (_label: string, operation: () => Promise<void>) => {await operation();}),
    nativeValue: (result: {value: unknown}) => {
      if (overrides.nativeFailure) throw new Error(overrides.nativeFailure);
      return result.value;
    },
  } as unknown as WorkspaceModel;
  const props = {model, theme: 'light' as const, changeTheme: vi.fn().mockResolvedValue(undefined), onPauseExit: vi.fn().mockResolvedValue(undefined), onFinish: vi.fn(), onClose: vi.fn(), busy: overrides.busy ?? false, running: overrides.running ?? false};
  render(<ApplicationSettings {...props}/>);
  return {...props, call};
}

it('makes the selected appearance explicit and changes only a different choice', () => {
  const {changeTheme} = setup();
  expect(screen.getByRole('button', {name: '浅色'})).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(screen.getByRole('button', {name: '浅色'}));
  expect(changeTheme).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: '深色'}));
  expect(changeTheme).toHaveBeenCalledOnce();
});

it('separates preserving progress from the destructive confirmation entry', () => {
  const {onFinish, onPauseExit, onClose, call} = setup();
  expect(screen.getByText('清理本机任务记录，保留已保存的 MP3')).toBeVisible();
  fireEvent.click(screen.getByRole('button', {name: '结束并清理…'}));
  expect(onFinish).toHaveBeenCalledOnce();
  expect(onPauseExit).not.toHaveBeenCalled();
  expect(call).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: '保存并退出'}));
  expect(onClose).toHaveBeenCalledOnce();
  expect(onPauseExit).toHaveBeenCalledOnce();
});

it('shows only the native response version after a successful check', async () => {
  const {call} = setup();
  expect(screen.queryByText(/当前版本/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: '检查更新'}));
  expect(await screen.findByText('当前版本 1.2.3')).toBeVisible();
  expect(screen.getByRole('status')).toHaveTextContent('当前已是最新版本');
  expect(call).toHaveBeenCalledWith('check_for_update', {});
});

it('keeps update failure visible inside the settings content', async () => {
  setup({nativeFailure: '请先保存所有音频，再检查更新。'});
  fireEvent.click(screen.getByRole('button', {name: '检查更新'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('请先保存所有音频，再检查更新。');
  expect(screen.queryByRole('button', {name: '下载并安装更新'})).not.toBeInTheDocument();
});

it('retains running-task restrictions while preserving the exit request', () => {
  setup({running: true});
  expect(screen.getByRole('button', {name: '检查更新'})).toBeDisabled();
  expect(screen.getByRole('button', {name: '结束并清理…'})).toBeDisabled();
  expect(screen.getByRole('button', {name: '保存并退出'})).toBeEnabled();
});

it('requests native exit without marking the empty workspace as pending', () => {
  const {model, call} = setup({noWorkspace: true});
  fireEvent.click(screen.getByRole('button', {name: '退出对白'}));
  expect(call).toHaveBeenCalledWith('request_close', {});
  expect(model.perform).not.toHaveBeenCalled();
});
