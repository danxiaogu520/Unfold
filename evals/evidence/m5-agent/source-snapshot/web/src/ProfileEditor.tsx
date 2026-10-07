import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import type { Profile, Space } from './types';

export function ProfileEditor({ space, onSaved }: { space: Space; onSaved: () => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (editing && !dialog.current?.open) dialog.current?.showModal(); }, [editing]);
  const fields = [
    ['primary_purpose', '主要用途', false], ['background', '当前基础', false],
    ['target_abilities', '目标能力（每行一项）', true], ['included_scope', '包含范围（每行一项）', true],
    ['excluded_scope', '暂不包含（每行一项）', true], ['constraints', '学习约束（每行一项）', true],
  ] as const;
  const begin = () => {
    if (!space.profile) return;
    const profile = space.profile;
    setValue(Object.fromEntries(fields.map(([key, , multiple]) => [key, multiple
      ? (profile[key] as Profile['target_abilities']).map(fact => fact.value).join('\n')
      : (profile[key] as Profile['background']).value])));
    setError(''); setEditing(true);
  };
  if (!editing) return <button className="text-button" onClick={begin}>修改设定</button>;
  return <dialog ref={dialog} className="profile-editor" onCancel={() => setEditing(false)} aria-label="修改学习方向"><h3>修改学习方向</h3><p className="hint">保存为新版本。旧内容保留，展开判定会等待重新评估。</p>
    <form onSubmit={event => {
      event.preventDefault();
      if (!space.profile) return;
      const profile = space.profile;
      const changes: Record<string, string | string[] | null> = {};
      for (const [key, , multiple] of fields) {
        const text = value[key].trim();
        const old = multiple ? (profile[key] as Profile['target_abilities']).map(fact => fact.value).join('\n')
          : (profile[key] as Profile['background']).value;
        if (text !== old) changes[key] = multiple ? text.split('\n').map(line => line.trim()).filter(Boolean) : text || null;
      }
      if (!Object.keys(changes).length) { setEditing(false); return; }
      setBusy(true); setError('');
      void api(`/v1/spaces/${space.id}/profile`, { method: 'PATCH', body: JSON.stringify({ expected_revision: space.profile_revision, fields: changes }) })
        .then(async () => { setEditing(false); await onSaved(); })
        .catch(problem => setError(problem instanceof Error ? problem.message : '保存失败'))
        .finally(() => setBusy(false));
    }}>
      {fields.map(([key, label]) => <label key={key}>{label}<textarea rows={2} value={value[key]}
        onChange={event => setValue(previous => ({ ...previous, [key]: event.target.value }))} maxLength={6000} /></label>)}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="form-end"><button type="button" className="secondary" disabled={busy} onClick={() => setEditing(false)}>取消修改</button>
        <button className="primary" disabled={busy}>保存新设定</button></div>
    </form>
  </dialog>;
}
