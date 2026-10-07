import { useEffect, useRef, useState } from 'react';
import { api, observeRun } from './api';
import { active } from './types';
import type { Run } from './types';
import type { ResourceState } from './ResourceMedia';

interface RuntimeView { session_id: string; frame_url: string; seconds_remaining: number; viewport: { width: number; height: number } }

export function WebResource({ spaceId, resource, caption, fallback, onChanged }: {
  spaceId: string; resource: ResourceState | undefined; caption: string; fallback: string; onChanged: () => Promise<void>;
}) {
  const [runtime, setRuntime] = useState<RuntimeView | null>(null);
  const [frame, setFrame] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [text, setText] = useState('');
  const [savedSource, setSavedSource] = useState<string | null>(null);
  const [workingHtml, setWorkingHtml] = useState('');
  const latestRuntime = useRef<RuntimeView | null>(null);
  latestRuntime.current = runtime;
  const version = resource?.versions[0];
  useEffect(() => { setSavedSource(null); setWorkingHtml(''); }, [version?.id]);
  useEffect(() => {
    let obsolete = false;
    let close: (() => void) | undefined;
    if (resource?.run_id && ['queued', 'running'].includes(resource.status)) {
      void api<Run>(`/v1/runs/${resource.run_id}`).then(saved => {
        if (obsolete) return;
        if (!active(saved)) { void onChanged(); return; }
        close = observeRun(saved, state => { if (!active(state)) void onChanged(); }, () => {});
      }).catch(() => { if (!obsolete) setError('制作进度暂时不可用，重新打开后可继续查看。'); });
    }
    return () => { obsolete = true; close?.(); };
  }, [resource?.run_id, resource?.status]);
  useEffect(() => {
    if (!runtime) return;
    const timer = setInterval(() => setFrame(old => old+1), 600);
    const expiry = setTimeout(() => { setRuntime(null); setError('本轮演示已结束，可再次启动，不会重新生成资源。'); }, runtime.seconds_remaining*1000);
    return () => { clearInterval(timer); clearTimeout(expiry); };
  }, [runtime?.session_id]);
  useEffect(() => () => {
    const current = latestRuntime.current;
    if (current) void api(`/v1/runtime/${current.session_id}/actions`, { method: 'POST', body: JSON.stringify({ action: 'stop' }) }).catch(() => {});
  }, []);
  const command = async (action: Record<string, unknown>) => {
    if (!runtime) return;
    setError(''); setBusy(true);
    try { await api(`/v1/runtime/${runtime.session_id}/actions`, { method: 'POST', body: JSON.stringify(action) });
      if (action.action === 'stop') setRuntime(null); else setFrame(old => old+1);
      if (action.action === 'reset' && savedSource !== null) setWorkingHtml(savedSource);
    } catch (problem) { setError(problem instanceof Error ? problem.message : '演示操作失败'); }
    finally { setBusy(false); }
  };
  const start = async (source?: string) => {
    if (!version) return;
    setBusy(true); setError('');
    try {
      const saved = await api<RuntimeView>(`/v1/spaces/${spaceId}/resources/${version.id}/runtime`, {
        method: 'POST', ...(source !== undefined ? { body: JSON.stringify({ working_html: source }) } : {}),
      });
      setRuntime(saved); setFrame(0);
    } catch (problem) { setError(problem instanceof Error ? problem.message : '演示启动失败'); }
    finally { setBusy(false); }
  };
  return <figure className="web-resource">
    <strong>{caption}</strong><p className="hint">{fallback}</p>
    <p className="hint">演示在独立浏览器中运行。当前页面接收画面和操作结果，资源脚本不进入学习应用；技术检查与知识核验分开。</p>
    {version?.source.browser_check?.passed && <p className="hint">已执行有限浏览器行为检查，教学适配仍待核对。</p>}
    {runtime && <div className="runtime-surface" tabIndex={0} aria-label="网页演示画面" onKeyDown={event => {
      const mapped = event.key === ' ' ? 'Space' : event.key;
      if (['ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown', 'Enter', 'Tab', 'Backspace', 'r', 'Space'].includes(mapped)) {
        event.preventDefault(); void command({ action: 'key', key: mapped });
      }
    }}><img alt={caption+'运行画面'} src={runtime.frame_url+'?frame='+frame} onClick={event => {
      const rect = event.currentTarget.getBoundingClientRect();
      void command({ action: 'click', x: (event.clientX-rect.left)*runtime.viewport.width/rect.width,
        y: (event.clientY-rect.top)*runtime.viewport.height/rect.height });
    }} onError={() => { setError('运行画面暂时不可用；可停止后重新启动。'); }} /></div>}
    <div className="node-actions">
      {!runtime && version?.status === 'ready' && <button className="primary" disabled={busy} onClick={() => void start()}>启动演示</button>}
      {runtime && <><button className="secondary" disabled={busy} onClick={() => void command({ action: 'reset' })}>重置演示</button>
        <button className="secondary" disabled={busy} onClick={() => void command({ action: 'stop' })}>停止演示</button></>}
      {resource?.can_retry && ['failed', 'cancelled', 'conflicted', 'missing'].includes(resource.status) && <button className="secondary" disabled={busy} onClick={() => {
        setBusy(true); setError(''); void api(`/v1/spaces/${spaceId}/resources/${resource.id}/retry`, { method: 'POST' })
          .then(onChanged).catch(problem => setError(problem instanceof Error ? problem.message : '重试失败')).finally(() => setBusy(false));
      }}>重新制作资源</button>}
    </div>
    {version?.status === 'ready' && <details className="web-source"><summary>查看或修改本轮代码</summary>
      <p className="hint">编辑用于本轮运行，保存的资源不会被改写。重置恢复已保存代码。</p>
      {savedSource === null ? <button className="secondary" disabled={busy} onClick={() => {
        setBusy(true); setError(''); void api<{ html: string }>(`/v1/spaces/${spaceId}/resources/${version.id}/source`)
          .then(source => { setSavedSource(source.html); setWorkingHtml(source.html); })
          .catch(problem => setError(problem instanceof Error ? problem.message : '代码读取失败'))
          .finally(() => setBusy(false));
      }}>读取已保存代码</button> : <>
        <label>本轮代码副本<textarea aria-label="本轮代码副本" value={workingHtml} maxLength={24000} rows={12}
          spellCheck={false} onChange={event => setWorkingHtml(event.target.value)} /></label>
        <div className="node-actions"><button className="secondary" disabled={busy || !workingHtml.trim()}
          onClick={() => void start(workingHtml)}>运行代码副本</button>
          <button className="text-button" disabled={busy} onClick={() => {
            setWorkingHtml(savedSource); if (runtime) void command({ action: 'reset' });
          }}>恢复已保存代码</button></div>
      </>}
    </details>}
    {runtime && <div className="runtime-text"><label>向已选中的演示输入框填写文本<input value={text} onChange={event => setText(event.target.value)} maxLength={100} /></label>
      <button className="text-button" disabled={busy || !text} onClick={() => void command({ action: 'text', text })}>发送文本</button></div>}
    {!version && <p className="hint">{resource?.error?.message ?? '资源待制作，正文已经保存。'}</p>}
    {error && <p className="error" role="alert">{error}</p>}
  </figure>;
}
