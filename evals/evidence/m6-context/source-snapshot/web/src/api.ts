import type { ApiError, CreateRun, Run, RunEvent } from './types';
import { active } from './types';

export class RequestError extends Error {
  constructor(readonly detail: ApiError, readonly status: number) { super(detail.message); }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!response.ok) {
    let error: ApiError;
    try { error = await response.json() as ApiError; }
    catch { error = { code: 'connection', message: '服务暂时不可用，请稍后重试。', retryable: true, outcome_unknown: false }; }
    throw new RequestError(error, response.status);
  }
  return response.json() as Promise<T>;
}

export function createRun(input: CreateRun, key = crypto.randomUUID()): Promise<Run> {
  return api('/v1/runs', { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(input) });
}

// Closing the stream only stops observation. Cancellation is a separate HTTP action.
export function observeRun(run: Run, onChange: (state: Run) => void, onEvent: (event: RunEvent) => void): () => void {
  let stopped = false;
  let lastSequence = run.last_event_seq;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let source: EventSource | null = null;
  const close = () => { stopped = true; source?.close(); clearTimeout(timer); };
  const scheduleSnapshot = () => {
    source?.close();
    if (!stopped && !timer) timer = setTimeout(async () => {
      timer = undefined;
      try {
        const snapshot = await api<Run>(`/v1/runs/${encodeURIComponent(run.run_id)}`);
        if (stopped) return;
        if (snapshot.schema_version !== 'run.v3' || snapshot.run_id !== run.run_id) throw new Error('unsupported snapshot');
        lastSequence = Math.max(lastSequence, snapshot.last_event_seq);
        onChange(snapshot);
        if (!active(snapshot)) close();
        else connect();
      } catch { scheduleSnapshot(); }
    }, 1000);
  };
  const connect = () => {
    if (stopped) return;
    source?.close();
    source = new EventSource(`/api/v1/runs/${encodeURIComponent(run.run_id)}/events?after=${lastSequence}`);
    source.onmessage = message => {
      try {
        const event = JSON.parse(message.data) as RunEvent;
        if (event.schema_version !== 'event.v3' || event.run_id !== run.run_id
            || event.snapshot.schema_version !== 'run.v3' || event.sequence !== event.snapshot.last_event_seq) {
          throw new Error('unsupported event snapshot');
        }
        if (event.sequence <= lastSequence) return;
        lastSequence = event.sequence;
        onEvent(event);
        onChange(event.snapshot);
        if (!active(event.snapshot)) close();
      } catch { scheduleSnapshot(); }
    };
    source.onerror = () => scheduleSnapshot();
  };
  connect();
  return close;
}
