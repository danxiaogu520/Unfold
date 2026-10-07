export interface ApiError {
  code: string;
  message: string;
  retryable: boolean;
  outcome_unknown: boolean;
}

export interface Run {
  schema_version: 'run.v3';
  run_id: string;
  operation: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'conflicted';
  attempt: number;
  last_event_seq: number;
  result_ref: string | null;
  result_kind: 'knowledge' | 'structure_preview' | 'exercise_set' | 'resource' | null;
  error: ApiError | null;
}

export interface RunEvent { schema_version: 'event.v3'; run_id: string; sequence: number; kind: string; snapshot: Run }
export interface Option { key: string; text: string }
export interface Question {
  key: string;
  target: string;
  prompt: string;
  mode: 'single' | 'multiple';
  options: Option[];
  allow_unknown: true;
  allow_free_text: true;
}
export interface Questionnaire { schema_version: 'questionnaire.v1'; questions: Question[]; rationale: string }
export interface Answer { question_key: string; selected_keys: string[]; free_text: string | null; unknown: boolean }
export interface Fact { value: string; source: 'user' | 'assumption' | 'unknown'; evidence_keys: string[] }
export interface Profile {
  schema_version: 'profile.v1';
  topic: Fact;
  primary_purpose: Fact;
  secondary_purposes: Fact[];
  background: Fact;
  target_abilities: Fact[];
  included_scope: Fact[];
  excluded_scope: Fact[];
  constraints: Fact[];
}
export interface Intake {
  id: string;
  questionnaire: Questionnaire;
  answers: Answer[] | null;
  questions_asked: number;
  round: number;
}
export interface SpaceSummary {
  id: string;
  domain: string;
  user_brief: string;
  profile_revision: number;
  outline_revision: number;
  root_id: string | null;
  created_at: string;
}
export interface Content {
  id: string;
  revision: number;
  profile_revision: number;
  overview: string;
  blocks: ({ type: 'markdown'; text: string } | { type: 'diagram'; source: string; caption: string; fallback_text: string }
    | { type: 'image' | 'playground' | 'interactive'; resource_key: string; caption: string; fallback_text: string }
    | { type: 'exercise'; exercise_revision_ref: string; fallback_text: string })[];
  source: Record<string, unknown>;
  review_status: 'unverified' | 'reported';
  resources: import('./ResourceMedia').ResourceState[];
}
export interface Node {
  id: string;
  parent_id: string | null;
  concept_id: string | null;
  kind: 'group' | 'concept';
  active: boolean;
  revision: number;
  title: string;
  scope: string;
  overview: string;
  decision: { state: 'leaf' | 'expandable'; reason: string; profile_revision: number; status: 'current' | 'stale' };
  children: string[];
  contents: Content[];
  exercise_sets: import('./PracticePanel').ExerciseSet[];
  suggestions: {
    id: string; operation: string; status: 'pending' | 'applied' | 'dismissed' | 'stale';
    proposal: { restructuring_note?: string; division_basis?: string;
      decision?: { state: 'leaf' | 'expandable'; reason: string };
      root?: { title: string; scope: string }; children?: { title: string; scope: string; overview: string }[] };
    profile_revision: number; content_revision: number;
  }[];
}
export interface LearningAction {
  operation: 'outline' | 'expansion' | 'reassessment' | 'explanation' | 'exercises';
  expected_profile_revision: number;
  expected_base_revision: number | null;
  target_id: string | null;
  instruction?: string;
  regenerate?: boolean;
  count?: number;
  context_strategy?: 'all_history' | 'current_path' | 'structural_relevance';
}
export interface Space extends SpaceSummary {
  intakes: Intake[]; profile: Profile | null; runs: Run[]; nodes: Node[];
  relations: { id: string; source_id: string; target_id: string; kind: string; explanation: string }[];
}
export interface CreateRun {
  schema_version: 'api.v1';
  space_id: string;
  operation: string;
  profile_revision: number | null;
  base_revision: number | null;
  input: Record<string, unknown>;
}

export const active = (run: Run | null) => run?.status === 'queued' || run?.status === 'running';
export const statusLabel: Record<Run['status'], string> = {
  queued: '等待开始', running: '正在生成', succeeded: '已保存', failed: '生成失败',
  cancelled: '已取消', conflicted: '设定已变化',
};
