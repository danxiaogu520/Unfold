CREATE TABLE resource_candidates (
	id VARCHAR(80) NOT NULL, 
	run_id VARCHAR(80) NOT NULL, 
	attempt INTEGER NOT NULL, 
	file JSONB NOT NULL, 
	source JSONB NOT NULL, 
	proposal JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_resource_candidate_attempt FOREIGN KEY(run_id, attempt) REFERENCES run_attempts (run_id, attempt)
);
CREATE TABLE resource_revisions (
	id VARCHAR(80) NOT NULL, 
	task_id VARCHAR(80) NOT NULL, 
	revision INTEGER NOT NULL, 
	candidate_id VARCHAR(80) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_resource_revision UNIQUE (task_id, revision), 
	CONSTRAINT ck_resource_revision CHECK (revision > 0), 
	FOREIGN KEY(task_id) REFERENCES resource_tasks (id), 
	FOREIGN KEY(candidate_id) REFERENCES resource_candidates (id)
);
CREATE TABLE tool_invocations (
	id VARCHAR(80) NOT NULL, 
	run_id VARCHAR(80) NOT NULL, 
	attempt INTEGER NOT NULL, 
	step INTEGER NOT NULL, 
	tool VARCHAR(80) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	input JSONB NOT NULL, 
	output JSONB, 
	outcome_unknown BOOLEAN DEFAULT 'false' NOT NULL, 
	latency_seconds NUMERIC(12, 4), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_tool_attempt_step UNIQUE (run_id, attempt, step), 
	CONSTRAINT fk_tool_attempt FOREIGN KEY(run_id, attempt) REFERENCES run_attempts (run_id, attempt), 
	CONSTRAINT ck_tool_state CHECK (step > 0 AND status IN ('started','completed','failed'))
);
