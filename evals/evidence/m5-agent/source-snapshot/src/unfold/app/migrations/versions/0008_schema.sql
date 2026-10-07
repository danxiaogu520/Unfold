CREATE TABLE exercise_sets (
	id VARCHAR(80) NOT NULL, 
	space_id VARCHAR(80) NOT NULL, 
	node_id VARCHAR(80) NOT NULL, 
	content_id VARCHAR(80) NOT NULL, 
	revision INTEGER NOT NULL, 
	profile_revision INTEGER NOT NULL, 
	source_run_id VARCHAR(80) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_exercise_set_revision UNIQUE (node_id, revision), 
	CONSTRAINT uq_exercise_set_space_id UNIQUE (space_id, id), 
	CONSTRAINT fk_exercise_node_space FOREIGN KEY(space_id, node_id) REFERENCES outline_items (space_id, id), 
	CONSTRAINT fk_exercise_content_space FOREIGN KEY(space_id, content_id) REFERENCES content_revisions (space_id, id), 
	CONSTRAINT ck_exercise_set_versions CHECK (revision > 0 AND profile_revision > 0), 
	FOREIGN KEY(space_id) REFERENCES spaces (id), 
	FOREIGN KEY(source_run_id) REFERENCES generation_runs (id)
);
CREATE TABLE exercise_questions (
	id VARCHAR(80) NOT NULL, 
	space_id VARCHAR(80) NOT NULL, 
	set_id VARCHAR(80) NOT NULL, 
	position INTEGER NOT NULL, 
	proposal JSONB NOT NULL, 
	status VARCHAR(16) DEFAULT 'active' NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_exercise_question_set_space FOREIGN KEY(space_id, set_id) REFERENCES exercise_sets (space_id, id), 
	CONSTRAINT uq_exercise_question_position UNIQUE (set_id, position), 
	CONSTRAINT ck_exercise_question_status CHECK (status IN ('active','disabled') AND position >= 0), 
	FOREIGN KEY(space_id) REFERENCES spaces (id)
);
CREATE TABLE exercise_answers (
	id VARCHAR(80) NOT NULL, 
	question_id VARCHAR(80) NOT NULL, 
	idempotency_key VARCHAR(128) NOT NULL, 
	selected_keys JSONB NOT NULL, 
	correct BOOLEAN NOT NULL, 
	result JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_answer_identity UNIQUE (question_id, idempotency_key), 
	FOREIGN KEY(question_id) REFERENCES exercise_questions (id)
);
CREATE TABLE exercise_issues (
	id VARCHAR(80) NOT NULL, 
	question_id VARCHAR(80) NOT NULL, 
	text TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(question_id) REFERENCES exercise_questions (id)
);
