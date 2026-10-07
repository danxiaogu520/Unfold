CREATE TABLE spaces (
	id VARCHAR(80) NOT NULL, 
	owner VARCHAR(80) DEFAULT 'local' NOT NULL, 
	domain TEXT NOT NULL, 
	user_brief TEXT NOT NULL, 
	profile_revision INTEGER DEFAULT '0' NOT NULL, 
	outline_revision INTEGER DEFAULT '0' NOT NULL, 
	root_id VARCHAR(80), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_space_revisions CHECK (profile_revision >= 0 AND outline_revision >= 0)
)
-- unfold statement --
CREATE TABLE budget_ledgers (
	id VARCHAR(80) NOT NULL, 
	currency VARCHAR(8) NOT NULL, 
	limit_cost NUMERIC(18, 8) NOT NULL, 
	accounted_cost NUMERIC(18, 8) DEFAULT '0' NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_ledger_cost CHECK (limit_cost > 0 AND accounted_cost >= 0)
)
-- unfold statement --
CREATE TABLE profile_revisions (
	id VARCHAR(80) NOT NULL, 
	space_id VARCHAR(80) NOT NULL, 
	revision INTEGER NOT NULL, 
	proposal JSONB NOT NULL, 
	evidence JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_profile_revision UNIQUE (space_id, revision), 
	CONSTRAINT ck_profile_revision_positive CHECK (revision > 0), 
	FOREIGN KEY(space_id) REFERENCES spaces (id)
)
-- unfold statement --
CREATE TABLE intake_sessions (
	id VARCHAR(80) NOT NULL, 
	space_id VARCHAR(80) NOT NULL, 
	questionnaire JSONB NOT NULL, 
	answers JSONB, 
	questions_asked INTEGER NOT NULL, 
	round INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_intake_limits CHECK (questions_asked BETWEEN 0 AND 5 AND round BETWEEN 1 AND 2), 
	FOREIGN KEY(space_id) REFERENCES spaces (id)
)
-- unfold statement --
CREATE TABLE concepts (
	id VARCHAR(80) NOT NULL, 
	space_id VARCHAR(80) NOT NULL, 
	title TEXT NOT NULL, 
	scope TEXT NOT NULL, 
	aliases JSONB NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_concept_space_id UNIQUE (space_id, id), 
	FOREIGN KEY(space_id) REFERENCES spaces (id)
)
-- unfold statement --
CREATE TABLE outline_items (
	id VARCHAR(80) NOT NULL, 
	space_id VARCHAR(80) NOT NULL, 
	parent_id VARCHAR(80), 
	concept_id VARCHAR(80), 
	position INTEGER NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	title TEXT NOT NULL, 
	scope TEXT NOT NULL, 
	overview TEXT NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	active BOOLEAN DEFAULT 'true' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_item_space_id UNIQUE (space_id, id), 
	CONSTRAINT fk_item_parent_space FOREIGN KEY(space_id, parent_id) REFERENCES outline_items (space_id, id), 
	CONSTRAINT fk_item_concept_space FOREIGN KEY(space_id, concept_id) REFERENCES concepts (space_id, id), 
	CONSTRAINT ck_item_not_own_parent CHECK (parent_id IS NULL OR parent_id <> id), 
	CONSTRAINT ck_item_revision_position CHECK (revision > 0 AND position >= 0), 
	CONSTRAINT ck_item_kind CHECK (kind IN ('concept','group')), 
	CONSTRAINT ck_item_group_identity CHECK ((kind='group' AND concept_id IS NULL) OR kind='concept'), 
	FOREIGN KEY(space_id) REFERENCES spaces (id)
)
-- unfold statement --
CREATE UNIQUE INDEX uq_active_root ON outline_items (space_id) WHERE active AND parent_id IS NULL
-- unfold statement --
CREATE UNIQUE INDEX uq_active_sibling_position ON outline_items (space_id, parent_id, position) WHERE active
-- unfold statement --
CREATE TABLE node_decisions (
	id VARCHAR(80) NOT NULL, 
	space_id VARCHAR(80) NOT NULL, 
	node_id VARCHAR(80) NOT NULL, 
	profile_revision INTEGER NOT NULL, 
	content_revision INTEGER NOT NULL, 
	state VARCHAR(20) NOT NULL, 
	reason TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_decision_item_space FOREIGN KEY(space_id, node_id) REFERENCES outline_items (space_id, id), 
	CONSTRAINT fk_decision_profile_space FOREIGN KEY(space_id, profile_revision) REFERENCES profile_revisions (space_id, revision), 
	CONSTRAINT uq_node_decision_basis UNIQUE (node_id, profile_revision, content_revision), 
	CONSTRAINT ck_decision_state CHECK (state IN ('leaf','expandable') AND content_revision > 0), 
	FOREIGN KEY(space_id) REFERENCES spaces (id)
)
-- unfold statement --
CREATE TABLE content_revisions (
	id VARCHAR(80) NOT NULL, 
	space_id VARCHAR(80) NOT NULL, 
	node_id VARCHAR(80) NOT NULL, 
	revision INTEGER NOT NULL, 
	profile_revision INTEGER NOT NULL, 
	overview TEXT NOT NULL, 
	blocks JSONB NOT NULL, 
	source JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_content_item_space FOREIGN KEY(space_id, node_id) REFERENCES outline_items (space_id, id), 
	CONSTRAINT fk_content_profile_space FOREIGN KEY(space_id, profile_revision) REFERENCES profile_revisions (space_id, revision), 
	CONSTRAINT uq_content_revision UNIQUE (node_id, revision), 
	CONSTRAINT ck_content_revision_positive CHECK (revision > 0), 
	FOREIGN KEY(space_id) REFERENCES spaces (id)
)
-- unfold statement --
CREATE TABLE relations (
	id VARCHAR(80) NOT NULL, 
	space_id VARCHAR(80) NOT NULL, 
	source_id VARCHAR(80) NOT NULL, 
	target_id VARCHAR(80) NOT NULL, 
	kind VARCHAR(24) NOT NULL, 
	explanation TEXT NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_relation_source_space FOREIGN KEY(space_id, source_id) REFERENCES outline_items (space_id, id), 
	CONSTRAINT fk_relation_target_space FOREIGN KEY(space_id, target_id) REFERENCES outline_items (space_id, id), 
	CONSTRAINT uq_relation_edge UNIQUE (source_id, target_id, kind), 
	CONSTRAINT ck_relation_not_self CHECK (source_id <> target_id), 
	CONSTRAINT ck_relation_kind CHECK (kind IN ('related_to','prerequisite_of','contrasted_with')), 
	FOREIGN KEY(space_id) REFERENCES spaces (id)
)
-- unfold statement --
CREATE TABLE generation_runs (
	id VARCHAR(80) NOT NULL, 
	space_id VARCHAR(80) NOT NULL, 
	operation VARCHAR(24) NOT NULL, 
	target_id VARCHAR(80), 
	profile_revision INTEGER, 
	base_revision INTEGER, 
	idempotency_key VARCHAR(128) NOT NULL, 
	request_digest VARCHAR(64) NOT NULL, 
	active_key VARCHAR(64) NOT NULL, 
	input_payload JSONB NOT NULL, 
	context_dependencies JSONB NOT NULL, 
	status VARCHAR(16) DEFAULT 'queued' NOT NULL, 
	attempt INTEGER DEFAULT '0' NOT NULL, 
	lease_token VARCHAR(80), 
	lease_until TIMESTAMP WITH TIME ZONE, 
	not_before TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	last_event_seq INTEGER DEFAULT '0' NOT NULL, 
	result_ref VARCHAR(80), 
	proposal JSONB, 
	error JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_run_space_id UNIQUE (space_id, id), 
	CONSTRAINT uq_run_request_identity UNIQUE (space_id, idempotency_key), 
	CONSTRAINT fk_run_target_space FOREIGN KEY(space_id, target_id) REFERENCES outline_items (space_id, id), 
	CONSTRAINT ck_run_status CHECK (status IN ('queued','running','succeeded','failed','cancelled','conflicted')), 
	CONSTRAINT ck_run_counters CHECK (attempt >= 0 AND last_event_seq >= 0), 
	CONSTRAINT ck_run_success_result CHECK ((status='succeeded') = (result_ref IS NOT NULL)), 
	CONSTRAINT ck_run_error_state CHECK ((status IN ('failed','conflicted')) = (error IS NOT NULL)), 
	CONSTRAINT ck_running_lease CHECK (status <> 'running' OR (attempt > 0 AND lease_token IS NOT NULL AND lease_until IS NOT NULL)), 
	FOREIGN KEY(space_id) REFERENCES spaces (id)
)
-- unfold statement --
CREATE INDEX ix_claimable_run ON generation_runs (status, not_before, created_at)
-- unfold statement --
CREATE UNIQUE INDEX uq_active_generation ON generation_runs (space_id, active_key) WHERE status IN ('queued','running')
-- unfold statement --
CREATE TABLE request_identities (
	space_id VARCHAR(80) NOT NULL, 
	key VARCHAR(128) NOT NULL, 
	run_id VARCHAR(80) NOT NULL, 
	request_digest VARCHAR(64) NOT NULL, 
	PRIMARY KEY (space_id, key), 
	CONSTRAINT fk_request_run_space FOREIGN KEY(space_id, run_id) REFERENCES generation_runs (space_id, id), 
	CONSTRAINT ck_request_key_nonempty CHECK (length(key) > 0), 
	FOREIGN KEY(space_id) REFERENCES spaces (id)
)
-- unfold statement --
CREATE TABLE run_events (
	run_id VARCHAR(80) NOT NULL, 
	sequence INTEGER NOT NULL, 
	payload JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (run_id, sequence), 
	CONSTRAINT ck_event_sequence_positive CHECK (sequence > 0), 
	FOREIGN KEY(run_id) REFERENCES generation_runs (id)
)
-- unfold statement --
CREATE TABLE run_attempts (
	run_id VARCHAR(80) NOT NULL, 
	attempt INTEGER NOT NULL, 
	lease_token VARCHAR(80) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	outcome_unknown BOOLEAN DEFAULT 'false' NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (run_id, attempt), 
	CONSTRAINT ck_attempt_positive CHECK (attempt > 0), 
	FOREIGN KEY(run_id) REFERENCES generation_runs (id)
)
-- unfold statement --
CREATE TABLE model_calls (
	id VARCHAR(80) NOT NULL, 
	run_id VARCHAR(80) NOT NULL, 
	attempt INTEGER NOT NULL, 
	step INTEGER NOT NULL, 
	provider VARCHAR(32) NOT NULL, 
	model VARCHAR(120) NOT NULL, 
	prompt_version VARCHAR(80) NOT NULL, 
	schema_version VARCHAR(80) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	request JSONB NOT NULL, 
	response JSONB, 
	input_tokens INTEGER, 
	output_tokens INTEGER, 
	reserved_cost NUMERIC(18, 8) DEFAULT '0' NOT NULL, 
	accounted_cost NUMERIC(18, 8), 
	currency VARCHAR(8) DEFAULT 'CNY' NOT NULL, 
	latency_seconds NUMERIC(12, 4), 
	error_code VARCHAR(40), 
	outcome_unknown BOOLEAN DEFAULT 'false' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_call_attempt FOREIGN KEY(run_id, attempt) REFERENCES run_attempts (run_id, attempt), 
	CONSTRAINT uq_call_step UNIQUE (run_id, attempt, step), 
	CONSTRAINT ck_call_step_cost CHECK (step > 0 AND reserved_cost >= 0), 
	CONSTRAINT ck_call_input_tokens CHECK (input_tokens IS NULL OR input_tokens >= 0), 
	CONSTRAINT ck_call_output_tokens CHECK (output_tokens IS NULL OR output_tokens >= 0)
)
-- unfold statement --
ALTER TABLE spaces ADD CONSTRAINT fk_space_root FOREIGN KEY(id, root_id) REFERENCES outline_items (space_id, id)
