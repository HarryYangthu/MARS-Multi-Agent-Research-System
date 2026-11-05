import { useEffect, useRef, useState } from 'react';
import { api, type FolderEntries } from '../api';
import { useT } from '../prefs';
import { ErrorNote } from './ui';

/** The local server supplies real filesystem entries; no browser-only fake folder paths. */
export function PathPicker({ initial = '', files = false, onPick, onClose }: {
  initial?: string; files?: boolean; onPick: (path: string) => void; onClose: () => void;
}) {
  const t = useT();
  const dialog = useRef<HTMLDialogElement>(null);
  const [folder, setFolder] = useState<FolderEntries | null>(null);
  const [path, setPath] = useState(initial);
  const [chosen, setChosen] = useState('');
  const [error, setError] = useState('');
  const go = (next: string) => {
    setError(''); setChosen('');
    api.folders(next, files).then((data) => { setFolder(data); setPath(data.path); }, (e: Error) => setError(e.message));
  };
  useEffect(() => { dialog.current?.showModal(); go(initial); }, []);
  return (
    <dialog ref={dialog} className="path-dialog" onCancel={onClose} onClose={onClose} aria-label={t(files ? '选择文件' : '选择文件夹')}>
      <div className="section-head"><h2>{t(files ? '选择文件' : '选择文件夹')}</h2><button type="button" onClick={onClose} aria-label={t('关闭')}>×</button></div>
      <form className="path-bar" onSubmit={(e) => { e.preventDefault(); go(path); }}>
        <button type="button" disabled={!folder || folder.parent === folder.path} onClick={() => folder && go(folder.parent)}>{t('上一级')}</button>
        <input aria-label={t('文件夹路径')} value={path} onChange={(e) => setPath(e.target.value)} /><button>{t('打开')}</button>
      </form>
      {error && <ErrorNote error={error} />}
      <div className="path-list">
        {folder?.entries.map((entry) => <button type="button" key={entry.path} className={chosen === entry.path ? 'chosen' : ''}
          onClick={() => entry.directory ? go(entry.path) : setChosen(entry.path)}>
          <span aria-hidden="true">{entry.directory ? '▸' : '·'}</span>{entry.name}
        </button>)}
        {folder?.entries.length === 0 && <p className="muted">{t('这个文件夹是空的')}</p>}
      </div>
      <div className="section-head"><span className="small muted ellipsis">{chosen || folder?.path}</span><div className="actions">
        <button type="button" onClick={onClose}>{t('取消')}</button>
        <button type="button" className="primary" disabled={files ? !chosen : !folder} onClick={() => { onPick(files ? chosen : folder!.path); onClose(); }}>{t('选择')}</button>
      </div></div>
    </dialog>
  );
}
