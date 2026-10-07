import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import type { Content, LearningAction, Node, Space } from './types';

export interface AnswerResult {
  answer_id: string; question_id: string; correct: boolean; selected_keys: string[];
  correct_keys: string[]; explanation: string; content_refs: string[];
}
export interface SavedQuestion {
  exercise_revision_ref: string; stem: string; testing_point: string; mode: 'single' | 'multiple';
  options: { key: string; text: string }[]; status: 'active' | 'disabled'; attempts: AnswerResult[];
}
export interface ExerciseSet {
  id: string; revision: number; content_id: string; profile_revision: number; questions: SavedQuestion[];
}

export function PracticePanel({ space, node, content, disabled, onAction, onChanged }: {
  space: Space; node: Node; content: Content; disabled: boolean;
  onAction: (action: LearningAction) => Promise<void>; onChanged: () => Promise<void>;
}) {
  const [count, setCount] = useState(3);
  const [setId, setSetId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Record<string, string[]>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, AnswerResult>>({});
  const [issueId, setIssueId] = useState<string | null>(null);
  const [issue, setIssue] = useState('');
  const [error, setError] = useState('');
  const identities = useRef<Record<string, { selection: string; key: string }>>({});
  useEffect(() => { setSetId(null); setSelected({}); setResults({}); setError(''); setIssueId(null); }, [content.id]);
  const sets = node.exercise_sets.filter(item => item.content_id === content.id);
  const saved = sets.find(item => item.id === setId) ?? sets[0];
  const generate = () => void onAction({ operation: 'exercises', target_id: node.id,
    expected_profile_revision: space.profile_revision, expected_base_revision: node.revision,
    regenerate: Boolean(saved), count });
  const submit = async (question: SavedQuestion) => {
    const id = question.exercise_revision_ref;
    const choices = selected[id] ?? [];
    const signature = JSON.stringify([...choices].sort());
    if (identities.current[id]?.selection !== signature) identities.current[id] = { selection: signature, key: crypto.randomUUID() };
    setBusy(id); setError('');
    try {
      const result = await api<AnswerResult>(`/v1/spaces/${space.id}/questions/${id}/answers`, {
        method: 'POST', headers: { 'Idempotency-Key': identities.current[id].key },
        body: JSON.stringify({ selected_keys: choices }),
      });
      setResults(old => ({ ...old, [id]: result }));
      await onChanged();
    } catch (problem) { setError(problem instanceof Error ? problem.message : '作答保存失败'); }
    finally { setBusy(null); }
  };
  return <section className="practice-panel" aria-label="节点练习">
    <div className="content-heading"><h3>检验一下理解</h3>
      {content.revision === node.revision && <div className="practice-controls">
        <label>题量 <select aria-label="练习题量" value={count} onChange={event => setCount(Number(event.target.value))}>
          {[1, 2, 3, 4, 5].map(number => <option key={number} value={number}>{number} 题</option>)}</select></label>
        <button className="secondary" disabled={disabled} onClick={generate}>{saved ? '生成新版练习' : '生成练习'}</button>
      </div>}
    </div>
    <p className="hint">题目针对所读内容版本，单选和多选均按完整答案判分。题目答案仍需核对，少量答对不代表掌握。</p>
    {sets.length > 1 && <label>练习版本 <select aria-label="练习版本" value={saved?.id} onChange={event => setSetId(event.target.value)}>
      {sets.map(item => <option key={item.id} value={item.id}>v{item.revision} · 设定 v{item.profile_revision}</option>)}</select></label>}
    {!saved && <p className="hint">此内容版本还没有保存练习。</p>}
    {saved?.questions.map((question, index) => {
      const id = question.exercise_revision_ref;
      const choices = selected[id] ?? [];
      const result = results[id] ?? question.attempts.at(-1);
      return <article className="practice-question" key={id}>
        <h4>{index + 1}. {question.stem} <small>{question.mode === 'single' ? '单选' : '多选'}</small></h4>
        <p className="hint">考查点 · {question.testing_point}</p>
        <fieldset disabled={question.status === 'disabled' || busy === id}>
          <legend className="sr-only">第 {index + 1} 题的选项</legend>
          {question.options.map(option => <label className="practice-option" key={option.key}>
            <input type={question.mode === 'single' ? 'radio' : 'checkbox'} name={id} checked={choices.includes(option.key)} onChange={() => {
              setSelected(old => ({ ...old, [id]: question.mode === 'single' ? [option.key]
                : choices.includes(option.key) ? choices.filter(key => key !== option.key) : [...choices, option.key] }));
            }} />{option.text}</label>)}
        </fieldset>
        <div className="node-actions"><button className="primary" disabled={busy !== null || question.status === 'disabled' || !choices.length}
          onClick={() => void submit(question)}>{busy === id ? '正在保存…' : '提交作答'}</button>
          <button className="text-button" disabled={busy !== null || question.status === 'disabled'} onClick={() => { setIssueId(id); setIssue(''); }}>题目有问题</button></div>
        {question.status === 'disabled' && <p className="review-note">此题已报告问题并停用；历史作答保留，可生成新版题目。</p>}
        {result && <div className={`answer-result ${result.correct ? 'correct' : 'incorrect'}`} role="status">
          <strong>{result.correct ? '本次答案符合已保存答案' : '本次答案与已保存答案不同'}</strong>
          <p>参考答案：{question.options.filter(option => result.correct_keys.includes(option.key)).map(option => option.text).join('；')}</p>
          <p>{result.explanation}</p><small>已保存作答 {question.attempts.length} 次 · 内容 v{content.revision}</small>
        </div>}
      </article>;
    })}
    {issueId && <div className="exercise-issue"><label>题目问题说明<textarea aria-label="题目问题说明" maxLength={6000} value={issue} onChange={event => setIssue(event.target.value)} /></label>
      <button className="secondary" disabled={!issue.trim() || busy !== null} onClick={() => {
        setBusy(issueId); setError(''); void api(`/v1/spaces/${space.id}/questions/${issueId}/issues`, { method: 'POST', body: JSON.stringify({ text: issue.trim() }) })
          .then(async () => { setIssueId(null); await onChanged(); }).catch(problem => setError(problem instanceof Error ? problem.message : '反馈保存失败')).finally(() => setBusy(null));
      }}>报告问题并停用此题</button><button className="text-button" onClick={() => setIssueId(null)}>取消</button>
    </div>}
    {error && <p role="alert" className="error">{error}</p>}
  </section>;
}
