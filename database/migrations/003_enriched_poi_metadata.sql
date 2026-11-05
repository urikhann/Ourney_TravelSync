ALTER TABLE poi ADD COLUMN enriched_metadata jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(enriched_metadata) = 'object');
INSERT INTO schema_migrations(version) VALUES(3);
