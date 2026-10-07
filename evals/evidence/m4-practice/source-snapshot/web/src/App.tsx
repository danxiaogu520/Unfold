import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { api, createRun, observeRun } from './api';
import { Questionnaire } from './Questionnaire';
import { ProfileEditor } from './ProfileEditor';
import type { Answer, CreateRun, Fact, LearningAction, Run, RunEvent, Space, SpaceSummary } from './types';
import { active, statusLabel } from './types';

const selectionKey = 'unfold.space';
const KnowledgePanel = lazy(() => import('./KnowledgePanel').then(module => ({ default: module.KnowledgePanel })));
const message = (error: unknown) => error instanceof Error ? error.message : '操作暂时失败，请重试。';

function FactRow({ label, facts }: { label: string; facts: Fact[] }) {
  const tags = { user: '你提供的', assumption: '待确认假设', unknown: '尚未确定' };
  return <div className="fact-row"><span>{label}</span><div>{facts.map((fact, index) => <p key={index}>
    {fact.value}<small className={`fact-source ${fact.source}`}>{tags[fact.source]}</small>
  </p>)}</div></div>;
}

export default function App() {
  const [spaces, setSpaces] = useState<SpaceSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(() => localStorage.getItem(selectionKey));
  const [space, setSpace] = useState<Space | null>(null);
  const [health, setHealth] = useState<{ model_mode: string } | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [domain, setDomain] = useState('');
  const [purpose, setPurpose] = useState('');
  const [lastRequest, setLastRequest] = useState<CreateRun | null>(null);
  const [trace, setTrace] = useState<unknown>(null);
  const isActive = active(run);
  const currentSelection = useRef(selected);
  const refreshSequence = useRef(0);
  currentSelection.current = selected;
  const mainRun = (saved: Space) => saved.runs.filter(item => item.operation !== 'resource').find(active)
    ?? saved.runs.find(item => item.operation !== 'resource') ?? null;

  const refresh = useCallback(async () => {
    const sequence = ++refreshSequence.current;
    const rows = await api<SpaceSummary[]>('/v1/spaces');
    setSpaces(rows);
    if (selected) {
      const saved = await api<Space>(`/v1/spaces/${encodeURIComponent(selected)}`);
      if (currentSelection.current === selected && sequence === refreshSequence.current) {
        setSpace(saved);
        setRun(mainRun(saved));
      }
    }
  }, [selected]);

  useEffect(() => {
    let disposed = false;
    api<{ model_mode: string }>('/health').then(value => { if (!disposed) setHealth(value); })
      .catch(value => { if (!disposed) setError(message(value)); });
    return () => { disposed = true; };
  }, []);

  useEffect(() => {
    let disposed = false;
    setSpace(null); setRun(null); setEvents([]); setTrace(null); setLastRequest(null);
    if (selected) localStorage.setItem(selectionKey, selected);
    else localStorage.removeItem(selectionKey);
    api<SpaceSummary[]>('/v1/spaces').then(rows => { if (!disposed) setSpaces(rows); }).catch(value => setError(message(value)));
    if (selected) api<Space>(`/v1/spaces/${encodeURIComponent(selected)}`).then(saved => {
      if (!disposed) { setSpace(saved); setRun(mainRun(saved)); }
    }).catch(value => { if (!disposed) setError(message(value)); });
    return () => { disposed = true; };
  }, [selected]);

  useEffect(() => {
    if (!run || !active(run)) return;
    return observeRun(run, next => {
      setRun(next);
      if (!active(next)) void refresh().catch(value => setError(message(value)));
    }, event => setEvents(previous => [...previous, event]));
    // Subscribe once per identity. Event snapshots update status, not subscription identity.
  }, [run?.run_id, isActive, refresh]);

  const perform = async (action: () => Promise<void>) => {
    setBusy(true); setError('');
    try { await action(); } catch (value) { setError(message(value)); } finally { setBusy(false); }
  };

  const generate = async (input: CreateRun) => {
    setLastRequest(input); setEvents([]); setTrace(null);
    setRun(await createRun(input));
  };

  const begin = async () => perform(async () => {
    const saved = await api<Space>('/v1/spaces', { method: 'POST', body: JSON.stringify({ domain: domain.trim(), user_brief: purpose.trim() }) });
    setSelected(saved.id);
    setDomain(''); setPurpose('');
    await createRun({ schema_version: 'api.v1', space_id: saved.id, operation: 'questionnaire',
      profile_revision: null, base_revision: null, input: { schema_version: 'questionnaire-input.v1',
        domain: saved.domain, user_brief: saved.user_brief, known_targets: ['purpose'],
        previous_question_keys: [], questions_asked: 0, round: 1 } });
    // Reload persisted state rather than mixing an optimistic candidate into official content.
    const latest = await api<Space>(`/v1/spaces/${saved.id}`);
    setSpace(latest);
    setRun(latest.runs[0] ?? null);
    setSpaces(await api<SpaceSummary[]>('/v1/spaces'));
  });

  const submitAnswers = async (answers: Answer[], operation: 'profile' | 'questionnaire' = 'profile') => perform(async () => {
    if (!space) return;
    const intake = space.intakes.at(-1);
    if (!intake) return;
    setEvents([]); setTrace(null); setLastRequest(null);
    setRun(await api<Run>(`/v1/spaces/${space.id}/intake`, { method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ operation, expected_intake_id: intake.id, answers }) }));
  });

  const learningAction = async (action: LearningAction) => perform(async () => {
    if (!space) return;
    setEvents([]); setTrace(null); setLastRequest(null);
    setRun(await api<Run>(`/v1/spaces/${space.id}/actions`, { method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify(action) }));
  });

  const intake = space?.intakes.at(-1);
  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#" onClick={event => { event.preventDefault(); setSelected(null); }}>
        <svg viewBox="0 0 32 32" aria-hidden="true"><path d="M7 25V12M7 18l11-9M7 25l18-13" /><circle cx="7" cy="8" r="3" /><circle cx="22" cy="6" r="3" /><circle cx="26" cy="10" r="2" /></svg>
        <span>知脉<small>UNFOLD</small></span>
      </a>
      <button className="new-space" onClick={() => { setSelected(null); setError(''); }}>＋ 新的学习空间</button>
      <div className="section-label">我的学习空间 <span>{spaces.length.toString().padStart(2, '0')}</span></div>
      <nav aria-label="学习空间">{spaces.map(item => <button className={`space-link ${selected === item.id ? 'selected' : ''}`} key={item.id}
        onClick={() => { setSelected(item.id); setError(''); }}><span className="space-symbol">◇</span><span>{item.domain}<small>{item.profile_revision ? '学习设定已保存' : '了解你的目标'}</small></span><span className="chevron">›</span></button>)}</nav>
      <div className="sidebar-note"><span className="local-dot" />本地单用户空间<small>进度保存在这台设备的数据库中</small></div>
    </aside>
    <main className="main-area">
      <header className="topbar"><span>一点点展开，慢慢形成自己的理解。</span><span className="mode-badge">{!health ? '连接中' : health.model_mode === 'fake' ? '开发替身 · 不调用模型' : 'DeepSeek · 真实生成'}</span></header>
      <div className={`workspace ${space ? 'with-task' : ''}`}>
        <div className="learning-area">
          {!selected ? <section className="welcome">
            <p className="eyebrow">从一个想学的主题开始</p>
            <h1>知识有脉络，<br />学习有方向。</h1>
            <p className="intro">告诉我你想学什么，以及希望用它做什么。<br />我们会从你的目标出发，把复杂的知识逐层展开。</p>
            <form className="start-form" onSubmit={event => { event.preventDefault(); void begin(); }}>
              <label>你想学习什么？<input autoFocus value={domain} onChange={event => setDomain(event.target.value)} required maxLength={6000} placeholder="例如：后端开发、二分查找、高中生物" /></label>
              <label>你希望用它做什么？<textarea value={purpose} onChange={event => setPurpose(event.target.value)} required maxLength={6000} rows={3} placeholder="例如：准备校招，希望能解释原理并完成一个小项目。暂不确定也可以。" /></label>
              <div className="form-end"><span className="hint">只问影响学习方向的问题</span><button className="primary" disabled={busy || !health}>开始探索 <span aria-hidden="true">→</span></button></div>
            </form>
            <div className="principles"><div><span>01</span><strong>先明确目标</strong><p>从用途出发，保留不确定的部分。</p></div><div><span>02</span><strong>按需逐层展开</strong><p>每次只向前一层，让知识保持清晰。</p></div><div><span>03</span><strong>随时回来继续</strong><p>已经生成的内容，会保存下来。</p></div></div>
          </section> : !space ? <p className="loading" role="status">正在读取学习空间…</p> : <>
            <div className="space-heading"><p className="eyebrow">你的学习空间</p><h1>{space.domain}</h1><p>{space.user_brief}</p></div>
            <div className="flow-steps"><span className={!space.profile ? 'current' : 'done'}>1 了解目标</span><span className={space.root_id ? 'done' : space.profile ? 'current' : ''}>2 学习设定</span><span className={space.root_id ? 'current' : ''}>3 展开知识</span></div>
            {space.profile ? <><section className="card profile-card"><div className="card-heading"><h2>你的学习设定</h2><ProfileEditor key={space.profile_revision} space={space} onSaved={() => perform(refresh)} /><span className="revision">版本 {space.profile_revision}</span></div>
              <p className="hint">假设与未知会明确标出。这份设定会决定后续知识的展开范围。</p>
              <FactRow label="主题" facts={[space.profile.topic]} /><FactRow label="主要用途" facts={[space.profile.primary_purpose]} />
              <FactRow label="当前基础" facts={[space.profile.background]} /><FactRow label="目标能力" facts={space.profile.target_abilities} />
              <FactRow label="包含范围" facts={space.profile.included_scope} /><FactRow label="暂不包含" facts={space.profile.excluded_scope} />
              <FactRow label="学习约束" facts={space.profile.constraints} />
            </section><Suspense fallback={<p className="hint">正在读取学习内容…</p>}><KnowledgePanel space={space} disabled={busy || isActive} onAction={learningAction} onChanged={() => perform(refresh)} /></Suspense></> : intake ? <section className="card"><div className="card-heading"><h2>把学习方向再确定一点</h2><span className="revision">{intake.questionnaire.questions.length} 个问题</span></div>
              <p className="hint">{intake.questionnaire.rationale}</p>
              <Questionnaire key={intake.id} value={intake.questionnaire} saved={intake.answers} disabled={busy || isActive}
                onSubmit={answers => submitAnswers(answers)}
                onFollowup={intake.round < 2 && intake.questions_asked < 5 ? answers => submitAnswers(answers, 'questionnaire') : undefined} />
            </section> : <section className="card pending-card"><span className="branch-mark">◇</span><h2>正在准备开场问题</h2><p>问题会围绕你的用途和基础。成功保存后会显示在这里。</p></section>}
          </>}
          {error && <div className="error-banner" role="alert">{error}<button onClick={() => { setError(''); void refresh().catch(value => setError(message(value))); }}>重新读取</button></div>}
        </div>
        {space && <aside className="task-panel"><div className="panel-heading"><span>生成动态</span><small>保存与状态</small></div>
          {run ? <><div className={`run-state ${run.status}`}><span className="status-dot" /><strong role="status">{run.result_kind === 'structure_preview' ? '结构候选已保存' : statusLabel[run.status]}</strong></div>
            <p className="task-description">{run.result_kind === 'structure_preview' ? '生成结果是待确认候选。当前知识结构以节点区域中的实际状态为准。' : run.status === 'succeeded' ? '结果已经保存，刷新后仍可继续。' : isActive ? '关闭页面不会取消生成。只有保存成功的结果才会进入学习内容。' : run.error?.message ?? '这次生成已经结束。'}
              {run.error?.outcome_unknown && <span>外部调用结果未知，可能已产生费用。</span>}</p>
            {isActive && <button className="secondary" disabled={busy} onClick={() => void perform(async () => { setRun(await api(`/v1/runs/${run.run_id}/cancel`, { method: 'POST' })); await refresh(); })}>取消这次生成</button>}
            {run.status === 'failed' && <button className="secondary" disabled={busy} onClick={() => void perform(async () => {
              const input = lastRequest ?? (await api<{ request: CreateRun }>(`/v1/runs/${run.run_id}/trace`)).request;
              await generate(input);
            })}>重新生成</button>}
            <ul className="timeline">{events.map(event => <li key={event.sequence}><span />{statusLabel[event.snapshot.status]}<small>#{event.sequence}</small></li>)}</ul>
            <details className="debug"><summary onClick={() => { if (!trace) void api(`/v1/runs/${run.run_id}/trace`).then(setTrace).catch(value => setError(message(value))); }}>运行记录</summary>
              <p>Run · {run.run_id}</p><p>Attempt · {run.attempt}</p>{trace !== null && <pre>{JSON.stringify(trace, null, 2)}</pre>}</details>
          </> : <p className="hint">尚未开始生成。</p>}
          <div className="gentle-note">每次只展开当前需要的知识。<br />留一点空间，给自己的理解。</div>
        </aside>}
      </div>
    </main>
  </div>;
}
