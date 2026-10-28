import * as Select from '@radix-ui/react-select';
import type {WorkspaceSummary} from '../types/contracts';
import {Icon} from './Icon';
import {count, shortPath} from './format';
import styles from './TaskSwitcher.module.css';

const statusText: Record<WorkspaceSummary['status'], string> = {scanning: '正在扫描', ready: '准备就绪', running: '正在生成', pausing: '正在保存进度', paused: '已暂停', interrupted: '等待恢复', done: '本次已完成', cancelled: '扫描已取消', error: '需要处理'};
interface Props {workspaces: WorkspaceSummary[]; value?: string; disabled: boolean; onChange: (workspace: WorkspaceSummary) => void}

function Metadata({workspace}: {workspace: WorkspaceSummary}) {
  const tone = workspace.status === 'error' ? 'error' : ['paused', 'interrupted', 'cancelled'].includes(workspace.status) ? 'paused' : ['scanning', 'running', 'pausing', 'done', 'ready'].includes(workspace.status) ? 'active' : 'neutral';
  return <span className={styles.metadata}><span className={styles.dot} data-tone={tone} aria-hidden="true"/><span>{statusText[workspace.status]}</span><span className={styles.total}>{workspace.status === 'scanning' && !workspace.total ? `${count(workspace.scanned)} 份` : `${count(workspace.total)} 通`}</span></span>;
}

export function TaskSwitcher({workspaces, value, disabled, onChange}: Props) {
  const current = workspaces.find(workspace => workspace.id === value);
  return <div className={styles.picker}>
    <label className={styles.label} htmlFor="workspace-select">本机批次</label>
    <Select.Root value={value || ''} disabled={disabled} onValueChange={next => {const workspace = workspaces.find(item => item.id === next); if (workspace) onChange(workspace);}}>
      <Select.Trigger id="workspace-select" data-testid="workspace-list" aria-label="切换本机批次" className={styles.trigger}>
        <Icon name="folder" className={styles.folderIcon}/>
        <span className={styles.triggerText}><Select.Value placeholder="选择批次"><span className={styles.currentName}>{current ? shortPath(current.input_folder.path) : '选择批次'}</span></Select.Value></span>
        <Select.Icon className={styles.chevron}><Icon name="chevron"/></Select.Icon>
        {current && <Metadata workspace={current}/>}
      </Select.Trigger>
      <Select.Portal><Select.Content className={styles.content} position="popper" sideOffset={6} align="start" collisionPadding={12}>
        <Select.ScrollUpButton className={styles.scrollUp}><Icon name="chevron"/></Select.ScrollUpButton>
        <Select.Viewport className={styles.viewport}><Select.Group>
          <Select.Label className={styles.menuLabel}>切换批次<span>{count(workspaces.length)}</span></Select.Label>
          {workspaces.map(workspace => <Select.Item key={workspace.id} value={workspace.id} textValue={shortPath(workspace.input_folder.path)} className={styles.item}>
            <span className={styles.itemText}><Select.ItemText><span className={styles.itemName}>{shortPath(workspace.input_folder.path)}</span></Select.ItemText><Metadata workspace={workspace}/></span>
            <Select.ItemIndicator className={styles.indicator}><Icon name="check"/></Select.ItemIndicator>
          </Select.Item>)}
        </Select.Group></Select.Viewport>
        <Select.ScrollDownButton className={styles.scrollDown}><Icon name="chevron"/></Select.ScrollDownButton>
      </Select.Content></Select.Portal>
    </Select.Root>
  </div>;
}
