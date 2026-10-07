CREATE TABLE agent_steps (
	run_id VARCHAR(80) NOT NULL, 
	number INTEGER NOT NULL, 
	decision JSONB NOT NULL, 
	origin VARCHAR(32) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	input_digest VARCHAR(64) NOT NULL, 
	invocation_id VARCHAR(80), 
	observation JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (run_id, number), 
	CONSTRAINT ck_agent_step CHECK (number > 0 AND status IN ('decision_saved','tool_started','tool_completed','stopped')), 
	FOREIGN KEY(run_id) REFERENCES generation_runs (id), 
	FOREIGN KEY(invocation_id) REFERENCES tool_invocations (id)
);
