import {useCallback, useEffect, useRef, useState} from 'react';
import {ApiClient} from '../services/api';
import {createNativeBridge, type Outcome} from '../services/native';
import type {Config, Folder, Preferences, SelectionWrite, WorkspaceDetail, WorkspaceFilter, WorkspaceItem, WorkspaceSafety, WorkspaceSummary} from '../types/contracts';

export const isWorking = (workspace: WorkspaceSummary | null) => !!workspace && ['scanning', 'running', 'pausing'].includes(workspace.status);
export const canEdit = (workspace: WorkspaceSummary | null) => !!workspace && (workspace.can_edit ?? !['scanning', 'running', 'pausing', 'paused', 'interrupted'].includes(workspace.status));
export const messageOf = (error: unknown) => error instanceof Error ? error.message : '操作未完成，请重试。';

export function useWorkspace() {
  const [api] = useState(() => new ApiClient());
  const native = useRef<ReturnType<typeof createNativeBridge> | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [preferences, setPreferences] = useState<Preferences | null>(null);
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([]);
  const [workspace, setWorkspace] = useState<WorkspaceSummary | null>(null);
  const [items, setItems] = useState<WorkspaceItem[]>([]);
  const [matchedTotal, setMatchedTotal] = useState(0);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<WorkspaceFilter>('all');
  const [page, setPage] = useState(1);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [detail, setDetail] = useState<WorkspaceDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [pending, setPending] = useState('');
  const [revision, setRevision] = useState(0);
  const [detailRevision, setDetailRevision] = useState(0);
  const [hiddenSelection, setHiddenSelection] = useState(0);
  const [output, setOutput] = useState<Folder | null>(null);
  const [safety, setSafety] = useState<WorkspaceSafety | null>(null);
  const pendingRef = useRef(false);
  const workspaceRef = useRef(workspace);
  useEffect(() => {workspaceRef.current = workspace;}, [workspace]);
  const workspaceId = workspace?.id;

  const refresh = useCallback(() => setRevision(value => value + 1), []);
  const accept = useCallback((value: WorkspaceSummary) => {
    setWorkspace(value);
    setWorkspaces(existing => [value, ...existing.filter(item => item.id !== value.id)]);
  }, []);

  useEffect(() => {
    const bridge = createNativeBridge(); native.current = bridge;
    let alive = true;
    async function load() {
      try {
        const configuration = await api.getConfig();
        const [list, prefs] = await Promise.all([api.listWorkspaces(), bridge.call('get_preferences', {})]);
        if (!alive) return;
        setConfig(configuration); setWorkspaces(list.workspaces);
        if (prefs.kind === 'success') {setPreferences(prefs.value); setOutput(prefs.value.output_folder || null);}
        if (list.workspaces.length) {setWorkspace(list.workspaces[0]); setOutput(list.workspaces[0].output_folder || null);}
      } catch (issue) {if (alive) setError(messageOf(issue));}
      finally {if (alive) setLoading(false);}
    }
    void load();
    return () => {alive = false; bridge.dispose(); native.current = null;};
  }, [api]);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function pollSafety() {
      try {const value = await api.getWorkspaceSafety(); if (alive) setSafety(value);}
      catch {if (alive) setSafety(null);}
      finally {if (alive) timer = setTimeout(() => void pollSafety(), 1200);}
    }
    void pollSafety();
    return () => {alive = false; clearTimeout(timer);};
  }, [api]);

  useEffect(() => {
    if (!workspaceId) return;
    let alive = true;
    async function load() {
      setLoading(true);
      try {
        const result = await api.getWorkspace(workspaceId!, {q: query, filter, page});
        if (!alive) return;
        accept(result); setItems(result.items); setMatchedTotal(result.matched_total);
        setHiddenSelection(result.selection_hidden_count || 0);
        setActiveId(current => result.items.some(item => item.id === current) ? current : result.items.find(item => item.status === 'ready')?.id || result.items[0]?.id || null);
      } catch (issue) {if (alive) setError(messageOf(issue));}
      finally {if (alive) setLoading(false);}
    }
    void load();
    return () => {alive = false;};
  }, [api, workspaceId, query, filter, page, revision, accept]);

  useEffect(() => {
    if (!workspaceId) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await api.getWorkspaceSnapshot(workspaceId!, workspaceRef.current?.revision || 0);
        if (!alive) return;
        const previous = workspaceRef.current;
        const changed = result.revision !== previous?.revision;
        accept(result);
        if (result.items.length) {
          const changes = new Map(result.items.map(item => [item.id, item]));
          setItems(current => current.map(item => changes.get(item.id) || item));
        }
        if (changed && (result.reset || filter !== 'all' || result.status === 'scanning' || result.phase === 'ready')) refresh();
        if (changed && previous?.status === 'scanning' && result.status !== 'scanning') setDetailRevision(value => value + 1);
      } catch (issue) {if (alive) setError(messageOf(issue));}
      finally {if (alive) timer = setTimeout(() => void poll(), 1800);}
    }
    timer = setTimeout(() => void poll(), 1800);
    return () => {alive = false; clearTimeout(timer);};
  }, [api, workspaceId, filter, accept, refresh]);

  useEffect(() => {
    if (!workspaceId || !activeId) {setDetail(null); return;}
    let alive = true;
    async function load() {
      setDetailLoading(true); setDetail(null);
      try {const result = await api.getWorkspaceDetail(workspaceId!, activeId!); if (alive) setDetail(result);}
      catch {if (alive) setDetail(null);}
      finally {if (alive) setDetailLoading(false);}
    }
    void load(); return () => {alive = false;};
  }, [api, workspaceId, activeId, detailRevision]);

  async function perform(label: string, operation: () => Promise<void>) {
    if (pendingRef.current) return;
    pendingRef.current = true; setPending(label); setError('');
    try {await operation();} catch (issue) {setError(messageOf(issue));}
    finally {pendingRef.current = false; setPending('');}
  }

  function nativeValue<T>(result: Outcome<T>): T | null {
    if (result.kind === 'success') return result.value;
    if (result.kind === 'cancelled') return null;
    throw new Error('message' in result ? result.message : '此操作需要在对白桌面应用中选择文件夹。');
  }

  async function importFolder() {
    await perform('读取来源文件夹', async () => {
      if (!native.current) return;
      const folder = nativeValue(await native.current.call('choose_workspace_input', {}));
      if (!folder) return;
      const result = await api.importWorkspace(folder.folder_id);
      setPage(1); setQuery(''); setFilter('all'); setActiveId(null); setDetail(null); accept(result); refresh();
    });
  }

  async function chooseOutput() {
    await perform('选择结果文件夹', async () => {
      if (!native.current) return;
      const folder = nativeValue(await native.current.call('choose_workspace_output', {}));
      if (folder) {
        if (workspaceId) accept(await api.setWorkspaceFolders(workspaceId, {output_folder_id: folder.folder_id}));
        setOutput(folder); refresh();
      }
    });
  }

  async function restoreAccess() {
    await perform('核验目录访问权限', async () => {
      if (!native.current || !workspaceId) return;
      const result = nativeValue(await native.current.call('restore_workspace_access', {workspace_id: workspaceId}));
      if (result) {if (result.output_folder) setOutput(result.output_folder); refresh(); setDetailRevision(value => value + 1);}
    });
  }

  async function replaceInput() {
    await perform('更换来源文件夹', async () => {
      if (!native.current || !workspaceId) return;
      const folder = nativeValue(await native.current.call('choose_workspace_input', {}));
      if (folder) {accept(await api.setWorkspaceFolders(workspaceId, {input_folder_id: folder.folder_id})); refresh(); setDetailRevision(value => value + 1);}
    });
  }

  async function select(selection: SelectionWrite) {
    if (!workspaceId) return;
    await perform('更新选择', async () => {
      const result = await api.selectWorkspace(workspaceId, {...selection, q: query, filter});
      setHiddenSelection(result.hidden_count);
      setWorkspace(current => current ? {...current, selected: result.count, selection_id: result.selection_id} : current);
      refresh();
    });
  }

  async function mutate(label: string, operation: () => Promise<WorkspaceSummary>, updateDetail = false) {
    await perform(label, async () => {accept(await operation()); refresh(); if (updateDetail) setDetailRevision(value => value + 1);});
  }

  function chooseWorkspace(value: WorkspaceSummary) {
    accept(value); setItems([]); setActiveId(null); setDetail(null); setPage(1); setFilter('all'); setQuery('');
    setOutput(value.output_folder || null); setError('');
  }

  const changeQuery = useCallback((value: string) => {setQuery(value); setPage(1);}, []);
  const changeFilter = useCallback((value: WorkspaceFilter) => {setFilter(value); setPage(1);}, []);
  return {api, native, config, preferences, workspaces, workspace, items, matchedTotal, query, filter, page, activeId, detail, detailLoading, loading, error, pending, safety, hiddenSelection, output: workspace ? workspace.output_folder || null : output, accept, refresh, perform, nativeValue, mutate, importFolder, chooseOutput, restoreAccess, replaceInput, select, chooseWorkspace, setError, setActiveId, setQuery: changeQuery, setFilter: changeFilter, setPage};
}

export type WorkspaceModel = ReturnType<typeof useWorkspace>;
