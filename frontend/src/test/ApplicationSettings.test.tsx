import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {expect, it, vi} from 'vitest';
import {ApplicationSettings} from '../app/ApplicationSettings';
import type {WorkspaceModel} from '../app/useWorkspace';
import {useApplicationUpdate} from '../app/useApplicationUpdate';

function setup(overrides: {busy?: boolean; running?: boolean; nativeFailure?: string; noWorkspace?: boolean; noMacBuild?: boolean} = {}) {
  const call = vi.fn().mockResolvedValue({kind: 'success', value: {state: 'latest', current_version: '1.2.3', latest_version: '1.2.3'}});
  const model = {
    workspace: overrides.noWorkspace ? null : {input_folder: {path: '/fictional/source'}},
    config: {can_shutdown: true, app_version: '0.4.7', mac_build_number: overrides.noMacBuild ? null : '11', retry_attempts: 5},
    api: {setConnectionMode: vi.fn().mockResolvedValue({ok: true, connection_mode: 'system_proxy'}), setRetryAttempts: vi.fn().mockImplementation(async retry_attempts => ({ok: true, retry_attempts}))},
    setConfig: vi.fn(),
    native: {current: {call}},
    perform: vi.fn(async (_label: string, operation: () => Promise<void>) => {await operation();}),
    nativeValue: (result: {value: unknown}) => {
      if (overrides.nativeFailure) throw new Error(overrides.nativeFailure);
      return result.value;
    },
  } as unknown as WorkspaceModel;
  const props = {model, theme: 'light' as const, changeTheme: vi.fn().mockResolvedValue(undefined), onPauseExit: vi.fn().mockResolvedValue(undefined), onClose: vi.fn(), busy: overrides.busy ?? false, running: overrides.running ?? false};
  function Subject() {
    const updates = useApplicationUpdate(model, props.busy, props.running);
    return <ApplicationSettings {...props} updates={updates}/>;
  }
  render(<Subject/>);
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

it('preserves progress from application settings without duplicating batch cleanup', () => {
  const {onPauseExit, onClose, call} = setup();
  expect(screen.queryByRole('button', {name: '结束并清理…'})).not.toBeInTheDocument();
  expect(call).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: '保存并退出'}));
  expect(onClose).toHaveBeenCalledOnce();
  expect(onPauseExit).toHaveBeenCalledOnce();
});

it('shows the shipped version and Mac build without checking online', () => {
  const {call} = setup();
  expect(screen.getByText('当前版本 0.4.7 · Mac 构建 11')).toBeVisible();
  expect(call).not.toHaveBeenCalled();
});

it('does not display a Mac build for other platforms', () => {
  setup({noMacBuild: true});
  expect(screen.getByText('当前版本 0.4.7')).toBeVisible();
  expect(screen.queryByText(/Mac 构建/)).not.toBeInTheDocument();
});

it('reports a successful check without replacing the shipped version', async () => {
  const {call} = setup();
  fireEvent.click(screen.getByRole('button', {name: '检查更新'}));
  expect(await screen.findByRole('status')).toHaveTextContent('当前已是最新版本');
  expect(screen.getByText('当前版本 0.4.7 · Mac 构建 11')).toBeVisible();
  expect(call).toHaveBeenCalledWith('check_for_update', {});
});

it('displays checking feedback and waits before permitting another check', async () => {
  const {call} = setup();
  let finish!: (value: unknown) => void;
  call.mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
  fireEvent.click(screen.getByRole('button', {name: '检查更新'}));
  expect(screen.getByRole('status')).toHaveTextContent('正在检查正式版本…');
  expect(screen.getByRole('button', {name: '正在检查…'})).toBeDisabled();
  await act(async () => finish({kind: 'success', value: {state: 'latest', current_version: '0.4.7'}}));
  expect(screen.getByRole('button', {name: '检查更新'})).toBeEnabled();
  expect(call).toHaveBeenCalledOnce();
});

it('separates discovery from installation and explains cancellation', async () => {
  const {call} = setup();
  call.mockResolvedValueOnce({kind: 'success', value: {state: 'available', current_version: '0.4.7', latest_version: '0.4.8'}});
  fireEvent.click(screen.getByRole('button', {name: '检查更新'}));
  const install = await screen.findByRole('button', {name: '下载并安装更新'});
  expect(screen.getByRole('status')).toHaveTextContent('发现新版本 0.4.8');
  expect(call).toHaveBeenCalledOnce();
  expect(call).not.toHaveBeenCalledWith('install_update', {});
  call.mockResolvedValueOnce({kind: 'success', value: {state: 'cancelled'}});
  fireEvent.click(install);
  expect(await screen.findByText('已取消更新，当前版本保持不变。')).toBeVisible();
  expect(call).toHaveBeenLastCalledWith('install_update', {});
});

it('keeps update failure visible inside the settings content', async () => {
  setup({nativeFailure: '请先保存所有音频，再检查更新。'});
  fireEvent.click(screen.getByRole('button', {name: '检查更新'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('请先保存所有音频，再检查更新。');
  expect(screen.queryByRole('button', {name: '下载并安装更新'})).not.toBeInTheDocument();
});

it('explains the desktop requirement when opened in an ordinary browser', async () => {
  const {call} = setup();
  call.mockResolvedValueOnce({kind: 'unsupported'});
  fireEvent.click(screen.getByRole('button', {name: '检查更新'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('请在对白桌面应用中检查更新。');
});

it('retains running-task restrictions while preserving the exit request', () => {
  setup({running: true});
  expect(screen.getByRole('button', {name: '检查更新'})).toBeDisabled();
  expect(screen.queryByRole('button', {name: '结束并清理…'})).not.toBeInTheDocument();
  expect(screen.getByRole('button', {name: '保存并退出'})).toBeEnabled();
});

it('requests native exit without marking the empty workspace as pending', () => {
  const {model, call} = setup({noWorkspace: true});
  fireEvent.click(screen.getByRole('button', {name: '退出对白'}));
  expect(call).toHaveBeenCalledWith('request_close', {});
  expect(model.perform).not.toHaveBeenCalled();
});

it('defaults to direct and saves an explicit system proxy choice', async () => {
  const {model} = setup();
  expect(screen.getByRole('button', {name: '直连'})).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(screen.getByRole('button', {name: '系统代理'}));
  await waitFor(() => expect(model.api.setConnectionMode).toHaveBeenCalledWith('system_proxy'));
  await waitFor(() => expect(model.setConfig).toHaveBeenCalledOnce());
});
it('does not allow changing transport during synthesis', () => {
  setup({running: true});
  expect(screen.getByRole('button', {name: '直连'})).toBeDisabled();
  expect(screen.getByRole('button', {name: '系统代理'})).toBeDisabled();
});

it('defaults to five connection attempts and persists a changed limit', async () => {
  const {model} = setup();
  const input = screen.getByRole('spinbutton', {name: '连接尝试次数'});
  expect(input).toHaveValue(5);
  expect(input).toHaveAttribute('min', '1');
  expect(input).toHaveAttribute('max', '30');
  fireEvent.change(input, {target: {value: '30'}});
  fireEvent.click(screen.getByRole('button', {name: '保存'}));
  await waitFor(() => expect(model.api.setRetryAttempts).toHaveBeenCalledWith(30));
  expect(model.setConfig).toHaveBeenCalledWith(expect.any(Function));
  expect(await screen.findByRole('status')).toHaveTextContent('已保存。');
});

it('rejects an out-of-range value without sending it to the server', async () => {
  const {model} = setup();
  const input = screen.getByRole('spinbutton', {name: '连接尝试次数'});
  fireEvent.change(input, {target: {value: '31'}});
  fireEvent.submit(input.closest('form')!);
  expect(await screen.findByRole('alert')).toHaveTextContent('请输入 1–30 之间的整数。');
  expect(model.api.setRetryAttempts).not.toHaveBeenCalled();
});

it('locks the attempt setting while synthesis is running', () => {
  setup({running: true});
  expect(screen.getByRole('spinbutton', {name: '连接尝试次数'})).toBeDisabled();
  expect(screen.getByRole('button', {name: '保存'})).toBeDisabled();
});
