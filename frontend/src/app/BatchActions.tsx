import {useEffect, useId, useRef, useState, type KeyboardEvent} from 'react';
import {Icon, type IconName} from './Icon';
import styles from './App.module.css';

export interface BatchAction {id: string; label: string; icon: IconName; disabled?: boolean; danger?: boolean; onSelect: () => void}
interface Props {name: string; actions: BatchAction[]; disabled?: boolean}

export function BatchActions({name, actions, disabled = false}: Props) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const firstFocus = useRef<'first' | 'last'>('first');
  const menuId = useId();
  function close(restoreFocus = false) {setOpen(false); if (restoreFocus) trigger.current?.focus();}
  useEffect(() => {
    if (!open) return;
    const items = menu.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)');
    (firstFocus.current === 'last' ? items?.[items.length - 1] : items?.[0])?.focus();
    if (!items?.length) menu.current?.focus();
    function outside(event: PointerEvent) {if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);}
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);
  function keyboard(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); close(true); return;}
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const items = Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') || []);
    if (!items.length) return;
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    items[next].focus();
  }
  return <div ref={root} className={styles.batchActions}>
    <button ref={trigger} className={styles.iconButton} disabled={disabled} aria-label="批次操作" aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined} onPointerDown={event => {if (open) event.preventDefault();}} onClick={() => {if (open) close(true); else {firstFocus.current = 'first'; setOpen(true);}}} onKeyDown={event => {if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {event.preventDefault(); firstFocus.current = event.key === 'ArrowUp' ? 'last' : 'first'; setOpen(true);}}}><Icon name="more"/><span>批次操作</span></button>
    {open && <div ref={menu} id={menuId} className={styles.batchMenu} role="menu" aria-label="批次操作" tabIndex={-1} onKeyDown={keyboard} onBlur={event => {if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) close();}}>
      <p className={styles.batchMenuLabel}>{name}</p>
      {actions.map(action => <button key={action.id} role="menuitem" className={`${styles.batchMenuItem} ${action.danger ? styles.batchMenuDanger : ''}`} disabled={action.disabled} onClick={() => {close(true); action.onSelect();}}><Icon name={action.icon}/><span>{action.label}</span></button>)}
    </div>}
  </div>;
}
