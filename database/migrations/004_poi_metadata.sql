-- Separate enrichment from immutable binary features and POI identity.
CREATE TABLE poi_metadata (
    place_id text NOT NULL,
    dataset_version uuid NOT NULL,
    metadata jsonb NOT NULL CHECK (jsonb_typeof(metadata) = 'object'),
    PRIMARY KEY (place_id, dataset_version),
    FOREIGN KEY (dataset_version, place_id) REFERENCES poi(dataset_version, place_id)
);
-- Preserve the exact enrichment already visible to historical runs.
INSERT INTO poi_metadata(place_id, dataset_version, metadata)
SELECT place_id, dataset_version, enriched_metadata FROM poi WHERE enriched_metadata <> '{}';

CREATE FUNCTION guard_metadata_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Serialize against run creation (which takes a SHARE lock on this dataset).
    PERFORM 1 FROM dataset_versions WHERE dataset_version=NEW.dataset_version FOR UPDATE;
    IF EXISTS(SELECT 1 FROM recommendation_runs WHERE dataset_version=NEW.dataset_version) THEN
        RAISE EXCEPTION 'METADATA_RUN_EXISTS: create a new dataset snapshot' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER metadata_insert BEFORE INSERT ON poi_metadata FOR EACH ROW EXECUTE FUNCTION guard_metadata_insert();
CREATE TRIGGER metadata_immutable BEFORE UPDATE OR DELETE ON poi_metadata FOR EACH ROW EXECUTE FUNCTION immutable_row();
ALTER TABLE preferences ADD COLUMN preference_text text NOT NULL DEFAULT '';
ALTER TABLE individual_scores DROP CONSTRAINT individual_scores_score_check;
ALTER TABLE individual_scores ADD CHECK(score BETWEEN 0 AND 5);
ALTER TABLE group_scores DROP CONSTRAINT group_scores_mean_score_check;
ALTER TABLE group_scores ADD CHECK(mean_score BETWEEN 0 AND 5);
CREATE FUNCTION guard_individual_score_scale() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE method text; value double precision;
BEGIN
    SELECT coalesce(config_snapshot->>'individual_method','binary') INTO method FROM recommendation_runs WHERE run_id=NEW.run_id;
    IF TG_TABLE_NAME='individual_scores' THEN value=NEW.score; ELSE value=NEW.mean_score; END IF;
    IF (method='tfidf' AND (value<0 OR value>1)) OR (method='binary' AND (value<1 OR value>5)) THEN
        RAISE EXCEPTION 'SCORE_SCALE_MISMATCH' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER individual_scale BEFORE INSERT ON individual_scores FOR EACH ROW EXECUTE FUNCTION guard_individual_score_scale();
CREATE TRIGGER group_scale BEFORE INSERT ON group_scores FOR EACH ROW EXECUTE FUNCTION guard_individual_score_scale();
INSERT INTO schema_migrations(version) VALUES(4);
