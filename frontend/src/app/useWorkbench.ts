import {useCallback, useEffect, useRef, useState} from 'react';
import type {JobSnapshot, SoundSettings} from '../types/contracts';
import {requestId} from '../services/api';
import type {Modal} from './AppDialogs';
import {isWorking, messageOf, type WorkspaceModel} from './useWorkspace';
import {useApplicationUpdate} from './useApplicationUpdate';
import {useWorkspaceReport} from './useWorkspaceReport';
type SafetyHost = Window & {dialogueCloseSafety?: () => object; dialogueUpdateSafety?: () => object; dialogueShowUnsaved?: () => void; dialogueUpdateLock?: (value: boolean) => void; dialogueCheckForUpdates?: () => void};
const defaultSettings: SoundSettings = {voices: {AGENT: 'zh-CN-XiaoxiaoNeural', USER: 'zh-CN-XiaoyiNeural'}, rate: 1, pause_ms: 250, auto_voice: true, manual_user_voice: false};
export function useWorkbench(model: WorkspaceModel) {
  const {workspace, config} = model;
  const [mode, setMode] = useState<'overview' | 'review'>('overview');
  const [scope, setScope] = useState<'batch' | 'call'>('batch');
  const [settingsOpen, setSettingsOpen] = useState(true);
  const [narrowOpen, setNarrowOpen] = useState(false);
  const [modal, setModal] = useState<Modal | null>(null);
  const [consent, setConsent] = useState(false);
  const [theme, setTheme] = useState<'light' | 'dark'>('light');
  const [search, setSearch] = useState('');
  const [previewOpen, setPreviewOpen] = useState(true);
  const auditionSettings = useRef<SoundSettings>(defaultSettings);
  const [auditionJob, setAuditionJob] = useState<JobSnapshot | null>(null);
  const [updateLocked, setUpdateLocked] = useState(false);
  const exitRequested = useRef(false);
  const settingsTrigger = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const auditionRunning = !!auditionJob && ['queued', 'running'].includes(auditionJob.status);
  const busy = !!model.pending || updateLocked || auditionRunning;
  const running = isWorking(workspace);
  const updates = useApplicationUpdate(model, busy, running);
  const reports = useWorkspaceReport(model);
  const updateSafetyBusy = !!model.pending && !['准备更新','检查更新'].includes(model.pending);
  const restoredAccess = !!workspace && workspace.input_folder.valid !== false && model.output?.valid !== false;

  useEffect(() => {if (model.preferences?.theme) {setTheme(model.preferences.theme); document.documentElement.dataset.theme = model.preferences.theme;}}, [model.preferences?.theme]);
  useEffect(() => {const timer = setTimeout(() => {if (model.query !== search) model.setQuery(search);}, 250); return () => clearTimeout(timer);}, [search, model.query, model.setQuery]);

  useEffect(() => {
    const host = window as SafetyHost;
    const previous = host.dialogueCheckForUpdates;
    host.dialogueCheckForUpdates = () => {setNarrowOpen(false); setModal('settings'); void updates.check();};
    return () => {host.dialogueCheckForUpdates = previous;};
  }, [updates.check]);

  useEffect(() => {
    const host = window as SafetyHost;
    const originals = {close: host.dialogueCloseSafety, update: host.dialogueUpdateSafety, show: host.dialogueShowUnsaved, lock: host.dialogueUpdateLock};
    // This page has no legacy temporary results. Native code checks persistent
    // workspace caches separately; unknown task/save state still blocks close.
    host.dialogueCloseSafety = () => ({busy: busy || running || (model.safety?.busy ?? true), pending: busy || (model.safety?.pending ?? true), running: running || auditionRunning || (model.safety?.busy ?? true), saving: model.safety?.saving ?? true, unsaved: false, persistent_workspace: true});
    host.dialogueUpdateSafety = () => ({busy: updateSafetyBusy || running || auditionRunning || (model.safety?.busy ?? true) || (model.safety?.pending ?? true), unsaved: model.safety?.unsaved ?? true, saving: model.safety?.saving ?? true, persistent_workspace: true});
    host.dialogueShowUnsaved = () => {model.setFilter('error'); setMode('overview');};
    host.dialogueUpdateLock = setUpdateLocked;
    return () => {host.dialogueCloseSafety = originals.close; host.dialogueUpdateSafety = originals.update; host.dialogueShowUnsaved = originals.show; host.dialogueUpdateLock = originals.lock;};
  }, [busy, updateSafetyBusy, running, auditionRunning, workspace?.phase, model.setFilter, model.safety]);

  const auditionId = auditionJob?.id;
  useEffect(() => {
    if (!auditionId || !auditionRunning) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {const result = await model.api.getJob(auditionId!); if (alive) setAuditionJob(result);}
      catch (issue) {if (alive) model.setError(messageOf(issue));}
      finally {if (alive) timer = setTimeout(() => void poll(), 1000);}
    }
    timer = setTimeout(() => void poll(), 600);
    return () => {alive = false; clearTimeout(timer);};
  }, [auditionId, auditionRunning, model.api, model.setError]);

  const native = model.native;
  const reportError = model.setError;
  const requestClose = useCallback(async () => {
    if (!native.current) return;
    const result = await native.current.call('request_close', {});
    if (result.kind === 'error' || result.kind === 'unknown') reportError(result.message);
    else if (result.kind === 'unsupported') reportError('请使用桌面应用窗口退出。');
  }, [native, reportError]);

  useEffect(() => {
    if (!exitRequested.current || !workspace || !['paused', 'interrupted', 'ready', 'done', 'cancelled', 'error'].includes(workspace.status)) return;
    exitRequested.current = false;
    void requestClose();
  }, [workspace?.status, workspace, requestClose]);

  useEffect(() => {
    function keyboard(event: KeyboardEvent) {
      if (event.defaultPrevented) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'f' && !modal) {event.preventDefault(); searchRef.current?.focus();}
      if (event.key === 'Escape' && !modal && !narrowOpen && mode === 'review' && !(event.target instanceof HTMLSelectElement)) setMode('overview');
    }
    document.addEventListener('keydown', keyboard);
    return () => document.removeEventListener('keydown', keyboard);
  }, [mode, modal, narrowOpen]);

  async function changeTheme() {
    const next = theme === 'light' ? 'dark' : 'light'; setTheme(next); document.documentElement.dataset.theme = next;
    if (model.native.current) {
      const result = await model.native.current.call('set_preferences', {...(workspace?.settings || defaultSettings), theme: next});
      if (result.kind === 'error' || result.kind === 'unknown') model.setError(result.message);
    }
  }
  async function pauseAndExit() {
    if (!workspace || !running) return requestClose();
    exitRequested.current = true;
    if (workspace.status === 'scanning') await model.mutate('停止扫描并保存进度', () => model.api.cancelWorkspaceScan(workspace.id));
    else await model.mutate('保存进度并退出', () => model.api.pauseWorkspace(workspace.id));
  }
  async function startOrResume() {
    if (!workspace) return;
    if (workspace.phase === 'recovery_checking') return model.restoreAccess();
    if ((!offlineResume && (!consent || !model.output)) || !restoredAccess) return;
    if (['paused', 'interrupted'].includes(workspace.status)) await model.mutate('继续生成', () => model.api.resumeWorkspace(workspace.id, offlineResume));
    else if (workspace.selection_id) await model.mutate('开始生成', () => model.api.startWorkspace(workspace.id, {selection_id: workspace.selection_id!, output_folder_id: model.output!.folder_id, consent: true, request_id: requestId()}));
    setConsent(false);
  }
  function clearFilters() {setSearch(''); model.setQuery(''); model.setFilter('all');}
  function resetView() {setConsent(false); setMode('overview'); setSearch('');}
  async function importFolder() {await model.importFolder(); resetView();}
  function review() {setMode('review'); setScope('call');}
  const resumable = !!workspace && ['paused', 'interrupted'].includes(workspace.status);
  const offlineResume = resumable && ['recovery_scan','recovery_settings','recovery_checking'].includes(workspace?.phase || '');
  async function startAudition() {await model.perform('创建试听', async () => {setAuditionJob(await model.api.audition(auditionSettings.current, requestId()));});}
  return {workspace, config, mode, setMode, scope, setScope, settingsOpen, setSettingsOpen, modal, setModal, consent, setConsent, theme, search, setSearch, previewOpen, setPreviewOpen, auditionSettings, auditionJob, setAuditionJob, auditionRunning, busy, running, restoredAccess, settingsTrigger, searchRef, narrowOpen, setNarrowOpen, updateLocked, importFolder, resetView, changeTheme, review, startOrResume, pauseAndExit, resumable, startAudition, clearFilters, updates, reports};
}

export type WorkbenchUi = ReturnType<typeof useWorkbench>;
