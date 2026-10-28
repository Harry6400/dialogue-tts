import {StrictMode} from 'react';
import {act, fireEvent, render, screen, waitFor,within} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {App} from '../app/App';
import type {NativeHost} from '../services/native';
import type {WorkspaceItem, WorkspaceSummary} from '../types/contracts';

const settings = {voices: {AGENT: 'zh-CN-XiaoxiaoNeural', USER: 'zh-CN-XiaoyiNeural'}, rate: 1, pause_ms: 250, auto_voice: true, manual_user_voice: false} as const;
const item: WorkspaceItem = {id: 'item-one', filename: '000123.mp3', status: 'ready', selected: false, customer_voice: 'zh-CN-XiaoyiNeural', voice_source: '称呼未判定'};
function summary(patch: Partial<WorkspaceSummary> = {}): WorkspaceSummary {return {id: 'workspace-one', status: 'ready', phase: 'ready', revision: 1, discovered: 120000, scanned: 120000, total: 60000, ready: 60000, error: 0, saved: 0, selected: 0, processed: 0, input_folder: {folder_id: 'input-grant', path: '/fictional/input', valid: true}, output_folder: {folder_id: 'output-grant', path: '/fictional/output', valid: true}, settings, selection_id: 'selection-one', can_edit: true, has_run_plan: false, ...patch};}

function server(initial: WorkspaceSummary | null = summary(), matchEvidence=false) {
  let workspace = initial;
  let currentItem = {...item};
  let globalSafety={busy:false,unsaved:false,saving:false,pending:false};
  const calls: {path: string; body: Record<string, unknown> | undefined}[] = [];
  const transport = vi.fn<typeof fetch>(async (path, options) => {
    const url = String(path);
    const body = options?.body ? JSON.parse(String(options.body)) as Record<string, unknown> : undefined;
    calls.push({path: url, body});
    if(url.includes('/items/')&&workspace?.input_folder.valid===false)return new Response(JSON.stringify({error:'来源目录尚未恢复授权。'}),{status:403,headers:{'Content-Type':'application/json'}});
    let data: unknown;
    if (url === '/api/config') data = {token: 'test-token', voices: [{id: 'zh-CN-XiaoxiaoNeural', label: '晓晓', gender: 'Female'}, {id: 'zh-CN-XiaoyiNeural', label: '晓伊', gender: 'Female'}], can_shutdown: true};
    else if (url === '/api/workspaces/safety') data=globalSafety;
    else if (url === '/api/workspaces') data = {workspaces: workspace ? [workspace] : []};
    else if (url === '/api/workspaces/import') {workspace = summary(); data = workspace;}
    else if (url.includes('/items/')) data = {filename: '000123.mp3', item: currentItem, workspace_id: workspace?.id, rows: [{id: 0, speaker: 'AGENT', text: matchEvidence?'先生，您好。':'虚构会议室预约。', start_ms: 0}, {id: 1, speaker: 'USER', text: '请预留一小时。', start_ms: 500}, {id: 2, speaker: 'USER', text: '禁止显示的静音内容', muted: true}], warnings: [], sources: [{name: '0001231.xml', role: 'AGENT', segments: 1}, {name: '0001230.xml', role: 'USER', segments: 2}], voice_choice: {user: matchEvidence?'zh-CN-YunxiNeural':'zh-CN-XiaoyiNeural', reason: matchEvidence?'称呼：先生':'称呼未判定',address:matchEvidence?'male':'undetermined',...(matchEvidence?{evidence_row_id:0}:{})}, muted_count: 1};
    else if (url.endsWith('/selection')) {const selected = body?.mode === 'clear' ? false : body?.selected !== false; currentItem = {...currentItem, selected}; workspace = {...workspace!, selected: selected ? body?.mode === 'all' ? 60000 : 1 : 0, selection_id: 'selection-two'}; data = {selection_id: 'selection-two', count: workspace.selected, hidden_count: 0};}
    else if (url.endsWith('/start') || url.endsWith('/resume')) {workspace = {...workspace!, status: 'running', can_edit: false, has_run_plan: true}; data = workspace;}
    else if (url.endsWith('/snapshot')) data = {...workspace, items: [], reset: false};
    else data = {...workspace, items: url.includes('q=notfound')?[]:[currentItem], matched_total: url.includes('q=notfound')?0:60000, page: 1, page_size: 100, selection_hidden_count: 0};
    return new Response(JSON.stringify(data), {headers: {'Content-Type': 'application/json'}});
  });
  vi.stubGlobal('fetch', transport);
  return {calls, transport, invalidateOutput:()=>{workspace={...workspace!,output_folder:{...workspace!.output_folder!,valid:false}};},setSafety:(next:typeof globalSafety)=>{globalSafety=next;}, restore: () => {workspace = {...workspace!, input_folder: {...workspace!.input_folder, valid: true}, output_folder: {...workspace!.output_folder!, valid: true}};}};
}

afterEach(() => {delete (window as NativeHost).pywebview; vi.unstubAllGlobals();});

it('loads a bounded workspace without generating on repeated mounting and hides muted text', async () => {
  const backend = server();
  render(<StrictMode><App/></StrictMode>);
  await screen.findByTestId('workspace-ready');
  expect(screen.getAllByText('60,000 通')[0]).toBeInTheDocument();
  expect(screen.queryByText('禁止显示的静音内容')).not.toBeInTheDocument();
  expect(backend.calls.filter(call => call.body)).toHaveLength(0);
  expect(screen.getByTestId('generate')).toBeDisabled();
  expect(document.querySelectorAll('tbody tr').length).toBeLessThan(10);
});

it('imports only an opaque native grant and never transfers a path or XML body', async () => {
  const backend = server(null);
  const choose = vi.fn().mockResolvedValue({ok: true, folder_id: 'opaque-input', path: '/fictional/private-input', valid: true});
  (window as NativeHost).pywebview = {api: {choose_workspace_input: choose}};
  render(<App/>);
  await screen.findByRole('button', {name: '选择 XML 文件夹'});
  await waitFor(() => expect(screen.getByRole('button', {name: '选择 XML 文件夹'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button', {name: '选择 XML 文件夹'}));
  await screen.findByTestId('workspace-ready');
  expect(choose).toHaveBeenCalledWith();
  expect(backend.calls.find(call => call.path === '/api/workspaces/import')?.body).toEqual({input_folder_id: 'opaque-input'});
  expect(JSON.stringify(backend.calls)).not.toContain('/fictional/private-input');
});

it('requires selection and new consent, then submits the server selection snapshot once', async () => {
  const backend = server(); render(<App/>);
  await screen.findByTestId('workspace-ready');
  fireEvent.click(await screen.findByRole('checkbox', {name: '选择 000123.mp3'}));
  await waitFor(() => expect(screen.getByTestId('generate')).toHaveTextContent('生成 1 通'));
  expect(screen.getByTestId('generate')).toBeDisabled();
  fireEvent.click(screen.getByTestId('consent'));
  fireEvent.click(screen.getByTestId('generate'));
  await screen.findByTestId('pause');
  const calls = backend.calls.filter(call => call.path.endsWith('/start'));
  expect(calls).toHaveLength(1);
  expect(calls[0].body).toMatchObject({selection_id: 'selection-two', output_folder_id: 'output-grant', consent: true});
  expect(calls[0].body).not.toHaveProperty('item_ids');
});

it('selects the full filter without shipping sixty thousand IDs', async () => {
  const backend = server(); render(<App/>); await screen.findByTestId('workspace-ready');
  fireEvent.click(await screen.findByRole('checkbox', {name: '选择当前页可生成通话'}));
  await screen.findByRole('button', {name: '选择筛选中全部通话'});
  fireEvent.click(screen.getByRole('button', {name: '选择筛选中全部通话'}));
  await waitFor(() => expect(screen.getByTestId('generate')).toHaveTextContent('生成 60,000 通'));
  expect(backend.calls.filter(call => call.path.endsWith('/selection')).at(-1)?.body).toEqual({mode: 'all', q: '', filter: 'all'});
});

it('restores access before resuming and never resumes automatically after restart', async () => {
  const backend = server(summary({status: 'interrupted', phase: 'recovery', has_run_plan: true, can_edit: false, selected: 20, input_folder: {folder_id: 'input-grant', path: '', valid: false}, output_folder: {folder_id: 'output-grant', path: '', valid: false}}));
  const restore = vi.fn().mockImplementation(async () => {backend.restore(); return {ok: true, input_folder: {folder_id: 'input-grant', path: '/fictional/input', valid: true}, output_folder: {folder_id: 'output-grant', path: '/fictional/output', valid: true}};});
  (window as NativeHost).pywebview = {api: {restore_workspace_access: restore}};
  render(<App/>); await screen.findByTestId('resume');
  expect(screen.getByTestId('resume')).toBeDisabled();
  expect(backend.calls.some(call => call.path.endsWith('/resume'))).toBe(false);
  await screen.findAllByText('此通对话暂无可显示正文。');
  expect(screen.queryByText('虚构会议室预约。')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: '恢复访问'}));
  await waitFor(() => expect(restore).toHaveBeenCalledWith('workspace-one'));
  await waitFor(() => expect(screen.queryByRole('button', {name: '恢复访问'})).not.toBeInTheDocument());
  expect(screen.getByTestId('resume')).toBeDisabled();
  await screen.findAllByText('虚构会议室预约。');
  fireEvent.click(screen.getByTestId('consent')); fireEvent.click(screen.getByTestId('resume'));
  await screen.findByTestId('pause');
  expect(backend.calls.filter(call => call.path.endsWith('/resume'))).toHaveLength(1);
});

it('shows actual source roles even when the filename suffixes are reversed', async () => {
  server(); render(<App/>); await screen.findAllByText('虚构会议室预约。');
  fireEvent.click(screen.getByRole('button', {name: '查看真实源文件'}));
  const agentName = screen.getByText('0001231.xml');
  const customerName = screen.getByText('0001230.xml');
  expect(agentName.closest('li')).toHaveTextContent('客服');
  expect(customerName.closest('li')).toHaveTextContent('客户');
});

it('keeps updater safety clear of its own installation request while locking the UI', async () => {
  server(null);
  let safety: {busy: boolean} | undefined;
  const install = vi.fn().mockImplementation(async () => {
    await new Promise(resolve => setTimeout(resolve, 10));
    safety = (window as Window & {dialogueUpdateSafety?: () => {busy: boolean}}).dialogueUpdateSafety?.();
    return {state: 'cancelled'};
  });
  (window as NativeHost).pywebview = {api: {check_for_update: vi.fn().mockResolvedValue({state: 'available', current_version: '0.3.4', latest_version: '0.4.0'}), install_update: install}};
  render(<App/>);
  await waitFor(() => expect(screen.getByRole('button', {name: '选择 XML 文件夹'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button', {name: '应用设置'}));
  fireEvent.click(screen.getByRole('button', {name: '检查更新'}));
  fireEvent.click(await screen.findByRole('button', {name: '下载并安装更新'}));
  await waitFor(() => expect(install).toHaveBeenCalledOnce());
  await waitFor(() => expect(safety).toEqual({busy: false, unsaved: false, saving: false, persistent_workspace: true}));
});

it('opens application settings from the native menu and uses the same check without installing', async () => {
  server(null);
  const check = vi.fn().mockResolvedValue({state: 'latest', current_version: '0.4.0'});
  const install = vi.fn();
  (window as NativeHost).pywebview = {api: {check_for_update: check, install_update: install}};
  render(<App/>);
  await waitFor(() => expect(screen.getByRole('button', {name: '选择 XML 文件夹'})).toBeEnabled());
  expect(check).not.toHaveBeenCalled();
  const host = window as Window & {dialogueCheckForUpdates?: () => void};
  act(() => host.dialogueCheckForUpdates?.());
  expect(await screen.findByRole('dialog', {name: '应用设置'})).toBeVisible();
  expect(await screen.findByText('当前已是最新版本')).toBeVisible();
  expect(check).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', {name: '检查更新'}));
  await waitFor(() => expect(check).toHaveBeenCalledTimes(2));
  expect(install).not.toHaveBeenCalled();
});

it('clears the visible search and server query together from an empty result',async()=>{
  const backend=server();render(<App/>);
  await screen.findByRole('checkbox',{name:'选择 000123.mp3'});
  const search=screen.getByRole('searchbox',{name:'搜索通话编号'});
  fireEvent.change(search,{target:{value:'notfound'}});
  fireEvent.click(await screen.findByRole('button',{name:'清除筛选'}));
  expect(search).toHaveValue('');
  await screen.findByRole('checkbox',{name:'选择 000123.mp3'});
  await waitFor(()=>expect(backend.calls.filter(call=>call.path.includes('?q=')).at(-1)?.path).toContain('?q=&'));
});

it('shows recovery when the server invalidates an output grant previously held locally',async()=>{
  const backend=server(summary({selected:1}));render(<App/>);
  await screen.findByRole('checkbox',{name:'选择 000123.mp3'});
  fireEvent.click(screen.getByTestId('consent'));
  await waitFor(()=>expect(screen.getByTestId('generate')).toBeEnabled());
  backend.invalidateOutput();
  fireEvent.click(screen.getByRole('button',{name:/需处理/}));
  await screen.findByRole('button',{name:'恢复访问'});
  expect(screen.getByTestId('generate')).toBeDisabled();
});

it('keeps persistent caches out of legacy close warnings while still blocking updates',async()=>{
  const backend=server();backend.setSafety({busy:false,unsaved:true,saving:false,pending:false});
  render(<App/>);await screen.findByTestId('workspace-ready');
  const host=window as Window&{dialogueCloseSafety?:()=>object;dialogueUpdateSafety?:()=>{unsaved:boolean}};
  await waitFor(()=>expect(host.dialogueUpdateSafety?.().unsaved).toBe(true));
  expect(host.dialogueCloseSafety?.()).toEqual({busy:false,pending:false,running:false,saving:false,unsaved:false,persistent_workspace:true});
});

it('blocks closing on unknown global task state without mislabelling it as legacy unsaved audio',async()=>{
  const backend=server();
  vi.stubGlobal('fetch',vi.fn<typeof fetch>((path,options)=>String(path)==='/api/workspaces/safety'?Promise.reject(new TypeError('service unavailable')):backend.transport(path,options)));
  render(<App/>);await screen.findByTestId('workspace-ready');
  const host=window as Window&{dialogueCloseSafety?:()=>object;dialogueUpdateSafety?:()=>object};
  expect(host.dialogueCloseSafety?.()).toEqual({busy:true,pending:true,running:true,saving:true,unsaved:false,persistent_workspace:true});
  expect(host.dialogueUpdateSafety?.()).toMatchObject({busy:true,unsaved:true,saving:true});
});

it('continues an interrupted settings check without claiming online consent',async()=>{
  const backend=server(summary({status:'interrupted',phase:'recovery_settings',has_run_plan:false,can_edit:false}));render(<App/>);
  const button=await screen.findByRole('button',{name:'继续更新设置'});
  await waitFor(()=>expect(button).toBeEnabled());
  expect(screen.getByTestId('consent')).not.toBeChecked();
  fireEvent.click(button);
  await waitFor(()=>expect(backend.calls.find(call=>call.path.endsWith('/resume'))?.body).toEqual({}));
});

it('locates only the visible row supplied by the actual honorific matcher',async()=>{
  server(summary(),true);render(<App/>);
  await screen.findByRole('checkbox',{name:'选择 000123.mp3'});
  fireEvent.click(screen.getByRole('button',{name:'正文核对'}));
  const region=screen.getByRole('region',{name:'完整核对'});
  const evidence=await within(region).findByRole('button',{name:'定位称呼匹配依据'});
  const scroll=vi.spyOn(HTMLElement.prototype,'scrollIntoView');
  fireEvent.click(evidence);
  expect(scroll).toHaveBeenLastCalledWith({block:'center',behavior:'auto'});
  expect(within(region).getByText('先生，您好。').closest('[data-row-id="0"]')).toHaveFocus();
  scroll.mockRestore();
});


it('closes the batch menu with Escape without leaving transcript review',async()=>{
  server();render(<App/>);
  await screen.findByRole('checkbox',{name:'选择 000123.mp3'});
  fireEvent.click(screen.getByRole('button',{name:'正文核对'}));
  expect(screen.getByRole('button',{name:'正文核对'})).toHaveAttribute('aria-pressed','true');
  const trigger=screen.getByRole('combobox',{name:'切换本机批次'});
  fireEvent.keyDown(trigger,{key:'Enter'});
  const menu=await screen.findByRole('listbox');
  fireEvent.keyDown(menu,{key:'Escape'});
  await waitFor(()=>expect(screen.queryByRole('listbox')).not.toBeInTheDocument());
  expect(screen.getByRole('button',{name:'正文核对'})).toHaveAttribute('aria-pressed','true');
});
