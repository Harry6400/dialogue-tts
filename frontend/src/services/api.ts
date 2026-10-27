import type {BatchPayload, BatchPreview, BatchSnapshot, Config, JobSnapshot, PairPayload, Preview} from '../types/contracts';

export class ApiError extends Error {
  constructor(message: string, readonly status: number | null, readonly outcome: 'failed' | 'unknown' = 'failed') {super(message);}
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
  private id(value: string) {
    if (!/^[A-Za-z0-9_-]{1,100}$/.test(value)) throw new ApiError('任务编号无效。', null);
    return encodeURIComponent(value);
  }
}
