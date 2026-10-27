import {describe, expect, it, vi} from 'vitest';
import {ApiClient} from '../services/api';
import {createNativeBridge, type NativeHost} from '../services/native';

describe('HTTP contract', () => {
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
