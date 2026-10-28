import {act, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {App} from '../app/App';
import type {NextIssue, WorkspaceItem, WorkspaceSummary} from '../types/contracts';

const settings = {voices: {AGENT: 'zh-CN-XiaoxiaoNeural', USER: 'zh-CN-XiaoyiNeural'}, rate: 1, pause_ms: 250, auto_voice: true, manual_user_voice: false} as const;
const categories = {total: 211, pairing: 1, input: 205, changed: 1, conflict: 1, synthesis: 1, save: 1, other: 1};
const inputItems: WorkspaceItem[] = Array.from({length: 205}, (_, index) => ({id: 'input-' + index, filename: String(100000 + index) + '.mp3', status: 'invalid', error_code: 'invalid_xml', error: '虚构 XML 格式异常', selected: false}));
const otherItems: WorkspaceItem[] = [
  {id: 'pairing', filename: '900001.mp3', status: 'invalid', error_code: 'missing_track', error: '虚构声道缺失', selected: false},
  {id: 'changed', filename: '900002.mp3', status: 'changed', error_code: 'input_changed', error: '虚构输入变化', selected: true},
  {id: 'conflict', filename: '900003.mp3', status: 'conflict', error_code: 'output_conflict', error: '虚构同名冲突', selected: false},
  {id: 'synthesis', filename: '900004.mp3', status: 'synthesis_error', error_code: 'synthesis_failed', error: '虚构合成失败', selected: false},
  {id: 'save', filename: '900005.mp3', status: 'save_error', error_code: 'save_failed', error: '虚构保存失败', selected: false},
  {id: 'other', filename: '900006.mp3', status: 'invalid', error_code: 'future_error', error: '虚构未知问题', selected: false},
];
function fixture(patch: Partial<WorkspaceSummary> = {}): WorkspaceSummary {
  return {id: 'issues-workspace', status: 'ready', phase: 'ready', revision: 1, discovered: 428, scanned: 428, total: 214, ready: 1, error: 211, issues: categories, saved: 0, save_pending: 2, selected: 2, ready_selected: 1, selection_query: {q: '', filter: 'ready', issue: ''}, processed: 0, input_folder: {folder_id: 'input', path: '/fictional/input', valid: true}, output_folder: {folder_id: 'output', path: '/fictional/output', valid: true}, settings, selection_id: 'fixed-selection', can_edit: true, save_error: 1, synthesis_error: 1, ...patch};
}
function server(patch: Partial<WorkspaceSummary> = {}) {
  let summary = fixture(patch);
  let nextIndex = 0;
  const rows = [{id: 'ready', filename: '000001.mp3', status: 'ready', selected: true} as WorkspaceItem, ...inputItems, ...otherItems];
  const calls: {url: URL; body?: Record<string, unknown>}[] = [];
  let nextReply = async (): Promise<NextIssue> => nextIndex++ === 0 ? {item_id: 'input-200', page: 3, page_size: 100, wrapped: false, matched_total: 205, filter: 'error', issue: 'input'} : {item_id: 'input-0', page: 1, page_size: 100, wrapped: true, matched_total: 205, filter: 'error', issue: 'input'};
  const transport = vi.fn<typeof fetch>(async (path, options) => {
    const url = new URL(String(path), 'http://localhost');
    const body = options?.body ? JSON.parse(String(options.body)) as Record<string, unknown> : undefined;
    calls.push({url, body});
    let data: unknown;
    if (url.pathname === '/api/config') data = {token: 'fixture-token', voices: [{id: 'zh-CN-XiaoxiaoNeural', label: '晓晓', gender: 'Female'}, {id: 'zh-CN-XiaoyiNeural', label: '晓伊', gender: 'Female'}], can_shutdown: true};
    else if (url.pathname === '/api/workspaces/safety') data = {busy: false, unsaved: true, saving: false, pending: false};
    else if (url.pathname === '/api/workspaces') data = {workspaces: [summary]};
    else if (url.pathname.endsWith('/snapshot')) data = {...summary, items: [], reset: false};
    else if (url.pathname.endsWith('/next-issue')) data = await nextReply();
    else if (url.pathname.includes('/items/')) {const current = rows.find(row => row.id === url.pathname.split('/').at(-1)); data = {item: current, workspace_id: summary.id, rows: [], sources: [], warnings: [], muted_count: 0, voice_choice: {user: 'zh-CN-XiaoyiNeural', reason: '称呼未判定'}};}
    else if (url.pathname.endsWith('/selection')) {summary = {...summary, selected: 0, ready_selected: 0, selection_id: 'selection-after-clear'}; data = {selection_id: summary.selection_id, count: 0, hidden_count: 0, selection_query: {q: body?.q, filter: body?.filter, issue: body?.issue}};}
    else if (url.pathname.endsWith('/retry')) {summary = {...summary, status: 'running', can_edit: false, has_run_plan: true}; data = summary;}
    else {
      const filter = url.searchParams.get('filter'); const issue = url.searchParams.get('issue') || ''; const query = url.searchParams.get('q') || ''; const page = Number(url.searchParams.get('page') || '1');
      const categoryIds: Record<string, string[]> = {input: inputItems.map(row => row.id), pairing: ['pairing'], changed: ['changed'], conflict: ['conflict'], synthesis: ['synthesis'], save: ['save'], other: ['other']};
      const matched = rows.filter(row => (!query || row.filename?.includes(query)) && (filter !== 'error' || row.status !== 'ready') && (!issue || categoryIds[issue]?.includes(row.id)));
      const visibleSelected = matched.filter(row => row.selected).length;
      data = {...summary, items: matched.slice((page - 1) * 100, page * 100), matched_total: matched.length, page, page_size: 100, issue, selection_hidden_count: summary.selected ? Math.max(0, summary.selected - visibleSelected) : 0};
    }
    return new Response(JSON.stringify(data), {headers: {'Content-Type': 'application/json'}});
  });
  vi.stubGlobal('fetch', transport);
  return {calls, setNext: (reply: () => Promise<NextIssue>) => {nextReply = reply;}};
}
afterEach(() => vi.unstubAllGlobals());

// Each mode keeps its own bounded table mounted. Scope text lookups to the
// active table so JSDOM need not recompute every row's accessibility tree for
// each async retry; still verify the located control's accessible name.
function activeTable() {
  const table = Array.from(document.querySelectorAll<HTMLTableElement>('table[aria-label="通话列表"]')).find(table => !table.closest('[hidden]'));
  if (!table) throw new Error('Active call table is not mounted');
  expect(table).toHaveRole('table'); expect(table).toHaveAccessibleName('通话列表');
  return table;
}
async function findCall(name: string) {
  const button = await waitFor(() => within(activeTable()).getByText(name, {selector: 'button'}));
  expect(button).toBeVisible(); expect(button).toHaveAccessibleName(name);
  return button;
}
function call(name: string) {return within(activeTable()).getByText(name, {selector: 'button'});}
function pageControl(name: string) {return within(activeTable().parentElement!.parentElement!).getByLabelText(name, {selector: 'button'});}
function issuePanel() {return screen.getByLabelText('问题分类');}
function issueControl(name: string) {return within(issuePanel()).getByRole('button', {name});}
function statusFilter() {return within(screen.getByLabelText('通话状态筛选')).getByRole('button', {name: /^需处理/});}
function footerControl(name: string) {return within(document.querySelector('footer')!).getByRole('button', {name});}

it('uses global reason counts while pages and selection requests retain the chosen issue scope', async () => {
  const backend = server(); render(<App/>);
  await findCall('000001.mp3');
  fireEvent.click(statusFilter());
  const panel = issuePanel();
  expect(within(panel).getByText('本批次 211 通')).toBeVisible();
  fireEvent.click(within(panel).getByRole('button', {name: '筛选输入文件，205 通'}));
  await findCall('100099.mp3');
  expect(activeTable().querySelectorAll('tbody tr')).toHaveLength(100);
  expect(document.querySelectorAll('tbody tr').length).toBeLessThanOrEqual(200);
  expect(screen.getByTitle('其中 2 通不在当前筛选')).toHaveTextContent('已选 2 通 · 筛选外 2');
  fireEvent.click(pageControl('下一页'));
  await findCall('100199.mp3');
  expect(within(panel).getByRole('button', {name: '筛选输入文件，205 通'})).toHaveAttribute('aria-pressed', 'true');
  const pageRequest = backend.calls.filter(call => call.url.pathname === '/api/workspaces/issues-workspace').at(-1)!;
  expect(Object.fromEntries(pageRequest.url.searchParams)).toMatchObject({filter: 'error', issue: 'input', page: '2', limit: '100'});
  fireEvent.click(within(screen.getByLabelText('通话选择操作')).getByRole('button', {name: '清空选择'}));
  await waitFor(() => expect(backend.calls.some(call => call.url.pathname.endsWith('/selection'))).toBe(true));
  expect(backend.calls.find(call => call.url.pathname.endsWith('/selection'))?.body).toEqual({mode: 'clear', q: '', filter: 'error', issue: 'input'});
  expect(JSON.stringify(backend.calls.map(call => call.body))).not.toContain('item_ids');
});

it('loads the server-reported next page and focuses that problem, including wraparound', async () => {
  const backend = server(); render(<App/>); await findCall('000001.mp3');
  fireEvent.click(statusFilter());
  fireEvent.click(issueControl('筛选输入文件，205 通'));
  await findCall('100099.mp3');
  fireEvent.click(pageControl('下一页'));
  fireEvent.click(await findCall('100199.mp3'));
  fireEvent.click(issueControl('下一问题'));
  const next = await findCall('100200.mp3');
  await waitFor(() => expect(next).toHaveFocus());
  expect(activeTable().querySelectorAll('tbody tr')).toHaveLength(5);
  const request = backend.calls.find(call => call.url.pathname.endsWith('/next-issue'))!;
  expect(Object.fromEntries(request.url.searchParams)).toEqual({q: '', issue: 'input', limit: '100', after_id: 'input-199'});
  expect(backend.calls.filter(call => call.url.pathname === '/api/workspaces/issues-workspace').at(-1)?.url.searchParams.get('page')).toBe('3');
  fireEvent.click(call('100204.mp3')); fireEvent.click(issueControl('下一问题'));
  const wrapped = await findCall('100000.mp3');
  await waitFor(() => expect(wrapped).toHaveFocus());
  expect(activeTable().querySelectorAll('tbody tr')).toHaveLength(100);
  expect(within(issuePanel()).getByRole('status')).toHaveTextContent('已回到当前筛选的第一个问题');
});

it('ignores a next-problem reply after its category has changed', async () => {
  const backend = server(); let resolve!: (value: NextIssue) => void;
  backend.setNext(() => new Promise(done => {resolve = done;}));
  render(<App/>); await findCall('000001.mp3');
  fireEvent.click(statusFilter()); fireEvent.click(issueControl('筛选输入文件，205 通'));
  await findCall('100099.mp3');
  fireEvent.click(issueControl('下一问题'));
  fireEvent.click(issueControl('筛选保存异常，1 通'));
  await findCall('900005.mp3');
  await act(async () => resolve({item_id: 'input-200', page: 3, page_size: 100, wrapped: false, matched_total: 205, filter: 'error', issue: 'input'}));
  expect(issueControl('筛选保存异常，1 通')).toHaveAttribute('aria-pressed', 'true');
  expect(within(activeTable()).queryByText('100200.mp3', {selector: 'button'})).not.toBeInTheDocument();
  const preview = screen.getByRole('region', {name: '通话快速预览'});
  expect(within(preview).getByText(/检查输出目录权限与可用空间/)).toBeVisible();
});

it('keeps pending audio outside issue categories and retries saving without source access or synthesis consent', async () => {
  const backend = server({status: 'interrupted', has_run_plan: true, can_edit: false, error: 0, issues: {total: 0, pairing: 0, input: 0, changed: 0, conflict: 0, synthesis: 0, save: 0, other: 0}, save_pending: 2, save_error: 0, synthesis_error: 0, input_folder: {folder_id: 'input', path: '/fictional/missing', valid: false}});
  render(<App/>);
  expect(await screen.findByTestId('pending-audio')).toHaveTextContent('2 通完整音频待保存');
  expect(screen.queryByTestId('consent')).not.toBeInTheDocument();
  const retry = footerControl('重试保存'); await waitFor(() => expect(retry).toBeEnabled()); fireEvent.click(retry);
  await waitFor(() => expect(backend.calls.some(call => call.url.pathname.endsWith('/retry'))).toBe(true));
  expect(backend.calls.find(call => call.url.pathname.endsWith('/retry'))?.body).toEqual({kind: 'save'});
  expect(backend.calls.some(call => /\/(start|resume|audition)$/.test(call.url.pathname))).toBe(false);
});

it('uses eligible selected count for generation and retries synthesis only from its explicit action', async () => {
  // A completed frozen plan may contain failed synthesis plus a new ready
  // selection; a never-started ready batch cannot retry a prior plan.
  const backend = server({status: 'done', has_run_plan: true, save_pending: 0, save_error: 0}); render(<App/>); await findCall('000001.mp3');
  expect(screen.getByTestId('generate')).toHaveTextContent('生成 1 通');
  expect(screen.getByText('已选 2 通 · 可生成 1 通')).toBeVisible();
  await waitFor(() => expect(footerControl('重试合成')).toBeEnabled());
  expect(backend.calls.some(call => call.url.pathname.endsWith('/retry'))).toBe(false);
  fireEvent.click(footerControl('重试合成'));
  await waitFor(() => expect(backend.calls.find(call => call.url.pathname.endsWith('/retry'))?.body).toEqual({kind: 'synthesis', consent: true}));
});
