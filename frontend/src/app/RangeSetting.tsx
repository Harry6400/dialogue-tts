import {useState,type CSSProperties} from 'react';
import styles from './App.module.css';

export function RangeSetting({id, label, value, min, max, step, unit, disabled, decimals = 0, onCommit}: {id: string; label: string; value: number; min: number; max: number; step: number; unit: string; disabled: boolean; decimals?: number; onCommit: (value: number) => void}) {
  const [draft, setDraft] = useState(() => value.toFixed(decimals));
  const [error, setError] = useState('');
  function commit(text: string) {
    const next = Number(text);
    if (!text.trim() || !Number.isFinite(next) || next < min || next > max || decimals === 0 && !Number.isInteger(next)) {setError(`请输入 ${min}–${max}${unit} 范围内的${decimals === 0 ? '整数' : '数值'}。`); return;}
    setError(''); setDraft(next.toFixed(decimals));
    if (next !== value) onCommit(next);
  }
  const numeric = Number(draft);
  const rangeValue = Number.isFinite(numeric) && numeric >= min && numeric <= max ? numeric : value;
  return <div className={styles.rangeField}>
    <div className={styles.fieldLabel}><label htmlFor={id}>{label}</label><div className={styles.numericValue}><input type="number" aria-label={label + '数值'} aria-invalid={!!error} aria-describedby={error ? id + '-error' : undefined} value={draft} min={min} max={max} step={decimals ? 0.01 : 1} disabled={disabled} onChange={event => setDraft(event.target.value)} onBlur={event => commit(event.target.value)} onKeyDown={event => {if (event.key === 'Enter') event.currentTarget.blur(); if (event.key === 'Escape') {setDraft(value.toFixed(decimals)); setError('');}}}/><span>{unit}</span></div></div>
    <input id={id} type="range" min={min} max={max} step={step} value={rangeValue} disabled={disabled} style={{'--range-progress': Math.max(0,Math.min(100,(rangeValue-min)/(max-min)*100))+'%'} as CSSProperties} onChange={event => {setDraft(Number(event.target.value).toFixed(decimals)); setError('');}} onPointerUp={event => commit(event.currentTarget.value)} onKeyUp={event => {if (['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','PageUp','PageDown'].includes(event.key)) commit(event.currentTarget.value);}}/>
    {error && <p id={id + '-error'} className={styles.fieldError} role="alert">{error}</p>}
  </div>;
}
