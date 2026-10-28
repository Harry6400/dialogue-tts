import {useEffect, useRef, type ReactNode} from 'react';
import {Icon} from './Icon';
import styles from './App.module.css';

function ModalFrame({children, onClose, className, label, labelledBy}: {children: ReactNode; onClose: () => void; className: string; label?: string; labelledBy?: string}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement;
    const dialog = ref.current;
    if (!dialog) return;
    const native = typeof dialog.showModal === 'function';
    if (native) dialog.showModal();
    else {
      dialog.setAttribute('open', ''); dialog.setAttribute('role', 'dialog');
      dialog.parentElement!.dataset.fallback = 'true';
      dialog.querySelector<HTMLElement>('button,input,select,audio')?.focus();
    }
    function trap(event: KeyboardEvent) {
      if (native) return;
      if (event.key === 'Escape') {event.preventDefault(); dialog!.dispatchEvent(new Event('cancel', {cancelable: true}));}
      if (event.key !== 'Tab') return;
      const controls = Array.from(dialog!.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),select:not([disabled]),audio,[tabindex="0"]'));
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {event.preventDefault(); last?.focus();}
      else if (!event.shiftKey && document.activeElement === last) {event.preventDefault(); first?.focus();}
    }
    document.addEventListener('keydown', trap);
    return () => {
      document.removeEventListener('keydown', trap);
      if (native) dialog.close(); else dialog.removeAttribute('open');
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
    };
  }, []);
  return <div className={styles.modalLayer}><dialog ref={ref} className={className} aria-label={label} aria-labelledby={labelledBy} aria-modal="true" onCancel={event => {event.preventDefault(); onClose();}}>{children}</dialog></div>;
}

export function Dialog({title, children, onClose, wide = false}: {title: string; children: ReactNode; onClose: () => void; wide?: boolean}) {
  return <ModalFrame className={`${styles.dialog} ${wide ? styles.wideDialog : ''}`} labelledBy="dialog-title" onClose={onClose}>
    <header className={styles.dialogHead}><h2 id="dialog-title">{title}</h2><button className={styles.iconButton} aria-label="关闭弹窗" onClick={onClose}><Icon name="close"/></button></header>
    <div className={styles.dialogBody}>{children}</div>
  </ModalFrame>;
}

export function SettingsDrawer({children, onClose}: {children: ReactNode; onClose: () => void}) {
  return <ModalFrame className={styles.settingsDialog} label="声音与设置" onClose={onClose}>{children}</ModalFrame>;
}
