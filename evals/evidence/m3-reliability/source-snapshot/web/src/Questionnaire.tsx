import { useState } from 'react';
import type { Answer, Questionnaire as QuestionnaireData } from './types';

export function Questionnaire({ value, saved, disabled, onSubmit, onFollowup }: {
  value: QuestionnaireData;
  saved: Answer[] | null;
  disabled: boolean;
  onSubmit: (answers: Answer[]) => Promise<void>;
  onFollowup?: (answers: Answer[]) => Promise<void>;
}) {
  const [answers, setAnswers] = useState<Record<string, Answer>>(
    Object.fromEntries((saved ?? []).map(answer => [answer.question_key, answer])),
  );
  const [error, setError] = useState('');
  const update = (key: string, change: Partial<Answer>) => setAnswers(previous => ({
    ...previous, [key]: { ...(previous[key] ?? { question_key: key, selected_keys: [], free_text: null, unknown: false }), ...change },
  }));
  return <form onSubmit={event => {
    event.preventDefault();
    const rows = value.questions.map(question => answers[question.key]);
    if (rows.some(answer => !answer || (!answer.unknown && !answer.selected_keys.length && !answer.free_text?.trim()))) {
      setError('请回答每一题，也可以选择「暂不确定」。'); return;
    }
    setError('');
    const submitter = (event.nativeEvent as SubmitEvent).submitter;
    if (submitter instanceof HTMLButtonElement && submitter.dataset.action === 'followup' && onFollowup) void onFollowup(rows);
    else void onSubmit(rows);
  }}>
    {value.questions.map((question, index) => {
      const answer = answers[question.key];
      return <fieldset key={question.key} disabled={disabled || saved !== null} className="question">
        <legend><span className="question-number">{String(index + 1).padStart(2, '0')}</span>{question.prompt}</legend>
        <p className="hint">{question.mode === 'single' ? '单选' : '可多选'} · 选择符合你的情况</p>
        <div className="option-list">{question.options.map(option => <label key={option.key} className="option">
          <input type={question.mode === 'single' ? 'radio' : 'checkbox'} name={question.key}
            checked={answer?.selected_keys.includes(option.key) ?? false}
            onChange={event => update(question.key, {
              unknown: false,
              selected_keys: question.mode === 'single' ? [option.key]
                : event.target.checked ? [...(answer?.selected_keys ?? []), option.key]
                : (answer?.selected_keys ?? []).filter(key => key !== option.key),
            })} />{option.text}
        </label>)}
          <label className="option unknown"><input type="checkbox" checked={answer?.unknown ?? false}
            onChange={event => update(question.key, { unknown: event.target.checked, selected_keys: [], free_text: null })} />暂不确定</label>
        </div>
        <label className="supplement">补充说明（选填）<textarea rows={2} maxLength={6000}
          value={answer?.free_text ?? ''} placeholder="有其他想法，也可以直接告诉我…"
          onChange={event => update(question.key, { unknown: false, free_text: event.target.value.trim() ? event.target.value : null })} /></label>
      </fieldset>;
    })}
    {error && <p role="alert" className="error">{error}</p>}
    <div className="form-end"><p className="hint">不确定的部分会保留为未知，不会当作你的回答。</p>
      <button className="primary" disabled={disabled}>整理学习设定 <span aria-hidden="true">→</span></button></div>
    {onFollowup && <button className="text-button" type="submit" data-action="followup" disabled={disabled}>还有影响范围的信息，补充一轮问题</button>}
  </form>;
}
