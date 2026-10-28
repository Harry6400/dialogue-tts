import {useState} from 'react';
import {ApiError} from '../services/api';
import type {ReportSaveReceipt, WorkspaceReport} from '../types/contracts';
import {shortPath} from './format';
import {messageOf, type WorkspaceModel} from './useWorkspace';

type ReportState = 'idle' | 'creating' | 'saving' | 'cleaning' | 'saved' | 'cancelled' | 'error' | 'unknown';
const incompletePhases = new Set(['enumerating', 'reading', 'pairing', 'validating', 'cancelled', 'recovery_scan', 'scan_error', 'finished']);
function validManifest(report: WorkspaceReport, workspaceId: string) {
  return report && report.workspace_id === workspaceId && /^[a-f0-9]{32}$/.test(report.report_id) && /^[a-f0-9]{64}$/.test(report.sha256)
    && typeof report.filename === 'string' && /^[^/\\\0]+\.csv$/i.test(report.filename) && report.scope === 'whole_workspace' && report.schema_version === 1
    && [report.rows, report.revision, report.items, report.sources].every(value => Number.isSafeInteger(value) && value >= 0)
    && Number.isSafeInteger(report.size) && report.size > 0 && report.size <= 256 * 1024 * 1024;
}
function matchingReceipt(receipt: ReportSaveReceipt, report: WorkspaceReport) {
  return receipt?.ok === true && receipt.report_id === report.report_id && receipt.size === report.size && receipt.sha256 === report.sha256
    && typeof receipt.save_id === 'string' && /^[a-f0-9]{32}$/.test(receipt.save_id) && typeof receipt.name === 'string' && /^[^/\\\0]+\.csv$/i.test(receipt.name)
    && typeof receipt.path === 'string' && receipt.path.length > 0;
}

export function useWorkspaceReport(model: WorkspaceModel) {
  const [manifest, setManifest] = useState<WorkspaceReport | null>(null);
  const [batchName, setBatchName] = useState('');
  const [receipt, setReceipt] = useState<ReportSaveReceipt | null>(null);
  const [state, setState] = useState<ReportState>('idle');
  const [message, setMessage] = useState('');
  const [creationUnknown, setCreationUnknown] = useState(false);
  const workspace = model.workspace;
  const canCreate = !!workspace && !['scanning', 'cancelled', 'error'].includes(workspace.status) && !incompletePhases.has(workspace.phase);
  const busy = ['creating', 'saving', 'cleaning'].includes(state);

  async function discard(report: WorkspaceReport) {
    const result = await model.api.discardWorkspaceReport(report.report_id);
    if (result.ok !== true || result.report_id !== report.report_id) throw new Error('临时 CSV 取消回执无效，请重试取消。');
    setManifest(null);
  }
  async function save(report: WorkspaceReport) {
    setState('saving'); setMessage('请在系统面板选择 CSV 保存位置。');
    const result = model.native.current ? await model.native.current.call('save_workspace_report', {report_id: report.report_id}) : {kind: 'unsupported' as const};
    if (result.kind === 'cancelled') {
      try {await discard(report); setState('cancelled'); setMessage('已取消保存，本次临时 CSV 已取消。');}
      catch (issue) {setState('error'); setMessage('保存已取消，但临时清单尚未清理。' + messageOf(issue));}
      return;
    }
    if (result.kind !== 'success') {
      setState(result.kind === 'error' ? 'error' : 'unknown');
      setMessage(result.kind === 'unsupported' ? '请在对白桌面应用中保存。当前清单快照已保留。' : ('message' in result ? result.message : '无法确认保存结果。') + ' 清单快照已保留，可核对后重试保存。');
      return;
    }
    if (!matchingReceipt(result.value, report)) {setState('unknown'); setMessage('保存回执与本次清单不一致，无法确认保存；快照已保留，请先核对所选位置。'); return;}
    setReceipt(result.value);
    try {await discard(report); setState('saved'); setMessage('已保存并核验 ' + result.value.name + '。');}
    catch (issue) {setState('error'); setMessage('CSV 已保存并核验，但临时清单尚未清理。' + messageOf(issue));}
  }
  async function createAndSave() {
    if (!workspace || !canCreate || manifest || creationUnknown) return;
    await model.perform('导出批次 CSV 清单', async () => {
      setState('creating'); setMessage('正在创建整个批次的 CSV 快照…'); setReceipt(null);
      try {
        const report = await model.api.createWorkspaceReport(workspace.id);
        if (!validManifest(report, workspace.id)) {setCreationUnknown(true); setState('unknown'); setMessage('清单信息未通过核验。请先取消未保存的临时 CSV 清单，再重新导出。'); return;}
        setManifest(report); setBatchName(shortPath(workspace.input_folder.path));
        await save(report);
      } catch (issue) {
        const unknown = issue instanceof ApiError && issue.outcome === 'unknown';
        setCreationUnknown(unknown); setState(unknown ? 'unknown' : 'error');
        setMessage(unknown ? '创建结果未知，可能已留下临时 CSV。请先取消未保存的临时 CSV 清单，再重新导出。' : messageOf(issue));
      }
    });
  }
  async function retrySave() {
    if (!manifest || receipt) return;
    await model.perform('重试保存 CSV 清单', async () => {
      try {await save(manifest);} catch (issue) {setState('unknown'); setMessage(messageOf(issue) + ' 当前清单快照已保留。');}
    });
  }
  async function cancelCurrent() {
    if (!manifest) return;
    await model.perform('取消本次临时 CSV 清单', async () => {
      setState('cleaning');
      try {await discard(manifest); setState(receipt ? 'saved' : 'cancelled'); setMessage(receipt ? '已清理本次临时 CSV；已保存文件保留。' : '已取消本次临时 CSV 清单。');}
      catch (issue) {setState('error'); setMessage(messageOf(issue));}
    });
  }
  async function cancelPending() {
    await model.perform('取消未保存的临时 CSV 清单', async () => {
      setState('cleaning');
      try {
        const result = await model.api.cancelPendingReports();
        if (result.ok !== true || !Number.isSafeInteger(result.cancelled) || result.cancelled < 0) throw new Error('临时 CSV 取消回执无效，请重新核对。');
        setManifest(null); setReceipt(null); setCreationUnknown(false); setState('cancelled'); setMessage('已取消 ' + result.cancelled.toLocaleString('zh-CN') + ' 份临时 CSV 清单。');
      } catch (issue) {setState('error'); setMessage(messageOf(issue));}
    });
  }
  return {manifest, batchName, receipt, state, message, creationUnknown, canCreate, busy, createAndSave, retrySave, cancelCurrent, cancelPending};
}

export type WorkspaceReportController = ReturnType<typeof useWorkspaceReport>;
