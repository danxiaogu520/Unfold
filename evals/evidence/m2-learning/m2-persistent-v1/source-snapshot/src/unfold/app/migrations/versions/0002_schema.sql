CREATE TABLE learning_feedback (
	id VARCHAR(80) NOT NULL, 
	space_id VARCHAR(80) NOT NULL, 
	node_id VARCHAR(80), 
	profile_revision INTEGER NOT NULL, 
	content_revision INTEGER, 
	kind VARCHAR(32) NOT NULL, 
	text TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_feedback_item_space FOREIGN KEY(space_id, node_id) REFERENCES outline_items (space_id, id), 
	CONSTRAINT fk_feedback_profile_space FOREIGN KEY(space_id, profile_revision) REFERENCES profile_revisions (space_id, revision), 
	FOREIGN KEY(space_id) REFERENCES spaces (id)
)
-- unfold statement --
CREATE TABLE structure_suggestions (
	id VARCHAR(80) NOT NULL, 
	space_id VARCHAR(80) NOT NULL, 
	node_id VARCHAR(80) NOT NULL, 
	profile_revision INTEGER NOT NULL, 
	content_revision INTEGER NOT NULL, 
	proposal JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_suggestion_item_space FOREIGN KEY(space_id, node_id) REFERENCES outline_items (space_id, id), 
	CONSTRAINT fk_suggestion_profile_space FOREIGN KEY(space_id, profile_revision) REFERENCES profile_revisions (space_id, revision), 
	FOREIGN KEY(space_id) REFERENCES spaces (id)
)
