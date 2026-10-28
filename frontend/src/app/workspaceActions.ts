import type {Folder, WorkspaceSafety, WorkspaceSummary} from '../types/contracts';
import {count} from './format';

export interface ActionContext {workspace: WorkspaceSummary; output: Folder | null; busy: boolean; loading: boolean; safety: WorkspaceSafety | null}
export type PrimaryAction = 'cancel' | 'pause' | 'restore' | 'resume_offline' | 'resume' | 'start';
const stopped = new Set(['paused', 'interrupted', 'done']);
const offlineLabels: Record<string, string> = {recovery_scan: '继续扫描', recovery_settings: '继续更新设置', recovery_checking: '继续核验'};

/** Both the run controls and controller use this decision, including recovery gates. */
export function workspaceActions({workspace: w, output, busy, loading, safety}: ActionContext) {
  const running = ['scanning', 'running', 'pausing'].includes(w.status);
  const interrupted = ['paused', 'interrupted'].includes(w.status);
  const offline = interrupted && !!offlineLabels[w.phase] && (w.phase !== 'recovery_scan' || w.has_run_plan === false);
  const resumable = interrupted && (offline || w.has_run_plan === true);
  const inputValid = w.input_folder.valid !== false;
  const outputValid = !!output && output.valid !== false;
  const restoredAccess = inputValid && (!output || output.valid !== false);
  const safe = !!safety && ['busy', 'saving', 'pending', 'unsaved'].every(key => typeof safety[key as keyof WorkspaceSafety] === 'boolean') && !safety.busy && !safety.saving && !safety.pending;
  const unverified = (w.counts?.unverified || 0) > 0;
  const idle = !busy && !loading && safe && !running;
  let kind: PrimaryAction = resumable ? 'resume' : 'start';
  if (w.status === 'scanning') kind = 'cancel';
  else if (w.status === 'running' || w.status === 'pausing') kind = 'pause';
  else if (offline) kind = w.phase === 'recovery_checking' ? 'restore' : 'resume_offline';
  const cached = !!w.has_unsaved_audio || w.save_pending > 0;
  const startStatus = ['ready', 'done'].includes(w.status) || interrupted && w.has_run_plan === false;
  const eligible = w.phase !== 'finished' && !offlineLabels[w.phase] && !cached && startStatus && !!w.ready_selected && !!w.selection_id;
  const enabled = kind === 'cancel' ? !busy : kind === 'pause' ? !busy && w.status !== 'pausing' :
    idle && (offline ? w.phase === 'recovery_checking' || inputValid : !unverified && restoredAccess && outputValid && (resumable || eligible));
  let help = '';
  if (w.status === 'scanning') help = '扫描完成后可生成';
  else if (w.phase === 'finished') help = '此批次已结束，可新建批次';
  else if (w.status === 'cancelled') help = '扫描已取消，请重新导入文件夹';
  else if (!safe) help = '正在核验或处理其他任务，请稍候';
  else if (!restoredAccess && w.phase !== 'recovery_checking') help = '请先恢复目录访问权限';
  else if (offline) help = '离线检查不会发送正文';
  else if (unverified) help = '请先恢复目录并核验已有结果';
  else if (!outputValid) help = '请选择有效的结果文件夹';
  else if (cached && !resumable) help = '请先保存已有的完整音频';
  else if (!w.selected && !resumable) help = '勾选需要生成的通话';
  else if (!w.ready_selected && !resumable) help = '所选通话当前均不可生成，请重新选择合格通话';
  const label = offline ? offlineLabels[w.phase] : resumable ? '继续生成' : `生成 ${count(w.ready_selected)} 通`;
  const canRetry = idle && !unverified && stopped.has(w.status) && w.has_run_plan === true && w.phase !== 'finished';
  return {kind, enabled, label, help, running, resumable, offline, restoredAccess, needsVerification: unverified,
    retrySave: canRetry && outputValid && !!(w.save_error || w.save_pending),
    retrySynthesis: canRetry && restoredAccess && outputValid && !!w.synthesis_error};
}
