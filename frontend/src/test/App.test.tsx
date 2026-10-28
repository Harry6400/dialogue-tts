import {StrictMode} from 'react';
import {act, fireEvent, render, screen, waitFor,within} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {App} from '../app/App';
import type {NativeHost} from '../services/native';
import type {WorkspaceItem, WorkspaceSafety, WorkspaceSummary} from '../types/contracts';

const settings = {voices: {AGENT: 'zh-CN-XiaoxiaoNeural', USER: 'zh-CN-XiaoyiNeural'}, rate: 1, pause_ms: 250, auto_voice: true, manual_user_voice: false} as const;
const item: WorkspaceItem = {id: 'item-one', filename: '000123.mp3', status: 'ready', selected: false, customer_voice: 'zh-CN-XiaoyiNeural', voice_source: '称呼未判定'};
function summary(patch: Partial<WorkspaceSummary> = {}): WorkspaceSummary {return {id: 'workspace-one', status: 'ready', phase: 'ready', revision: 1, discovered: 120000, scanned: 120000, total: 60000, ready: 60000, error: 0, issues: {total: 0, pairing: 0, input: 0, changed: 0, conflict: 0, synthesis: 0, save: 0, other: 0}, save_pending: 0, saved: 0, selected: 0, ready_selected: patch.selected ?? 0, selection_query: {q: '', filter: 'all', issue: ''}, processed: 0, input_folder: {folder_id: 'input-grant', path: '/fictional/input', valid: true}, output_folder: {folder_id: 'output-grant', path: '/fictional/output', valid: true}, settings, selection_id: 'selection-one', can_edit: true, has_run_plan: false, ...patch};}

function server(initial: WorkspaceSummary | null = summary(), matchEvidence=false, initialItem: Partial<WorkspaceItem> = {}, pageItems?:WorkspaceItem[]) {
  let workspace = initial;
  let currentItem = {...item, ...initialItem};
  let globalSafety: WorkspaceSafety={busy:false,unsaved:false,saving:false,pending:false};
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
    else if (url.endsWith('/report')) data = {report_id: 'a'.repeat(32), filename: '对白-批次清单-aaaaaaaa.csv', workspace_id: workspace!.id, rows: 120000, items: 60000, sources: 120000, size: 1024, revision: 8, sha256: 'c'.repeat(64), scope: 'whole_workspace', schema_version: 1, content_type: 'text/csv; charset=utf-8'};
    else if (url.startsWith('/api/reports/') && url.endsWith('/discard')) data = {ok: true, report_id: 'a'.repeat(32)};
    else if (url.includes('/items/')) data = {filename: '000123.mp3', item: currentItem, workspace_id: workspace?.id, rows: [{id: 0, speaker: 'AGENT', text: matchEvidence?'先生，您好。':'虚构会议室预约。', start_ms: 0}, {id: 1, speaker: 'USER', text: '请预留一小时。', start_ms: 500}, {id: 2, speaker: 'USER', text: '禁止显示的静音内容', muted: true}], warnings: ['按毫秒排序，不还原原始时长。', '两份来源开始时间不一致。'], info_notes: ['按毫秒排序，不还原原始时长。'], sources: [{name: '0001231.xml', role: 'AGENT', segments: 1}, {name: '0001230.xml', role: 'USER', segments: 2}], voice_choice: {user: matchEvidence?'zh-CN-YunxiNeural':'zh-CN-XiaoyiNeural', reason: matchEvidence?'称呼：先生':'称呼未判定',address:matchEvidence?'male':'undetermined',...(matchEvidence?{evidence_row_id:0}:{})}, muted_count: 1};
    else if (url.endsWith('/selection')) {const selected = body?.mode === 'clear' ? false : body?.selected !== false; const count = selected ? body?.mode === 'all' ? 60000 : 1 : 0; currentItem = {...currentItem, selected}; workspace = {...workspace!, selected: count, ready_selected: count, selection_id: 'selection-two'}; data = {selection_id: 'selection-two', count, hidden_count: 0, selection_query: {q: body?.q || '', filter: body?.filter || 'all', issue: body?.issue || ''}};}
    else if (url.endsWith('/start') || url.endsWith('/resume')) {workspace = {...workspace!, status: 'running', can_edit: false, has_run_plan: true}; data = workspace;}
    else if (url.endsWith('/settings')) {
      if (body && 'customer_voice' in body) currentItem = {...currentItem, customer_voice: body.customer_voice as WorkspaceItem['customer_voice'] || 'zh-CN-XiaoyiNeural', voice_source: body.customer_voice ? '手动选择' : '称呼未判定'};
      workspace = {...workspace!, revision: workspace!.revision + 1}; data = workspace;
    }
    else if (url.includes('/snapshot?')) data = {...workspace, items: [], reset: false};
    else {const page=Number(new URL(url,'http://localhost').searchParams.get('page')||1);data = {...workspace, items: url.includes('q=notfound')?[]:pageItems?pageItems.slice((page-1)*100,page*100):[currentItem], matched_total: url.includes('q=notfound')?0:pageItems?.length??60000, page, page_size: 100, selection_hidden_count: 0};}
    return new Response(JSON.stringify(data), {headers: {'Content-Type': 'application/json'}});
  });
  vi.stubGlobal('fetch', transport);
  return {calls, transport, updateWorkspace:(patch:Partial<WorkspaceSummary>)=>{workspace={...workspace!,...patch};}, invalidateOutput:()=>{workspace={...workspace!,output_folder:{...workspace!.output_folder!,valid:false}};},setSafety:(next:typeof globalSafety)=>{globalSafety=next;}, restore: () => {workspace = {...workspace!, input_folder: {...workspace!.input_folder, valid: true}, output_folder: {...workspace!.output_folder!, valid: true}};}};
}

afterEach(() => {delete (window as NativeHost).pywebview; vi.unstubAllGlobals();});

it('polls real scan counts and phases, then removes activity when the scan completes', async () => {
  const backend=server(summary({status:'scanning',phase:'enumerating',scanned:0,discovered:0,total:0,ready:0}));
  render(<App/>);
  await screen.findByRole('progressbar',{name:'扫描进度'});
  backend.updateWorkspace({revision:2,phase:'reading',scanned:300,discovered:300});
  await waitFor(()=>expect(screen.getByRole('progressbar',{name:'扫描进度'})).toHaveAttribute('aria-valuetext','正在读取 XML，已扫描 300 份文件'),{timeout:3000});
  backend.updateWorkspace({revision:3,phase:'validating'});
  await waitFor(()=>expect(screen.getByRole('status')).toHaveTextContent('正在配对与核验通话'),{timeout:3000});
  backend.updateWorkspace({revision:4,status:'ready',phase:'ready',total:150,ready:150});
  await waitFor(()=>expect(screen.queryByRole('progressbar',{name:'扫描进度'})).not.toBeInTheDocument(),{timeout:3000});
  expect(backend.calls.some(call=>call.path.endsWith('/start'))).toBe(false);
},10000);

it('synchronizes the sound panel trigger immediately when crossing the drawer breakpoint', async () => {
  vi.stubGlobal('innerWidth',1480); server(); render(<App/>);
  await screen.findByTestId('workspace-ready');
  const trigger=screen.getByRole('button',{name:'显示声音与输出'});
  expect(trigger).toHaveAttribute('aria-expanded','true');
  vi.stubGlobal('innerWidth',760); fireEvent(window,new Event('resize'));
  expect(trigger).toHaveAttribute('aria-expanded','false');
  fireEvent.click(trigger);
  const drawer=await screen.findByRole('dialog',{name:'声音与输出'});
  expect(trigger).toHaveAttribute('aria-expanded','true');
  fireEvent(drawer,new Event('cancel',{cancelable:true}));
  expect(trigger).toHaveAttribute('aria-expanded','false');
  vi.stubGlobal('innerWidth',1480); fireEvent(window,new Event('resize'));
  expect(trigger).toHaveAttribute('aria-expanded','true');
});

it('opens full review directly from a filename and keeps run controls in the header', async () => {
  server();render(<App/>);
  fireEvent.click(await screen.findByRole('button',{name:'000123.mp3'}));
  expect(screen.getByRole('button',{name:'正文核对'})).toHaveAttribute('aria-pressed','true');
  expect(screen.queryByRole('region',{name:'通话快速预览'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'显示预览'})).not.toBeInTheDocument();
  expect(screen.getByTestId('generate').closest('header')).not.toBeNull();
  expect(document.querySelector('main>footer')).toBeNull();
});

it('jumps directly to distant pages without sending selection or unbounded list requests', async () => {
  const backend=server();render(<App/>);
  await screen.findByRole('button',{name:'000123.mp3'});
  const input=screen.getByRole('textbox',{name:'跳转页码'});
  expect(input).toBeEnabled();
  fireEvent.change(input,{target:{value:'500'}});fireEvent.keyDown(input,{key:'Enter'});
  await waitFor(()=>expect(backend.calls.some(call=>call.path.includes('page=500&limit=100'))).toBe(true));
  expect(backend.calls.some(call=>call.path.endsWith('/selection')||call.path.endsWith('/start'))).toBe(false);
});

it('switches calls in a compact review picker, restores focus and keeps review open on Escape', async () => {
  vi.stubGlobal('innerWidth',760); server(); render(<App/>);
  await screen.findByRole('button',{name:'000123.mp3'});
  fireEvent.click(screen.getByRole('button',{name:'正文核对'}));
  const trigger=screen.getByRole('button',{name:'切换通话'});
  trigger.focus(); fireEvent.click(trigger);
  const dialog=await screen.findByRole('dialog',{name:'切换通话'});
  fireEvent(dialog,new Event('cancel',{cancelable:true}));
  expect(trigger).toHaveFocus();
  expect(screen.getByRole('button',{name:'正文核对'})).toHaveAttribute('aria-pressed','true');
  fireEvent.click(trigger);
  fireEvent.click(within(screen.getByRole('dialog',{name:'切换通话'})).getByRole('button',{name:'000123.mp3'}));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  vi.stubGlobal('innerWidth',1480);fireEvent(window,new Event('resize'));
  expect(screen.queryByRole('navigation',{name:'核对通话导航'})).not.toBeInTheDocument();
});

it('keeps empty search results and clearing available when the compact call list is closed', async () => {
  vi.stubGlobal('innerWidth',760);server();render(<App/>);
  await screen.findByRole('button',{name:'000123.mp3'});
  fireEvent.click(screen.getByRole('button',{name:'正文核对'}));
  fireEvent.change(screen.getByRole('searchbox',{name:'搜索通话编号'}),{target:{value:'notfound'}});
  const reader=()=>screen.getByRole('region',{name:'完整核对'});
  await waitFor(()=>expect(within(reader()).getByText('没有符合条件的通话')).toBeInTheDocument());
  fireEvent.click(within(reader()).getByRole('button',{name:'清除筛选'}));
  await waitFor(()=>expect(within(reader()).getByRole('heading',{name:'000123.mp3'})).toBeInTheDocument());
  expect(screen.getByRole('searchbox',{name:'搜索通话编号'})).toHaveValue('');
});

it('moves compact review across page boundaries in both directions without changing selection', async () => {
  vi.stubGlobal('innerWidth',760);
  const items=Array.from({length:101},(_,i)=>({...item,id:'call-'+i,filename:String(i).padStart(6,'0')+'.mp3'}));
  const backend=server(summary({total:101,ready:101}),false,{},items);render(<App/>);
  const table=await screen.findByRole('table',{name:'通话列表'});
  fireEvent.click(await within(table).findByText('000099.mp3',{selector:'button'}));
  const navigation=screen.getByRole('navigation',{name:'核对通话导航'});
  const reader=screen.getByRole('region',{name:'完整核对'});
  fireEvent.click(within(navigation).getByRole('button',{name:'下一通'}));
  await within(reader).findByRole('heading',{name:'000100.mp3'});
  expect(within(navigation).getByRole('button',{name:'下一通'})).toBeDisabled();
  fireEvent.click(within(navigation).getByRole('button',{name:'上一通'}));
  await within(reader).findByRole('heading',{name:'000099.mp3'});
  expect(backend.calls.some(call=>call.path.endsWith('/selection')||call.path.endsWith('/start'))).toBe(false);
});

it('restores preferences when the Windows bridge arrives after React without changing the active batch output', async () => {
  const backend = server(); render(<App/>);
  await screen.findByTestId('workspace-ready');
  const preferences = vi.fn().mockResolvedValue({ok: true, theme: 'dark', output_folder: {folder_id: 'other-output', path: '/fictional/unrelated-output', valid: true}});
  (window as NativeHost).pywebview = {api: {get_preferences: preferences}};
  await act(async () => {window.dispatchEvent(new Event('pywebviewready'));});
  await waitFor(() => expect(document.documentElement.dataset.theme).toBe('dark'));
  expect(screen.getByTestId('output-folder')).toHaveTextContent('output');
  expect(screen.getByTestId('output-folder')).not.toHaveTextContent('unrelated-output');
  expect(backend.calls.some(call => call.body)).toBe(false);
});

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

it('starts from the explicit generate action without a consent checkbox and submits the selection once', async () => {
  const backend = server(); render(<App/>);
  await screen.findByTestId('workspace-ready');
  fireEvent.click(await screen.findByRole('checkbox', {name: '选择 000123.mp3'}));
  await waitFor(() => expect(screen.getByTestId('generate')).toHaveTextContent('生成 1 通'));
  await waitFor(() => expect(screen.getByTestId('generate')).toBeEnabled());
  expect(screen.queryByTestId('consent')).not.toBeInTheDocument();
  expect(backend.calls.some(call => call.path.endsWith('/start'))).toBe(false);
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
  expect(backend.calls.filter(call => call.path.endsWith('/selection')).at(-1)?.body).toEqual({mode: 'all', q: '', filter: 'all', issue: ''});
});

it('restores access before resuming and never resumes automatically after restart', async () => {
  const backend = server(summary({status: 'interrupted', phase: 'recovery', has_run_plan: true, can_edit: false, selected: 20, input_folder: {folder_id: 'input-grant', path: '', valid: false}, output_folder: {folder_id: 'output-grant', path: '', valid: false}}));
  const restore = vi.fn().mockImplementation(async () => {backend.restore(); return {ok: true, input_folder: {folder_id: 'input-grant', path: '/fictional/input', valid: true}, output_folder: {folder_id: 'output-grant', path: '/fictional/output', valid: true}};});
  (window as NativeHost).pywebview = {api: {restore_workspace_access: restore}};
  render(<App/>); await screen.findByTestId('resume');
  expect(screen.getByTestId('resume')).toBeDisabled();
  expect(backend.calls.some(call => call.path.endsWith('/resume'))).toBe(false);
  fireEvent.click(await screen.findByRole('button',{name:'000123.mp3'}));
  await screen.findAllByText('此通对话暂无可显示正文。');
  expect(screen.queryByText('虚构会议室预约。')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: '恢复访问'}));
  await waitFor(() => expect(restore).toHaveBeenCalledWith('workspace-one'));
  await waitFor(() => expect(screen.queryByRole('button', {name: '恢复访问'})).not.toBeInTheDocument());
  await waitFor(() => expect(screen.getByTestId('resume')).toBeEnabled());
  expect(backend.calls.some(call => call.path.endsWith('/resume'))).toBe(false);
  await screen.findAllByText('虚构会议室预约。');
  fireEvent.click(screen.getByTestId('resume'));
  await screen.findByTestId('pause');
  expect(backend.calls.filter(call => call.path.endsWith('/resume'))).toHaveLength(1);
});

it('shows actual source roles even when the filename suffixes are reversed', async () => {
  server(); render(<App/>); fireEvent.click(await screen.findByRole('button',{name:'000123.mp3'})); await screen.findAllByText('虚构会议室预约。');
  fireEvent.click(screen.getByRole('button', {name: '查看通话信息'}));
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
  expect(screen.queryByTestId('consent')).not.toBeInTheDocument();
  fireEvent.click(button);
  await waitFor(()=>expect(backend.calls.find(call=>call.path.endsWith('/resume'))?.body).toEqual({}));
});

it('keeps a verification entry when current grants are valid but earlier outputs remain unverified', async () => {
  server(summary({status: 'interrupted', phase: 'recovery', has_run_plan: true, counts: {unverified: 1}, can_edit: false}));
  const restore = vi.fn().mockResolvedValue({cancelled: true});
  (window as NativeHost).pywebview = {api: {restore_workspace_access: restore}};
  render(<App/>);
  const button = await screen.findByRole('button', {name: '核验已有结果'});
  await waitFor(() => expect(button).toBeEnabled());
  expect(screen.getByTestId('resume')).toBeDisabled();
  fireEvent.click(button);
  await waitFor(() => expect(restore).toHaveBeenCalledExactlyOnceWith('workspace-one'));
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

it('uses batch filters as the single list scope while preserving selection and review', async () => {
  const backend = server(); render(<App/>);
  await screen.findByRole('checkbox', {name: '选择 000123.mp3'});
  expect(screen.getAllByRole('button', {name: '新建批次'})).toHaveLength(1);
  expect(screen.queryByRole('button', {name: '添加批次'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button', {name: '生成进度'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button', {name: '已保存结果'})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('checkbox', {name: '选择 000123.mp3'}));
  await waitFor(() => expect(screen.getByTestId('generate')).toHaveTextContent('生成 1 通'));
  fireEvent.click(screen.getByRole('button', {name: /^已保存/}));
  await waitFor(() => expect(backend.calls.filter(call => call.path.includes('?q=')).at(-1)?.path).toContain('filter=saved'));
  expect(screen.getByRole('heading', {name: 'input'})).toHaveAttribute('title', 'input');
  fireEvent.click(screen.getByRole('button', {name: /^进行中/}));
  expect(screen.queryByTestId('task-progress')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: /^全部/}));
  expect(screen.queryByTestId('task-progress')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: '正文核对'}));
  const active = within(screen.getByRole('region', {name: '完整核对'})).getByRole('heading', {name: '000123.mp3'});
  expect(active).toBeVisible();
  fireEvent.click(screen.getByRole('button', {name: /^待生成/}));
  expect(screen.getByRole('button', {name: '正文核对'})).toHaveAttribute('aria-pressed', 'true');
  await waitFor(() => expect(screen.getByRole('checkbox', {name: '选择 000123.mp3'})).toBeChecked());
  fireEvent.click(screen.getByRole('button', {name: '列表总览'}));
  expect(screen.getByRole('button', {name: /^待生成/})).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', {name: '000123.mp3'})).toHaveAttribute('aria-pressed', 'true');
});

it('keeps cleanup confirmation and complete-cache protection behind batch actions', async () => {
  const backend = server(summary({has_unsaved_audio: true, counts: {save_pending: 1}})); render(<App/>);
  await screen.findByRole('checkbox', {name: '选择 000123.mp3'});
  const trigger = screen.getByRole('button', {name: '批次操作'}); trigger.focus();
  fireEvent.keyDown(trigger, {key: 'ArrowUp'});
  const finish = await screen.findByRole('menuitem', {name: '结束并清理批次…'});
  expect(finish).toHaveFocus();
  fireEvent.click(finish);
  const confirmation = screen.getByRole('dialog', {name: '结束任务并清理记录'});
  const submit = within(confirmation).getByRole('button', {name: '结束并清理记录'});
  expect(submit).toBeDisabled();
  expect(backend.calls.some(call => call.path.endsWith('/finish'))).toBe(false);
  fireEvent.click(within(confirmation).getByRole('button', {name: '取消'}));
  await waitFor(() => expect(trigger).toHaveFocus());
  fireEvent.click(trigger); fireEvent.click(screen.getByRole('menuitem', {name: '结束并清理批次…'}));
  fireEvent.click(screen.getByRole('checkbox', {name: '我确认放弃尚未保存的音频缓存'}));
  fireEvent.click(screen.getByRole('button', {name: '结束并清理记录'}));
  await waitFor(() => expect(backend.calls.filter(call => call.path.endsWith('/finish'))).toHaveLength(1));
  expect(backend.calls.find(call => call.path.endsWith('/finish'))?.body).toEqual({discard_unsaved: true});
});

it('disables batch cleanup during generation and closes its menu without leaving review', async () => {
  server(summary({status: 'running', can_edit: false, has_run_plan: true})); render(<App/>);
  await screen.findByTestId('pause');
  await screen.findByRole('checkbox', {name: '选择 000123.mp3'});
  expect(screen.getByRole('button', {name: '新建批次'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button', {name: '正文核对'}));
  const trigger = screen.getByRole('button', {name: '批次操作'});
  fireEvent.click(trigger);
  expect(screen.getByRole('menuitem', {name: '结束并清理批次…'})).toBeDisabled();
  fireEvent.keyDown(screen.getByRole('menu', {name: '批次操作'}), {key: 'Escape'});
  await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument());
  expect(trigger).toHaveFocus();
  expect(screen.getByRole('button', {name: '正文核对'})).toHaveAttribute('aria-pressed', 'true');
});

it('applies then restores selected voices through the same selection snapshot repeatedly', async () => {
  const backend = server(); render(<App/>);
  fireEvent.click(await screen.findByRole('checkbox', {name: '选择 000123.mp3'}));
  const picker = await screen.findByRole('combobox', {name: '所选通话客户声音'});
  await waitFor(() => expect(picker).toBeEnabled());
  expect(picker.closest('aside')).toBeNull();
  expect(screen.queryByRole('button', {name: '所选通话恢复跟随批次'})).not.toBeInTheDocument();
  fireEvent.keyDown(picker, {key: 'Enter'});
  fireEvent.click(await screen.findByRole('option', {name: /晓晓/}));
  await waitFor(() => expect(backend.calls.filter(call => call.path.endsWith('/settings'))).toHaveLength(1));
  for (const expected of [2, 3]) {
    await waitFor(() => expect(picker).toBeEnabled());
    expect(picker).toHaveTextContent('批量设置声音');
    fireEvent.keyDown(picker, {key: 'Enter'});
    fireEvent.click(await screen.findByRole('option', {name: '恢复跟随批次'}));
    await waitFor(() => expect(backend.calls.filter(call => call.path.endsWith('/settings'))).toHaveLength(expected));
  }
  expect(backend.calls.filter(call => call.path.endsWith('/settings')).map(call => call.body)).toEqual([
    {selection_id: 'selection-two', customer_voice: 'zh-CN-XiaoxiaoNeural'},
    {selection_id: 'selection-two', customer_voice: null},
    {selection_id: 'selection-two', customer_voice: null},
  ]);
});

it('restores a single manual voice from its inherited menu option', async () => {
  const backend = server(summary(), false, {voice_source: '手动选择'}); render(<App/>);
  await screen.findByRole('checkbox', {name: '选择 000123.mp3'});
  fireEvent.click(screen.getByRole('button', {name: '当前通话'}));
  const picker = screen.getByRole('combobox', {name: '当前通话客户声音'});
  expect(screen.queryByRole('button', {name: '恢复跟随批次'})).not.toBeInTheDocument();
  fireEvent.keyDown(picker, {key: 'Enter'});
  fireEvent.click(await screen.findByRole('option', {name: '跟随批次'}));
  await waitFor(() => expect(backend.calls.find(call => call.path.endsWith('/settings'))?.body).toEqual({item_id: 'item-one', customer_voice: null}));
  await waitFor(() => expect(picker).toHaveTextContent('跟随批次'));
});

it('opens whole-batch CSV export from batch actions and waits for the native save panel', async () => {
  const backend = server(); let finish!: (value: unknown) => void;
  const save = vi.fn(() => new Promise(resolve => {finish = resolve;}));
  (window as NativeHost).pywebview = {api: {save_workspace_report: save}};
  render(<App/>); await screen.findByRole('checkbox', {name: '选择 000123.mp3'});
  fireEvent.click(screen.getByRole('button', {name: '批次操作'}));
  fireEvent.click(screen.getByRole('menuitem', {name: '导出本批次完整清单（CSV）'}));
  const dialog = screen.getByRole('dialog', {name: '导出批次清单（CSV）'});
  expect(within(dialog).getByText(/不受当前搜索、筛选或勾选范围影响/)).toBeVisible();
  expect(backend.calls.some(call => call.path.endsWith('/report'))).toBe(false);
  fireEvent.click(within(dialog).getByRole('button', {name: '导出并选择保存位置'}));
  await waitFor(() => expect(save).toHaveBeenCalledExactlyOnceWith('a'.repeat(32)));
  expect(within(dialog).getByRole('button', {name: '关闭弹窗'})).toBeDisabled();
  fireEvent(dialog, new Event('cancel', {cancelable: true}));
  expect(dialog).toBeVisible();
  act(() => (window as Window & {dialogueCheckForUpdates?: () => void}).dialogueCheckForUpdates?.());
  expect(dialog).toBeVisible();
  expect(screen.queryByRole('dialog', {name: '应用设置'})).not.toBeInTheDocument();
  await act(async () => finish({ok: true, save_id: 'd'.repeat(32), report_id: 'a'.repeat(32), name: '清单.csv', path: '/fictional/report.csv', size: 1024, sha256: 'c'.repeat(64)}));
  await waitFor(() => expect(within(dialog).getByRole('status')).toHaveTextContent('已保存并核验 清单.csv'));
  expect(backend.calls.find(call => call.path.endsWith('/report'))?.body).toEqual({});
  expect(backend.calls.find(call => call.path.endsWith('/discard'))?.body).toEqual({});
  expect(backend.calls.some(call => /\/(start|resume|retry|audition)$/.test(call.path))).toBe(false);
  expect(screen.queryByTestId('consent')).not.toBeInTheDocument();
});

it('changes only application theme and reverts visual state on a failed preference save', async () => {
  const backend = server();
  const theme = vi.fn().mockResolvedValueOnce({ok: true}).mockResolvedValueOnce({ok: false, error: '偏好暂不可写'});
  const legacy = vi.fn();
  (window as NativeHost).pywebview = {api: {set_application_preferences: theme, set_preferences: legacy}};
  render(<App/>); await screen.findByTestId('workspace-ready');
  fireEvent.click(screen.getByRole('button', {name: '应用设置'}));
  const dialog = screen.getByRole('dialog', {name: '应用设置'});
  fireEvent.click(within(dialog).getByRole('button', {name: '深色'}));
  await waitFor(() => expect(theme).toHaveBeenCalledWith({theme: 'dark'}));
  await waitFor(() => expect(within(dialog).getByRole('button', {name: '浅色'})).toBeEnabled());
  fireEvent.click(within(dialog).getByRole('button', {name: '浅色'}));
  await waitFor(() => expect(document.documentElement.dataset.theme).toBe('dark'));
  expect(theme.mock.calls).toEqual([[{theme: 'dark'}], [{theme: 'light'}]]);
  expect(legacy).not.toHaveBeenCalled();
  expect(backend.calls.some(call => call.path.endsWith('/settings'))).toBe(false);
});

it('blocks updates for a retained legacy audio capture while leaving close confirmation to native safety', async () => {
  const backend = server(); backend.setSafety({busy: false, unsaved: false, saving: false, pending: false, legacy_unsaved: true});
  render(<App/>); await screen.findByTestId('workspace-ready');
  const host = window as Window & {dialogueUpdateSafety?: () => {unsaved: boolean}; dialogueCloseSafety?: () => {unsaved: boolean}};
  await waitFor(() => expect(host.dialogueUpdateSafety?.().unsaved).toBe(true));
  expect(host.dialogueCloseSafety?.().unsaved).toBe(false);
});

it('moves routine notes into call information but keeps actual warnings visible',async()=>{
  server();render(<App/>);await screen.findByTestId('workspace-ready');await screen.findByRole('button',{name:'000123.mp3'});
  fireEvent.click(screen.getByRole('button',{name:'正文核对'}));
  expect(await screen.findByText('虚构会议室预约。')).toBeVisible();
  expect(await screen.findByText('两份来源开始时间不一致。')).toBeVisible();
  expect(screen.getByText('跳过 1 段静音')).toBeVisible();
  expect(screen.queryByText('按毫秒排序，不还原原始时长。')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'查看通话信息'}));
  expect(screen.getByRole('dialog',{name:'通话信息'})).toBeVisible();
  expect(screen.getByRole('heading',{name:'处理说明'})).toBeVisible();
  expect(screen.getByText('按毫秒排序，不还原原始时长。')).toBeVisible();
});
