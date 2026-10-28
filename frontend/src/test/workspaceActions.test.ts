import {describe, expect, it} from 'vitest';
import {blocksUpdate} from '../app/operation';
import {workspaceActions} from '../app/workspaceActions';
import type {ActionContext} from '../app/workspaceActions';
import type {WorkspaceSummary} from '../types/contracts';

const output = {folder_id: 'output', path: '/fictional/output', valid: true};
const base = {workspace: {status: 'ready', phase: 'ready', has_run_plan: false, selected: 2, ready_selected: 1, selection_id: 'fixed', input_folder: {valid: true}} as WorkspaceSummary, output, busy: false, loading: false, safety: {busy: false, saving: false, pending: false, unsaved: false}} satisfies ActionContext;
const decide = (patch: Partial<ActionContext> = {}, workspace: Partial<WorkspaceSummary> = {}) => workspaceActions({...base, ...patch, workspace: {...base.workspace, ...workspace}});

describe('shared workspace action decisions', () => {
  it('requires actual selection readiness, directory grants and known global state', () => {
    expect(decide()).toMatchObject({kind: 'start', enabled: true, label: '生成 1 通'});
    for (const patch of [{output: null}, {safety: null}, {busy: true}, {loading: true}]) expect(decide(patch).enabled).toBe(false);
    expect(decide({}, {ready_selected: 0}).enabled).toBe(false);
    expect(decide({}, {phase: 'finished'}).enabled).toBe(false);
    expect(decide({}, {status: 'error'}).enabled).toBe(false);
    expect(decide({}, {has_unsaved_audio: true}).enabled).toBe(false);
  });
  it('keeps recovery checking and scan/settings recovery offline without disabling explicit synthesis resume', () => {
    expect(decide({output: null}, {status: 'interrupted', phase: 'recovery_checking', input_folder: {...base.workspace.input_folder, valid: false}})).toMatchObject({kind: 'restore', enabled: true, offline: true});
    for (const phase of ['recovery_scan', 'recovery_settings']) {
      expect(decide({output: null}, {status: 'interrupted', phase})).toMatchObject({kind: 'resume_offline', enabled: true});
      expect(decide({}, {status: 'interrupted', phase, input_folder: {...base.workspace.input_folder, valid: false}}).enabled).toBe(false);
    }
    expect(decide({}, {status: 'paused', phase: 'generating', has_run_plan: true})).toMatchObject({kind: 'resume', enabled: true, offline: false});
    expect(decide({}, {status: 'interrupted', phase: 'recovery', has_run_plan: false})).toMatchObject({kind: 'start', enabled: true, resumable: false});
    expect(decide({}, {status: 'interrupted', phase: 'recovery_scan', has_run_plan: true})).toMatchObject({kind: 'resume', enabled: true, offline: false});
  });
  it('permits output-only save retry without consent and prevents synthesis or unknown-state retries', () => {
    const w = {status: 'paused', has_run_plan: true, input_folder: {...base.workspace.input_folder, valid: false}, save_pending: 1, synthesis_error: 1} as Partial<WorkspaceSummary>;
    expect(decide({}, w)).toMatchObject({retrySave: true, retrySynthesis: false});
    expect(decide({safety: null}, w).retrySave).toBe(false);
    expect(decide({output: null}, w).retrySave).toBe(false);
    expect(decide({}, {...w, has_run_plan: false}).retrySave).toBe(false);
    expect(decide({}, {...w, status: 'running'})).toMatchObject({retrySave: false, retrySynthesis: false});
    expect(decide({}, {...w, counts: {unverified: 1}})).toMatchObject({enabled: false, retrySave: false, retrySynthesis: false});
    expect(decide({safety: {...base.safety, busy: 0} as unknown as ActionContext['safety']}, w).retrySave).toBe(false);
  });
  it('allows stopping its own active work while refusing a second pause request', () => {
    expect(decide({}, {status: 'scanning'})).toMatchObject({kind: 'cancel', enabled: true});
    expect(decide({}, {status: 'running'})).toMatchObject({kind: 'pause', enabled: true});
    expect(decide({}, {status: 'pausing'})).toMatchObject({kind: 'pause', enabled: false});
  });
});

it('uses operation kinds even when translated labels coincide', () => {
  expect(blocksUpdate({kind: 'update_check', label: '核验'})).toBe(false);
  expect(blocksUpdate({kind: 'update_install', label: '新文案'})).toBe(false);
  expect(blocksUpdate({kind: 'report', label: '检查更新'})).toBe(true);
  expect(blocksUpdate({kind: 'access', label: '准备更新'})).toBe(true);
  expect(blocksUpdate(null)).toBe(false);
});
