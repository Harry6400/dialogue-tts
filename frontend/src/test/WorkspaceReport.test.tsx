import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {ApiError} from '../services/api';
import {createNativeBridge} from '../services/native';
import {ReportContent} from '../app/ReportContent';
import {useWorkspaceReport} from '../app/useWorkspaceReport';
import type {WorkspaceModel} from '../app/useWorkspace';
import type {ReportSaveReceipt, WorkspaceReport} from '../types/contracts';

const workspaceId = 'a'.repeat(32);
const manifest: WorkspaceReport = {report_id: 'b'.repeat(32), filename: '对白-批次清单-bbbbbbbb.csv', workspace_id: workspaceId, rows: 120000, items: 60000, sources: 120000, size: 1024, revision: 8, sha256: 'c'.repeat(64), scope: 'whole_workspace', schema_version: 1, content_type: 'text/csv; charset=utf-8'};
const receipt: ReportSaveReceipt = {ok: true, save_id: 'd'.repeat(32), report_id: manifest.report_id, name: '我选择的清单.csv', path: '/fictional/chosen.csv', size: manifest.size, sha256: manifest.sha256};
const bridges: ReturnType<typeof createNativeBridge>[] = [];
afterEach(() => {for (const bridge of bridges.splice(0)) bridge.dispose();});
function setup(options: {result?: unknown; saveRejects?: boolean; cleanupFails?: boolean; createFails?: ApiError; phase?: string} = {}) {
  const create = vi.fn().mockResolvedValue(manifest);
  if (options.createFails) create.mockRejectedValueOnce(options.createFails);
  const discard = vi.fn().mockResolvedValue({ok: true, report_id: manifest.report_id});
  if (options.cleanupFails) discard.mockRejectedValueOnce(new Error('临时 CSV 暂不可清理'));
  const cancelPending = vi.fn().mockResolvedValue({ok: true, cancelled: 4});
  const save = vi.fn().mockResolvedValue(options.result ?? receipt);
  if (options.saveRejects) save.mockRejectedValueOnce(new Error('reply lost'));
  const bridge = createNativeBridge({pywebview: {api: {save_workspace_report: save}}}); bridges.push(bridge);
  const model = {workspace: {id: workspaceId, status: 'ready', phase: options.phase || 'ready', input_folder: {path: '/fictional/会议室来源'}}, api: {createWorkspaceReport: create, discardWorkspaceReport: discard, cancelPendingReports: cancelPending}, native: {current: bridge}, perform: vi.fn(async (_label: string, work: () => Promise<void>) => work())} as unknown as WorkspaceModel;
  function Subject() {const reports = useWorkspaceReport(model); return <ReportContent reports={reports} workspaceId={workspaceId}/>;}
  render(<Subject/>);
  return {create, discard, cancelPending, save};
}
function exportNow() {fireEvent.click(screen.getByRole('button', {name: '导出并选择保存位置'}));}

it('explains whole-batch scope and discards only after a matching native save receipt', async () => {
  const {create, save, discard} = setup();
  expect(screen.getByText(/不受当前搜索、筛选或勾选范围影响/)).toBeVisible();
  expect(screen.getByText(/每份 XML 一行/)).toBeVisible();
  expect(screen.getByText(/不包含对话正文或完整私人路径/)).toBeVisible();
  expect(screen.queryByRole('button', {name: '取消未保存的临时 CSV 清单'})).not.toBeInTheDocument();
  exportNow();
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已保存并核验 我选择的清单.csv'));
  expect(create).toHaveBeenCalledExactlyOnceWith(workspaceId);
  expect(save).toHaveBeenCalledExactlyOnceWith(manifest.report_id);
  expect(discard).toHaveBeenCalledExactlyOnceWith(manifest.report_id);
});

it('distinguishes a cancelled save and discards its exact temporary report', async () => {
  const {discard} = setup({result: {cancelled: true}}); exportNow();
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已取消保存，本次临时 CSV 已取消'));
  expect(discard).toHaveBeenCalledExactlyOnceWith(manifest.report_id);
  expect(screen.queryByRole('button', {name: '重试保存同一快照'})).not.toBeInTheDocument();
});

it.each(['failure', 'unknown'])('retains the same snapshot after %s and retries only its native save', async kind => {
  const {create, save, discard} = setup(kind === 'failure' ? {result: {ok: false, error: '保存目录不可写'}} : {saveRejects: true});
  exportNow(); await screen.findByRole('alert');
  expect(discard).not.toHaveBeenCalled();
  expect(screen.getByText('120,000 行 · 60,000 通 · 120,000 份 XML')).toBeVisible();
  save.mockResolvedValueOnce(receipt);
  fireEvent.click(screen.getByRole('button', {name: '重试保存同一快照'}));
  await waitFor(() => expect(discard).toHaveBeenCalledOnce());
  expect(create).toHaveBeenCalledOnce();
  expect(save.mock.calls).toEqual([[manifest.report_id], [manifest.report_id]]);
});

it.each([{report_id: 'e'.repeat(32)}, {size: manifest.size + 1}, {sha256: 'f'.repeat(64)}])('keeps the snapshot when native receipt fields do not match %o', async mismatch => {
  const {discard} = setup({result: {...receipt, ...mismatch}}); exportNow();
  expect(await screen.findByRole('alert')).toHaveTextContent('保存回执与本次清单不一致');
  expect(discard).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: '取消本次临时清单'}));
  await waitFor(() => expect(discard).toHaveBeenCalledExactlyOnceWith(manifest.report_id));
});

it('retries only temporary cleanup after saving was already verified', async () => {
  const {save, discard} = setup({cleanupFails: true}); exportNow();
  expect(await screen.findByRole('alert')).toHaveTextContent('CSV 已保存并核验');
  expect(screen.queryByRole('button', {name: '重试保存同一快照'})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: '重试清理本次临时清单'}));
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已清理本次临时 CSV'));
  expect(save).toHaveBeenCalledOnce(); expect(discard).toHaveBeenCalledTimes(2);
});

it.each([new ApiError('提交结果未知', null, 'unknown'), new ApiError('已有 4 份未处理报告', 400)])('offers explicit temporary-CSV cleanup after a lost report ID or quota failure', async createFails => {
  const {create, cancelPending, save} = setup({createFails}); exportNow();
  await screen.findByRole('alert'); expect(save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: '取消未保存的临时 CSV 清单'}));
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已取消 4 份临时 CSV 清单'));
  expect(cancelPending).toHaveBeenCalledExactlyOnceWith();
  expect(create).toHaveBeenCalledOnce();
  expect(screen.getByRole('button', {name: '导出并选择保存位置'})).toBeEnabled();
});

it('disables new snapshots after a batch is finished but leaves temporary CSV cancellation available', () => {
  setup({phase: 'finished'});
  expect(screen.getByRole('button', {name: '导出并选择保存位置'})).toBeDisabled();
  expect(screen.getByRole('button', {name: '取消未保存的临时 CSV 清单'})).toBeEnabled();
});
