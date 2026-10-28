import {Icon} from './Icon';
import {count} from './format';
import {issueCategories} from './issues';
import type {WorkspaceModel} from './useWorkspace';
import styles from './IssuePanel.module.css';

export function IssuePanel({model}: {model: WorkspaceModel}) {
  const workspace = model.workspace;
  if (!workspace || model.filter !== 'error') return null;
  const guidance = issueCategories.find(category => category.key === model.issue)?.guidance || '点击通话查看具体原因，或用“下一问题”逐项定位。';
  return <section className={styles.panel} aria-label="问题分类">
    <div className={styles.heading}><strong>需处理问题</strong><span>本批次 {count(workspace.issues.total)} 通</span><button className={styles.next} disabled={!!model.pending || model.loading || !model.matchedTotal} onClick={() => void model.nextIssue()}><span>下一问题</span><Icon name="arrow"/></button></div>
    <div className={styles.categories} aria-label="按原因筛选"><button aria-pressed={model.issue === ''} aria-label={`筛选全部问题，${count(workspace.issues.total)} 通`} onClick={() => model.setIssue('')}>全部问题<span>{count(workspace.issues.total)}</span></button>{issueCategories.map(category => <button key={category.key} aria-pressed={model.issue === category.key} aria-label={`筛选${category.label}，${count(workspace.issues[category.key])} 通`} onClick={() => model.setIssue(category.key)}>{category.label}<span>{count(workspace.issues[category.key])}</span></button>)}</div>
    <p className={styles.guidance}>{guidance}</p>
    {model.issueNotice && <p className={styles.notice} role="status">{model.issueNotice}</p>}
  </section>;
}
