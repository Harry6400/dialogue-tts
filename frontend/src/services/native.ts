import type {Folder, NativeFailure, Preferences, PreferenceWrite, SaveReceipt, UpdateResult} from '../types/contracts';

interface Methods {
  request_close: {args: Record<string, never>; value: {ok: boolean}};
  get_preferences: {args: Record<string, never>; value: Preferences};
  set_preferences: {args: PreferenceWrite; value: {ok: boolean; error?: string}};
  choose_input_directory: {args: Record<string, never>; value: Folder & {ok: true; files: {name: string; content: string}[]}};
  choose_output_directory: {args: Record<string, never>; value: Folder & {ok: true}};
  refresh_input_directory: {args: {folder_id: string}; value: Folder & {ok: true; files: {name: string; content: string}[]}};
  check_output_space: {args: {folder_id: string}; value: {ok: true; writable: boolean; free_bytes: number | null}};
  check_completed: {args: {folder_id: string; fingerprints: string[]}; value: {ok: true; completed: Omit<SaveReceipt, 'ok'>[]}};
  save_to_output: {args: {resource_path: string; folder_id: string}; value: SaveReceipt};
  reveal_output: {args: {folder_id: string}; value: {ok: boolean}};
  check_for_update: {args: Record<string, never>; value: UpdateResult};
  install_update: {args: Record<string, never>; value: UpdateResult};
}
type Method = keyof Methods;
type Channel = 'dialogueFolders' | 'dialogueUpdater';
type Reply = {request_id: string; result: unknown};
export interface NativeHost {
  webkit?: {messageHandlers?: Partial<Record<Channel, {postMessage: (message: {request_id: string; method: Method; args: unknown}) => void}>>};
  pywebview?: {api?: Partial<Record<Method, (...args: unknown[]) => Promise<unknown>>>};
  dialogueNativeFolderResponse?: (reply: Reply) => void;
  dialogueNativeUpdateResponse?: (reply: Reply) => void;
}
export type Outcome<T> = {kind: 'success'; value: T} | {kind: 'cancelled' | 'unsupported'} | {kind: 'error' | 'unknown'; message: string};
interface Pending {resolve: (value: unknown) => void; timer?: ReturnType<typeof setTimeout>}
interface Broker {refs: number; pending: Map<string, Pending>; originals: Pick<NativeHost, 'dialogueNativeFolderResponse' | 'dialogueNativeUpdateResponse'>}
const brokers = new WeakMap<NativeHost, Broker>();

function normalize<T>(value: unknown): Outcome<T> {
  if (!value || typeof value !== 'object') return {kind: 'unknown', message: '原生操作未返回有效状态。'};
  const result = value as NativeFailure & {state?: string; message?: string};
  if (result.cancelled || result.state === 'cancelled') return {kind: 'cancelled'};
  if (result.ok === false || result.state === 'error') return {kind: 'error', message: result.error || result.message || '原生操作失败。'};
  if ('ok' in result && result.ok === true || ['latest', 'available', 'waiting_for_exit'].includes(result.state || '')) return {kind: 'success', value: value as T};
  return {kind: 'unknown', message: '原生操作状态未知，请先核对，不要重复提交。'};
}

export function createNativeBridge(host: NativeHost = window as NativeHost) {
  let broker = brokers.get(host);
  if (!broker) {
    broker = {refs: 0, pending: new Map(), originals: {dialogueNativeFolderResponse: host.dialogueNativeFolderResponse, dialogueNativeUpdateResponse: host.dialogueNativeUpdateResponse}};
    const shared = broker;
    for (const key of ['dialogueNativeFolderResponse', 'dialogueNativeUpdateResponse'] as const) {
      host[key] = reply => {
        const pending = shared.pending.get(reply?.request_id);
        if (!pending) {shared.originals[key]?.(reply); return;}
        clearTimeout(pending.timer); shared.pending.delete(reply.request_id); pending.resolve(reply.result);
      };
    }
    brokers.set(host, broker);
  }
  const shared = broker;
  shared.refs++;
  let disposed = false;
  return {
    async call<M extends Method>(method: M, args: Methods[M]['args']): Promise<Outcome<Methods[M]['value']>> {
      if (disposed) return {kind: 'unknown', message: '原生会话已结束。'};
      const channel: Channel = method === 'check_for_update' || method === 'install_update' ? 'dialogueUpdater' : 'dialogueFolders';
      const handler = host.webkit?.messageHandlers?.[channel];
      try {
        if (handler) {
          const bytes = crypto.getRandomValues(new Uint8Array(16));
          const request_id = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
          const result = await new Promise<unknown>(resolve => {
            // A user's file panel / installation confirmation must not expire as an ordinary request.
            const interactive = method.startsWith('choose_') || method === 'install_update';
            const pending: Pending = {resolve};
            if (!interactive) pending.timer = setTimeout(() => {shared.pending.delete(request_id); resolve(null);}, 30_000);
            shared.pending.set(request_id, pending);
            try {handler.postMessage({request_id, method, args});} catch {clearTimeout(pending.timer); shared.pending.delete(request_id); resolve(null);}
          });
          return normalize<Methods[M]['value']>(result);
        }
        const fn = host.pywebview?.api?.[method];
        if (!fn) return {kind: 'unsupported'};
        const values = args as Record<string, unknown>;
        const positional = method === 'set_preferences' ? [args] : method === 'check_completed' ? [values.folder_id, values.fingerprints] : method === 'save_to_output' ? [values.resource_path, values.folder_id] : 'folder_id' in values ? [values.folder_id] : [];
        return normalize<Methods[M]['value']>(await fn(...positional));
      } catch {return {kind: 'unknown', message: '无法确认原生操作结果，请先核对状态。'};}
    },
    dispose() {
      if (disposed) return;
      disposed = true; shared.refs--;
      if (shared.refs) return;
      for (const pending of shared.pending.values()) {clearTimeout(pending.timer); pending.resolve(null);}
      shared.pending.clear();
      for (const key of ['dialogueNativeFolderResponse', 'dialogueNativeUpdateResponse'] as const) host[key] = shared.originals[key];
      brokers.delete(host);
    },
  };
}
