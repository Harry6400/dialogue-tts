import type {BatchPayload, BatchPreview, BatchSnapshot, Config, JobSnapshot, NextIssue, PairPayload, Preview, SelectionResult, SelectionWrite, SoundSettings, VoiceId, WorkspaceDetail, WorkspaceIssue, WorkspacePage, WorkspaceQuery, WorkspaceReport, WorkspaceSafety, WorkspaceSnapshot, WorkspaceSummary} from '../types/contracts';

export class ApiError extends Error {
  constructor(message: string, readonly status: number | null, readonly outcome: 'failed' | 'unknown' = 'failed') {super(message);}
}

export function requestId() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
}

export class ApiClient {
  private token: string | null = null;
  constructor(private readonly transport: typeof fetch = (...args) => fetch(...args)) {}

  private async request<T>(path: string, body?: unknown): Promise<T> {
    if (body !== undefined && !this.token) throw new ApiError('请先读取本机配置。', null);
    let response: Response;
    try {
      response = await this.transport(path, {
        method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: body === undefined ? {'Accept': 'application/json'} : {'Accept': 'application/json', 'Content-Type': 'application/json', 'X-App-Token': this.token!},
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new ApiError(body === undefined ? '无法连接本机服务。' : '提交结果未知，请先核对任务状态，勿重复创建。', null, body === undefined ? 'failed' : 'unknown');
    }
    let data: unknown;
    try {data = await response.json();} catch {throw new ApiError('服务返回了无法识别的响应。', response.status, body === undefined ? 'failed' : 'unknown');}
    if (!response.ok) {
      const message = data && typeof data === 'object' && 'error' in data && typeof data.error === 'string' ? data.error : '本机请求失败。';
      throw new ApiError(message, response.status);
    }
    return data as T;
  }

  async getConfig(): Promise<Config> {
    const config = await this.request<Config>('/api/config');
    if (!config || typeof config.token !== 'string' || !Array.isArray(config.voices)) throw new ApiError('本机配置无效。', null);
    this.token = config.token;
    return config;
  }
  preview(payload: PairPayload) {return this.request<Preview>('/api/preview', payload);}
  previewBatch(payload: BatchPayload) {return this.request<BatchPreview>('/api/batches/preview', payload);}
  async getJob(id: string) {return this.request<JobSnapshot>('/api/jobs/' + this.id(id));}
  async getBatch(id: string) {return this.request<BatchSnapshot>('/api/batches/' + this.id(id));}
  startJob(payload: PairPayload & {consent: true; request_id: string}) {return this.request<JobSnapshot>('/api/jobs', payload);}
  startBatch(payload: BatchPayload & {consent: true; request_id: string}) {return this.request<BatchSnapshot>('/api/batches', payload);}
  cancelJob(id: string) {return this.request<JobSnapshot>('/api/jobs/' + this.id(id) + '/cancel', {});}
  cancelBatch(id: string) {return this.request<BatchSnapshot>('/api/batches/' + this.id(id) + '/cancel', {});}
  retryBatch(id: string, consent: true) {return this.request<BatchSnapshot>('/api/batches/' + this.id(id) + '/retry', {consent});}
  listWorkspaces() {return this.request<{workspaces: WorkspaceSummary[]}>('/api/workspaces');}
  async getWorkspaceSafety() {
    const state = await this.request<WorkspaceSafety & {unknown?: unknown}>('/api/workspaces/safety');
    if (!state || typeof state !== 'object' || ['busy', 'unsaved', 'saving', 'pending'].some(key => typeof state[key as keyof WorkspaceSafety] !== 'boolean') || state.legacy_unsaved !== undefined && typeof state.legacy_unsaved !== 'boolean' || state.unknown === true) throw new ApiError('无法确认任务与保存状态，请重新核验。', null);
    return state;
  }
  importWorkspace(inputFolderId: string) {return this.request<WorkspaceSummary>('/api/workspaces/import', {input_folder_id: inputFolderId});}
  getWorkspace(id: string, query: WorkspaceQuery) {
    const params = new URLSearchParams({q: query.q, filter: query.filter, issue: query.issue || '', page: String(query.page), limit: String(query.limit || 100), sort: 'filename'});
    return this.request<WorkspacePage>(this.workspacePath(id) + '?' + params);
  }
  getWorkspaceDetail(id: string, itemId: string) {return this.request<WorkspaceDetail>(this.workspacePath(id) + '/items/' + this.id(itemId));}
  getNextWorkspaceIssue(id: string, query: {q: string; issue: WorkspaceIssue; after_id?: string}) {
    const params = new URLSearchParams({q: query.q, issue: query.issue, limit: '100'});
    if (query.after_id) params.set('after_id', query.after_id);
    return this.request<NextIssue>(this.workspacePath(id) + '/next-issue?' + params);
  }
  getWorkspaceSnapshot(id: string, revision: number) {return this.request<WorkspaceSnapshot>(this.workspacePath(id) + '/snapshot?revision=' + revision);}
  selectWorkspace(id: string, selection: SelectionWrite) {return this.request<SelectionResult>(this.workspacePath(id) + '/selection', selection);}
  setWorkspaceSettings(id: string, body: {settings?: SoundSettings; selection_id?: string; item_id?: string; customer_voice?: VoiceId | null}) {return this.request<WorkspaceSummary>(this.workspacePath(id) + '/settings', body);}
  startWorkspace(id: string, body: {selection_id: string; output_folder_id: string; consent: true; request_id: string}) {return this.request<WorkspaceSummary>(this.workspacePath(id) + '/start', body);}
  pauseWorkspace(id: string) {return this.request<WorkspaceSummary>(this.workspacePath(id) + '/pause', {});}
  resumeWorkspace(id: string, offline = false) {return this.request<WorkspaceSummary>(this.workspacePath(id) + '/resume', offline ? {} : {consent: true});}
  retryWorkspace(id: string, kind: 'synthesis' | 'save') {return this.request<WorkspaceSummary>(this.workspacePath(id) + '/retry', kind === 'save' ? {kind} : {kind, consent: true});}
  cancelWorkspaceScan(id: string) {return this.request<WorkspaceSummary>(this.workspacePath(id) + '/cancel-scan', {});}
  finishWorkspace(id: string, discardUnsaved = false) {return this.request<WorkspaceSummary>(this.workspacePath(id) + '/finish', {discard_unsaved: discardUnsaved});}
  createWorkspaceReport(id: string) {return this.request<WorkspaceReport>(this.workspacePath(id) + '/report', {});}
  discardWorkspaceReport(id: string) {return this.request<{ok: true; report_id: string}>('/api/reports/' + this.id(id) + '/discard', {});}
  cancelPendingReports() {return this.request<{ok: true; cancelled: number}>('/api/reports/cancel-pending', {});}
  setWorkspaceFolders(id: string, folders: {input_folder_id?: string; output_folder_id?: string}) {return this.request<WorkspaceSummary>(this.workspacePath(id) + '/folders', folders);}
  workspaceAudio(id: string, itemId: string) {return this.workspacePath(id) + '/items/' + this.id(itemId) + '/audio';}
  audition(settings: SoundSettings, requestId: string) {return this.request<JobSnapshot>('/api/audition', {...settings, consent: true, request_id: requestId});}
  private workspacePath(id: string) {return '/api/workspaces/' + this.id(id);}
  private id(value: string) {
    if (!/^[A-Za-z0-9_-]{1,100}$/.test(value)) throw new ApiError('任务编号无效。', null);
    return encodeURIComponent(value);
  }
}
