import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useT } from '../prefs';

/**
 * In-page confirmation. Native window.confirm() is suppressed by embedded browsers (Claude's browser pane, VS Code
 * webviews), where it returns false at once and the action silently never happens.
 */
type Ask = (message: string, options?: { ok?: string; danger?: boolean }) => Promise<boolean>;
interface Pending { message: string; ok?: string; danger?: boolean; resolve: (value: boolean) => void }

const ConfirmContext = createContext<Ask | null>(null);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const t = useT();
  const [pending, setPending] = useState<Pending | null>(null);
  const okButton = useRef<HTMLButtonElement>(null);

  const ask = useCallback<Ask>((message, options) => new Promise<boolean>((resolve) => {
    setPending({ message, ...options, resolve });
  }), []);
  const close = (value: boolean) => {
    pending?.resolve(value);
    setPending(null);
  };

  useEffect(() => {
    if (!pending) return;
    okButton.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending]);

  return (
    <ConfirmContext.Provider value={ask}>
      {children}
      {pending && (
        <div className="dialog-backdrop" onClick={() => close(false)}>
          <div className="dialog card" role="alertdialog" aria-modal="true" aria-labelledby="confirm-message" onClick={(e) => e.stopPropagation()}>
            <p id="confirm-message" className="dialog-message">{pending.message}</p>
            <div className="actions dialog-actions">
              <button type="button" onClick={() => close(false)}>{t('取消')}</button>
              <button type="button" ref={okButton} className={pending.danger ? 'danger' : 'primary'} onClick={() => close(true)}>
                {pending.ok ?? t('确定')}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): Ask {
  const ask = useContext(ConfirmContext);
  if (!ask) throw new Error('useConfirm outside ConfirmProvider');
  return ask;
}
