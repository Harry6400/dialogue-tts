import {afterEach, describe, expect, it, vi} from 'vitest';
import {ApiClient} from '../services/api';
import {createNativeBridge, type NativeHost} from '../services/native';
afterEach(() => vi.useRealTimers());

describe('HTTP contract', () => {
  it('sends the configured connection attempt limit to the existing settings endpoint', async () => {
    const transport = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response(JSON.stringify({token: 'test-session', voices: []}))).mockResolvedValueOnce(new Response(JSON.stringify({ok: true, retry_attempts: 30})));
    const client = new ApiClient(transport);
    await client.getConfig();
    await client.setRetryAttempts(30);
    expect(transport.mock.calls[1][0]).toBe('/api/network');
    expect(transport.mock.calls[1][1]).toMatchObject({method: 'POST', body: JSON.stringify({retry_attempts: 30})});
  });
  it.each([null, {busy: 0, unsaved: false, saving: false, pending: false}, {busy: false, unsaved: false, saving: false}, {busy: false, unsaved: false, saving: false, pending: false, legacy_unsaved: 'unknown'}, {busy: false, unsaved: false, saving: false, pending: false, unknown: true}])('rejects malformed or unknown safety state %o', async state => {
    const client = new ApiClient(vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify(state))));
    await expect(client.getWorkspaceSafety()).rejects.toMatchObject({message: '无法确认任务与保存状态，请重新核验。'});
  });
  it('creates whole-workspace CSV snapshots and cancels only report resources with empty bodies', async () => {
    const transport = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response(JSON.stringify({token: 'test-session', voices: []}))).mockImplementation(async () => new Response(JSON.stringify({ok: true})));
    const client = new ApiClient(transport); const id = 'a'.repeat(32);
    await client.getConfig(); await client.createWorkspaceReport(id); await client.discardWorkspaceReport(id); await client.cancelPendingReports();
    expect(transport.mock.calls.slice(1).map(([path, options]) => [path, options?.body])).toEqual([['/api/workspaces/' + id + '/report', '{}'], ['/api/reports/' + id + '/discard', '{}'], ['/api/reports/cancel-pending', '{}']]);
  });
  it('requires configuration and preserves token, origin credentials and unknown POST outcome', async () => {
    const transport = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response(JSON.stringify({token: 'test-session', voices: []}))).mockRejectedValueOnce(new TypeError('connection lost'));
    const client = new ApiClient(transport);
    await expect(client.cancelJob('one')).rejects.toMatchObject({outcome: 'failed'});
    expect(transport).not.toHaveBeenCalled();
    await client.getConfig();
    await expect(client.cancelJob('one')).rejects.toMatchObject({outcome: 'unknown'});
    expect(transport.mock.calls[1][1]).toMatchObject({credentials: 'same-origin', headers: {'X-App-Token': 'test-session'}});
    await expect(client.getJob('../secret')).rejects.toMatchObject({message: '任务编号无效。'});
  });
});

describe('native contract', () => {
  it('writes application theme independently of batch sound preferences on Windows and Mac', async () => {
    const theme = vi.fn().mockResolvedValue({ok: true});
    const legacy = vi.fn();
    const windows = createNativeBridge({pywebview: {api: {set_application_preferences: theme, set_preferences: legacy}}});
    expect(await windows.call('set_application_preferences', {theme: 'dark'})).toMatchObject({kind: 'success'});
    expect(theme).toHaveBeenCalledExactlyOnceWith({theme: 'dark'}); expect(legacy).not.toHaveBeenCalled(); windows.dispose();
    let request: {request_id: string; method: string; args: unknown} | undefined;
    const host: NativeHost = {webkit: {messageHandlers: {dialogueFolders: {postMessage: message => {request = message;}}}}};
    const mac = createNativeBridge(host); const pending = mac.call('set_application_preferences', {theme: 'light'});
    expect(request).toMatchObject({method: 'set_application_preferences', args: {theme: 'light'}});
    host.dialogueNativeFolderResponse?.({request_id: request!.request_id, result: {ok: true}});
    expect(await pending).toMatchObject({kind: 'success'}); mac.dispose();
  });
  it('passes only the opaque report ID to the Windows save panel', async () => {
    const save = vi.fn().mockResolvedValue({cancelled: true}); const id = 'a'.repeat(32);
    const bridge = createNativeBridge({pywebview: {api: {save_workspace_report: save}}});
    expect(await bridge.call('save_workspace_report', {report_id: id})).toEqual({kind: 'cancelled'});
    expect(save).toHaveBeenCalledExactlyOnceWith(id); bridge.dispose();
  });
  it('does not expire the Mac report save panel as an ordinary 30 second request', async () => {
    vi.useFakeTimers(); let requestId = ''; let resolved = false;
    const host: NativeHost = {webkit: {messageHandlers: {dialogueFolders: {postMessage: message => {requestId = message.request_id;}}}}};
    const bridge = createNativeBridge(host);
    const pending = bridge.call('save_workspace_report', {report_id: 'a'.repeat(32)}).then(result => {resolved = true; return result;});
    await vi.advanceTimersByTimeAsync(60000); expect(resolved).toBe(false);
    host.dialogueNativeFolderResponse?.({request_id: requestId, result: {cancelled: true}});
    expect(await pending).toEqual({kind: 'cancelled'}); bridge.dispose();
  });
  it('passes expected names and route-only manual saves to Windows', async () => {
    const check = vi.fn().mockResolvedValue({ok: true, completed: [], conflicts: []});
    const save = vi.fn().mockResolvedValue({cancelled: true});
    const bridge = createNativeBridge({pywebview: {api: {check_completed: check, save_download: save}}});
    const names = {['a'.repeat(64)]: '000123.mp3'};
    await bridge.call('check_completed', {folder_id: 'authorized', fingerprints: Object.keys(names), expected_names: names});
    expect(check).toHaveBeenCalledWith('authorized', Object.keys(names), names);
    expect(await bridge.call('save_download', {resource_path: '/api/jobs/a/audio'})).toEqual({kind: 'cancelled'});
    expect(save).toHaveBeenCalledWith('/api/jobs/a/audio'); bridge.dispose();
  });
  it('does not simulate unsupported native success', async () => {
    const bridge = createNativeBridge({});
    expect(await bridge.call('choose_output_directory', {})).toEqual({kind: 'unsupported'});
    bridge.dispose(); bridge.dispose();
  });
  it('shares Mac registration, ignores unrelated replies, restores handlers and resolves abandoned requests as unknown', async () => {
    const original = vi.fn(); let replyId = '';
    const host: NativeHost = {dialogueNativeFolderResponse: original, webkit: {messageHandlers: {dialogueFolders: {postMessage: message => {replyId = message.request_id;}}}}};
    const first = createNativeBridge(host); const handler = host.dialogueNativeFolderResponse;
    const second = createNativeBridge(host); expect(host.dialogueNativeFolderResponse).toBe(handler);
    const pending = first.call('get_preferences', {});
    host.dialogueNativeFolderResponse?.({request_id: 'unknown', result: {ok: true}}); expect(original).toHaveBeenCalledOnce();
    host.dialogueNativeFolderResponse?.({request_id: replyId, result: {cancelled: true}}); expect(await pending).toEqual({kind: 'cancelled'});
    first.dispose(); expect(host.dialogueNativeFolderResponse).toBe(handler);
    const abandoned = second.call('get_preferences', {}); second.dispose();
    expect(await abandoned).toMatchObject({kind: 'unknown'}); expect(host.dialogueNativeFolderResponse).toBe(original);
  });
  it('keeps Windows positional folder capabilities and distinguishes failure', async () => {
    const save = vi.fn().mockResolvedValue({ok: false, error: '保存失败'});
    const bridge = createNativeBridge({pywebview: {api: {save_to_output: save}}});
    expect(await bridge.call('save_to_output', {resource_path: '/api/jobs/a/audio', folder_id: 'chosen'})).toEqual({kind: 'error', message: '保存失败'});
    expect(save).toHaveBeenCalledWith('/api/jobs/a/audio', 'chosen'); bridge.dispose();
  });
});
