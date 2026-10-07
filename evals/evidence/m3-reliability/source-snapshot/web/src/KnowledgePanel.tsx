import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import { api } from './api';
import type { LearningAction, Node, Space } from './types';

export function KnowledgePanel({ space, disabled, onAction, onChanged }: {
  space: Space;
  disabled: boolean;
  onAction: (action: LearningAction) => Promise<void>;
  onChanged: () => Promise<void>;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [history, setHistory] = useState<number | null>(null);
  const [instruction, setInstruction] = useState('');
  const [feedbackSaved, setFeedbackSaved] = useState(false);
  const [feedbackKind, setFeedbackKind] = useState('other');
  const [feedbackBusy, setFeedbackBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const saved = localStorage.getItem(`unfold.node.${space.id}`);
    setSelected(space.nodes.some(node => node.id === saved) ? saved : space.root_id);
    setHistory(null); setInstruction(''); setFeedbackSaved(false);
  }, [space.id, space.root_id]);
  useEffect(() => { if (selected) localStorage.setItem(`unfold.node.${space.id}`, selected); }, [selected, space.id]);
  const node = space.nodes.find(item => item.id === selected) ?? space.nodes.find(item => item.id === space.root_id);
  const choose = (id: string) => { setSelected(id); setHistory(null); setFeedbackSaved(false); setInstruction(''); };
  const action = (operation: LearningAction['operation'], regenerate = false) => onAction({
    operation, expected_profile_revision: space.profile_revision,
    expected_base_revision: operation === 'outline' ? space.outline_revision || null : node?.revision ?? null,
    target_id: operation === 'outline' ? null : node?.id ?? null,
    instruction: instruction.trim() || '结合当前用途说明这个节点，并给出一个例子。', regenerate,
  });
  if (!space.root_id) return <section className="card outline-start"><p className="eyebrow">下一步 · 看见知识的脉络</p>
    <h2>从第一层大纲开始</h2><p className="hint">按照已保存的学习设定组织知识。每个节点先展示概述，再由你选择展开或阅读讲解。</p>
    <button className="primary" disabled={disabled} onClick={() => void action('outline')}>生成第一层大纲 →</button></section>;
  if (!node) return <p className="hint">正在读取节点…</p>;
  const path: Node[] = [node];
  const visited = new Set([node.id]);
  while (path[0].parent_id) {
    const parent = space.nodes.find(item => item.id === path[0].parent_id);
    if (!parent || visited.has(parent.id)) break;
    visited.add(parent.id); path.unshift(parent);
  }
  const content = node.contents.find(item => item.revision === history) ?? node.contents[0];
  const readingHistory = content && content.revision !== node.revision;
  const stale = node.decision.status === 'stale';
  const leaf = node.decision.state === 'leaf';
  return <section className="knowledge-section">
    <nav className="breadcrumbs" aria-label="当前知识路径">{path.map((item, index) => <span key={item.id}>
      {index > 0 && <span aria-hidden="true"> / </span>}<button onClick={() => choose(item.id)} aria-current={item.id === node.id ? 'page' : undefined}>{item.title}</button></span>)}</nav>
    <div className="card node-card"><div className="card-heading"><h2>{node.title}</h2><span className={`decision-badge ${stale ? 'stale' : leaf ? 'leaf' : ''}`}>{stale ? '判定待重评' : leaf ? '当前目标的叶子' : '可继续展开'}</span></div>
      <p className="node-overview">{node.overview}</p><p className="node-scope">当前范围 · {node.scope}</p>
      <div className="decision-note">{stale ? '学习设定已更新。旧内容可以继续阅读，普通展开需要先重新评估。' : node.decision.reason}</div>
      {node.suggestions.filter(item => item.status === 'pending').map(item => <div key={item.id} className="structure-preview">
        <h3>待确认的新结构</h3><p>当前结构保持原样。采用后，旧子树会归档，历史内容继续保留。</p>
        {item.proposal.root && <strong>新根节点：{item.proposal.root.title}</strong>}
        {item.proposal.division_basis && <p>{item.proposal.division_basis}</p>}
        {item.proposal.restructuring_note && <p>{item.proposal.restructuring_note}</p>}
        {item.proposal.decision?.state === 'leaf' && <p>拟设为当前目标下的叶子，归档现有 {node.children.length} 个子项。</p>}
        {item.proposal.children?.map((child, index) => <div key={index} className="preview-child"><strong>{child.title}</strong><p>{child.scope}</p><small>{child.overview}</small></div>)}
        <div className="node-actions"><button className="primary" disabled={disabled} onClick={() => {
          setError(''); void api(`/v1/spaces/${space.id}/suggestions/${item.id}/apply`, { method: 'POST' })
            .then(async () => { setHistory(null); await onChanged(); }).catch(problem => setError(problem instanceof Error ? problem.message : '采用失败'));
        }}>确认采用这个结构</button><button className="secondary" disabled={disabled} onClick={() => {
          setError(''); void api(`/v1/spaces/${space.id}/suggestions/${item.id}/dismiss`, { method: 'POST' })
            .then(onChanged).catch(problem => setError(problem instanceof Error ? problem.message : '保存失败'));
        }}>保留原结构</button></div>
      </div>)}
      <div className="node-actions">
        {!stale && !leaf && !node.children.length && <button className="primary" disabled={disabled} onClick={() => void action('expansion')}>展开下一层 →</button>}
        <button className="secondary" disabled={disabled || readingHistory} onClick={() => void action('explanation')}>{node.contents.length > 1 ? '生成新的讲解' : '阅读详细讲解'}</button>
        <button className="text-button" disabled={disabled} onClick={() => void action('reassessment')}>{stale ? '重新评估展开判定' : '终点/展开不合适，重评'}</button>
      </div>
      <label className="supplement">补充要求或反馈（选填）<textarea rows={2} value={instruction} maxLength={6000}
        onChange={event => { setInstruction(event.target.value); setFeedbackSaved(false); }} placeholder="例如：需要一个例子，或者希望调整这个终点。" /></label>
      <label className="feedback-kind">反馈类型 <select aria-label="反馈类型" value={feedbackKind} onChange={event => setFeedbackKind(event.target.value)}>
        <option value="other">补充想法</option><option value="need_example">需要例子</option><option value="too_hard">太难</option>
        <option value="too_easy">太简单</option><option value="wrong_scope">不符合用途</option><option value="stopping">终点不合适</option><option value="factual_error">内容有问题</option>
      </select></label>
      <button className="text-button" disabled={disabled || readingHistory || feedbackBusy || !instruction.trim() || feedbackSaved} onClick={() => {
        setFeedbackBusy(true);
        setError(''); void api(`/v1/spaces/${space.id}/nodes/${node.id}/feedback`, { method: 'POST',
          body: JSON.stringify({ expected_profile_revision: space.profile_revision, expected_base_revision: node.revision, kind: feedbackKind, text: instruction.trim() }) })
          .then(async () => { setFeedbackSaved(true); await onChanged(); }).catch(problem => setError(problem instanceof Error ? problem.message : '反馈保存失败')).finally(() => setFeedbackBusy(false));
      }}>{feedbackSaved ? '反馈已保存' : '只保存反馈'}</button>
      {error && <p className="error" role="alert">{error}</p>}
      {node.suggestions.some(item => item.status === 'stale') && <p className="hint">历史结构候选已过期，不能覆盖当前内容。</p>}
      {content && <div className="content-area"><div className="content-heading"><span>已保存内容 · 版本 {content.revision}</span>
        <label>内容版本 <select aria-label="内容版本" value={content.revision} onChange={event => setHistory(Number(event.target.value))}>
          {node.contents.map(item => <option key={item.id} value={item.revision}>v{item.revision} · 设定 v{item.profile_revision}</option>)}</select></label></div>
        {content.review_status === 'reported' ? <p className="review-note">此版本已记录内容问题，请结合反馈核对；不要把它作为已核验的结论。</p>
          : <p className="hint">生成内容尚未完成事实核验，必要时请对照可靠资料。</p>}
        {content.profile_revision !== space.profile_revision && <p className="hint">这份内容按历史学习设定保存，当前设定为 v{space.profile_revision}。</p>}
        {readingHistory && <p className="hint">正在阅读历史版本。切回当前内容后可补充讲解或提交反馈。<button className="text-button" onClick={() => setHistory(null)}>回到当前内容</button></p>}
        {content.blocks.map((block, index) => block.type === 'markdown'
          ? <div key={index} className="markdown"><ReactMarkdown skipHtml remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[[rehypeKatex, { trust: false, throwOnError: false }]]}
            components={{ img: ({ alt }) => <span className="resource-fallback">图片需经过来源检查：{alt || '未提供说明'}</span>,
              a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer noopener">{children}</a> }}>{block.text}</ReactMarkdown></div>
          : <div key={index} className="resource-fallback">{'caption' in block && <strong>{block.caption}</strong>}<p>{block.fallback_text}</p></div>)}
      </div>}
    </div>
    {node.children.length > 0 && <div className="child-section"><div className="card-heading"><h3>下一层知识</h3>
      {!leaf && !stale && <button className="text-button" disabled={disabled} onClick={() => void action('expansion', true)}>重新生成这一层</button>}</div>
      <div className="child-grid">{node.children.map(id => {
        const child = space.nodes.find(item => item.id === id);
        return child && <button className="child-card" key={id} onClick={() => choose(id)}><span className="child-kind">{child.kind === 'group' ? '教学分组' : '知识节点'}</span><strong>{child.title} <span>↗</span></strong><p>{child.overview}</p>
          <small>{child.decision.status === 'stale' ? '待重评' : child.decision.state === 'leaf' ? '当前目标的叶子' : '可继续展开'}</small></button>;
      })}</div>
    </div>}
    {space.relations.some(item => item.source_id === node.id || item.target_id === node.id) && <div className="related-nodes"><h3>相关知识与联系</h3>
      {space.relations.filter(item => item.source_id === node.id || item.target_id === node.id).map(item => {
        const related = space.nodes.find(other => other.id === (item.source_id === node.id ? item.target_id : item.source_id));
        return related && <button className="child-card" key={item.id} onClick={() => choose(related.id)}><strong>{related.title} ↗</strong><p>{item.explanation}</p></button>;
      })}
    </div>}
  </section>;
}
