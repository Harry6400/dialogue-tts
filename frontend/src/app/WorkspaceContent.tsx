import type {WorkspaceModel} from './useWorkspace';
import type {WorkbenchUi} from './useWorkbench';
import {ReviewNavigation} from './ReviewNavigation';
import {WorkspaceSearch} from './WorkspaceToolbar';
import {SelectionToolbar} from './SelectionToolbar';
import {WorkspaceTable,WorkspaceEmptyState} from './WorkspaceTable';
import {IssuePanel} from './IssuePanel';
import {Reader} from './Reader';
import styles from './App.module.css';

function ReviewBody({model,ui}: {model:WorkspaceModel;ui:WorkbenchUi}) {
  if (ui.compactReview && !model.loading && !model.items.length) return <section className={styles.reader} aria-label="完整核对"><WorkspaceEmptyState model={model} onClearFilters={ui.clearFilters}/></section>;
  return <Reader model={model} active={ui.mode==='review'} onSources={()=>ui.setModal('sources')} onMatchHelp={()=>ui.setModal('match')}/>;
}

export function WorkspaceContent({model,ui}: {model:WorkspaceModel;ui:WorkbenchUi}) {
  const {mode,review}=ui;
  return <div className={styles.workspaceSurface}>
        <WorkspaceSearch model={model} ui={ui}/>
        <IssuePanel model={model}/>
        <SelectionToolbar model={model} ui={ui}/>
        <div className={styles.overview} hidden={mode !== 'overview'}><WorkspaceTable model={model} active={mode==='overview'} onReview={review} onActivate={review} onClearFilters={ui.clearFilters}/></div>
        <div className={`${styles.reviewWorkspace} ${ui.compactReview ? styles.compactReview : ''}`} hidden={mode !== 'review'}>{mode === 'review' && <ReviewNavigation model={model} ui={ui}/>}<div className={styles.reviewList} hidden={ui.compactReview}><WorkspaceTable model={model} active={mode === 'review' && !ui.compactReview} onReview={review} onClearFilters={ui.clearFilters} context="review"/></div><ReviewBody model={model} ui={ui}/></div>
        </div>;
}
