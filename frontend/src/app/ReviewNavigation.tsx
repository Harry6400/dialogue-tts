import {useEffect, useRef} from 'react';
import type {WorkspaceModel} from './useWorkspace';
import type {WorkbenchUi} from './useWorkbench';
import {Dialog} from './Dialog';
import {WorkspaceTable} from './WorkspaceTable';
import {Icon} from './Icon';
import {count} from './format';
import styles from './App.module.css';

export function ReviewNavigation({model,ui}: {model:WorkspaceModel;ui:WorkbenchUi}) {
  const trigger=useRef<HTMLButtonElement>(null);
  const index=model.items.findIndex(item=>item.id===model.activeId);
  const position=index<0?0:(model.page-1)*100+index+1;
  useEffect(()=>{
    if (!ui.compactReview || !model.issueFocus || model.issueFocus!==model.activeId) return;
    trigger.current?.focus(); model.consumeIssueFocus();
  },[ui.compactReview,model.issueFocus,model.activeId,model.consumeIssueFocus]);
  return <>
    <nav hidden={!ui.compactReview} className={styles.reviewNavigation} aria-label="核对通话导航">
      <button ref={trigger} className={styles.textButton} onClick={()=>ui.setCallPickerOpen(true)} aria-haspopup="dialog" aria-expanded={ui.callPickerOpen}><Icon name="file"/>切换通话<Icon name="chevron"/></button>
      <span>{count(position)} / {count(model.matchedTotal)}</span>
      <button className={styles.iconButton} aria-label="上一通" disabled={model.loading||position<=1} onClick={()=>model.navigateCall(-1)}><Icon name="back"/></button>
      <button className={styles.iconButton} aria-label="下一通" disabled={model.loading||position===0||position>=model.matchedTotal} onClick={()=>model.navigateCall(1)}><Icon name="arrow"/></button>
    </nav>
    {ui.callPickerOpen && <Dialog title="切换通话" onClose={()=>ui.setCallPickerOpen(false)}>
      <div className={styles.callPicker}><WorkspaceTable model={model} context="picker" onReview={()=>ui.setCallPickerOpen(false)} onActivate={()=>ui.setCallPickerOpen(false)} onClearFilters={ui.clearFilters}/></div>
    </Dialog>}
  </>;
}
