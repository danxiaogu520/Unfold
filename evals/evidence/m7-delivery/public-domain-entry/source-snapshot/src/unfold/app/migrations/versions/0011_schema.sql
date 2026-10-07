CREATE TABLE content_summaries (
	space_id VARCHAR(80) NOT NULL, 
	content_id VARCHAR(80) NOT NULL, 
	basis_digest VARCHAR(64) NOT NULL, 
	dependencies JSONB NOT NULL, 
	summary JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (space_id, content_id, basis_digest), 
	CONSTRAINT fk_summary_content_space FOREIGN KEY(space_id, content_id) REFERENCES content_revisions (space_id, id), 
	FOREIGN KEY(space_id) REFERENCES spaces (id)
);
