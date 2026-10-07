import { useEffect, useState } from 'react';
import { api, observeRun } from './api';
import { active } from './types';
import type { Run } from './types';

export interface ResourceState {
  id: string; request_key: string; status: string; run_id: string | null; can_retry: boolean;
  error: { message: string } | null;
  versions: { id: string; revision: number; status: string; file_url: string | null;
    source: { title?: string; artist?: string | null; source_url?: string; license?: string; license_url?: string | null; teaching_suitability: string;
      component_ref?: string; runtime?: string; browser_check?: { passed: boolean; version: string; knowledge_correctness: string } } }[];
}
const labels: Record<string, string> = { pending: '图片待制作', queued: '图片等待制作', running: '正在获取图片',
  ready: '图片已保存', failed: '图片获取失败', cancelled: '图片制作已取消', conflicted: '图片依赖已变化', missing: '图片文件不可用' };
const safeLink = (url: string | null | undefined) => url && /^https:\/\//i.test(url) ? url : undefined;

export function ResourceMedia({ spaceId, resource, caption, fallback, onChanged }: {
  spaceId: string; resource: ResourceState | undefined; caption: string; fallback: string; onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [versionId, setVersionId] = useState<string | null>(null);
  useEffect(() => {
    let obsolete = false;
    let close: (() => void) | undefined;
    if (resource?.run_id && ['queued', 'running'].includes(resource.status)) {
      void api<Run>(`/v1/runs/${resource.run_id}`).then(snapshot => {
        if (obsolete) return;
        if (!active(snapshot)) { void onChanged(); return; }
        close = observeRun(snapshot, state => { if (!active(state)) void onChanged(); }, () => {});
      }).catch(() => { if (!obsolete) setError('图片进度暂时无法读取，重新打开后可继续查看。'); });
    }
    return () => { obsolete = true; close?.(); };
  }, [resource?.run_id, resource?.status]);
  const version = resource?.versions.find(item => item.id === versionId) ?? resource?.versions[0];
  const source = version?.source;
  return <figure className="image-resource">
    <strong>{labels[resource?.status ?? 'pending'] ?? '资源暂不可用'}</strong>
    {resource && resource.versions.length > 1 && <label>图片版本 <select value={version?.id} onChange={event => setVersionId(event.target.value)}>
      {resource.versions.map(item => <option key={item.id} value={item.id}>v{item.revision}</option>)}</select></label>}
    {version?.status === 'ready' && version.file_url && <img src={version.file_url} alt={caption || fallback} loading="lazy" />}
    <figcaption>{caption}</figcaption><p className="hint">{fallback}</p>
    {source && <p className="image-source">来源：<a href={safeLink(source.source_url)} target="_blank" rel="noreferrer noopener">{source.title}</a>
      {' · '}{source.artist ?? '作者未标明'}{' · '}<a href={safeLink(source.license_url)} target="_blank" rel="noreferrer noopener">{source.license}</a>
      <span>图片教学适配尚待核对。</span></p>}
    {resource && resource.can_retry && ['failed', 'cancelled', 'conflicted', 'missing'].includes(resource.status) && <button className="secondary" disabled={busy} onClick={() => {
      setBusy(true); setError(''); void api(`/v1/spaces/${spaceId}/resources/${resource.id}/retry`, { method: 'POST' })
        .then(onChanged).catch(problem => setError(problem instanceof Error ? problem.message : '图片重试失败')).finally(() => setBusy(false));
    }}>{busy ? '正在登记…' : '重试图片'}</button>}
    {resource?.error && <p className="hint">{resource.error.message}</p>}
    {!resource?.can_retry && resource && !['pending', 'queued', 'running', 'ready'].includes(resource.status) && <p className="hint">资源属于历史内容，请通过当前讲解提出新的资源请求。</p>}
    {error && <p className="error" role="alert">{error}</p>}
  </figure>;
}
