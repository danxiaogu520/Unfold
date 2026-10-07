CREATE TABLE resource_tasks (
	id VARCHAR(80) NOT NULL, 
	space_id VARCHAR(80) NOT NULL, 
	content_id VARCHAR(80) NOT NULL, 
	source_run_id VARCHAR(80) NOT NULL, 
	request_key VARCHAR(80) NOT NULL, 
	request JSONB NOT NULL, 
	status VARCHAR(16) DEFAULT 'pending' NOT NULL, 
	dispatched_run_id VARCHAR(80), 
	error JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_resource_content_request UNIQUE (content_id, request_key), 
	CONSTRAINT fk_resource_content_space FOREIGN KEY(space_id, content_id) REFERENCES content_revisions (space_id, id), 
	CONSTRAINT fk_resource_source_run_space FOREIGN KEY(space_id, source_run_id) REFERENCES generation_runs (space_id, id), 
	CONSTRAINT ck_resource_task_status CHECK (status IN ('pending','dispatched','failed')), 
	CONSTRAINT ck_resource_dispatch_run CHECK ((status='dispatched') = (dispatched_run_id IS NOT NULL)), 
	FOREIGN KEY(space_id) REFERENCES spaces (id), 
	FOREIGN KEY(dispatched_run_id) REFERENCES generation_runs (id)
);
CREATE INDEX ix_pending_resource_task ON resource_tasks (status, created_at);
