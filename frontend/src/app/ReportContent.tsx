import type {WorkspaceReportController} from './useWorkspaceReport';
import {Icon} from './Icon';
import {count} from './format';
import styles from './ReportContent.module.css';

interface Props {reports: WorkspaceReportController; workspaceId?: string; cleanupOnly?: boolean}
function ReportFeedback({reports}: {reports: WorkspaceReportController}) {
  if (!reports.message) return null;
  const problem = reports.state === 'error' || reports.state === 'unknown';
  return <p className={`${styles.feedback} ${problem ? styles.error : ''}`} role={problem ? 'alert' : 'status'}><Icon name={problem ? 'warning' : reports.state === 'saved' ? 'check' : 'info'}/><span>{reports.message}</span></p>;
}
function ExportActions({reports}: {reports: WorkspaceReportController}) {
  if (reports.manifest && reports.receipt) return <div className={styles.actions}><button className={styles.primary} disabled={reports.busy} onClick={() => void reports.cancelCurrent()}>重试清理本次临时清单</button></div>;
  if (reports.manifest) return <div className={styles.actions}><button className={styles.secondary} disabled={reports.busy} onClick={() => void reports.cancelCurrent()}>取消本次临时清单</button><button className={styles.primary} disabled={reports.busy} onClick={() => void reports.retrySave()}><Icon name="download"/>重试保存同一快照</button></div>;
  return <div className={styles.actions}><button className={styles.primary} disabled={reports.busy || !reports.canCreate || reports.creationUnknown} onClick={() => void reports.createAndSave()}><Icon name="download"/>{reports.state === 'creating' ? '正在创建…' : '导出并选择保存位置'}</button></div>;
}
export function ReportContent({reports, workspaceId, cleanupOnly = false}: Props) {
  const showRecovery = cleanupOnly || reports.creationUnknown || !reports.manifest && (reports.state === 'error' || !reports.canCreate);
  return <div className={styles.content}>
    {!cleanupOnly && <>
      <div className={styles.scope}><Icon name="file"/><div><strong>导出整个批次</strong><p>包含每份 XML 的文件名、识别角色、结果名称、状态与异常原因，每份 XML 一行。</p></div></div>
      <p>不受当前搜索、筛选或勾选范围影响。CSV 不包含对话正文或完整私人路径。</p>
      {reports.manifest && <div className={styles.snapshot}><div><span>清单所属批次</span><strong>{reports.batchName}</strong></div><div><span>固定快照</span><strong>{count(reports.manifest.rows)} 行 · {count(reports.manifest.items)} 通 · {count(reports.manifest.sources)} 份 XML</strong></div><p>重试保存使用同一份快照，不会重新导出。</p>{reports.manifest.workspace_id !== workspaceId && <p className={styles.error}>这是其他批次保留的清单。请先保存或取消，再导出当前批次。</p>}</div>}
      {!reports.canCreate && !reports.manifest && <p className={styles.hint}>当前批次的索引尚未完整或已清理，暂不能导出。</p>}
      <ReportFeedback reports={reports}/>
      <ExportActions reports={reports}/>
    </>}
    {showRecovery && <section className={cleanupOnly ? styles.recoveryOnly : styles.recovery} aria-label="临时 CSV 清单">
      <h3>临时 CSV 清单</h3>
      <p>刷新后找不到待保存清单，或提示清单数量已达上限时，可取消本次应用会话的临时 CSV，再重新导出。</p>
      <p className={styles.hint}>仅清理临时 CSV 快照；任务、原始 XML、MP3 和已保存的 CSV 均保留。</p>
      {cleanupOnly && <ReportFeedback reports={reports}/>}
      <button className={styles.cleanup} disabled={reports.busy} onClick={() => void reports.cancelPending()}>取消未保存的临时 CSV 清单</button>
    </section>}
  </div>;
}
