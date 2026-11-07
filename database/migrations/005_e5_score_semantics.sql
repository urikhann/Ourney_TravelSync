-- Additive: retain all historical values and their original config_snapshot.
ALTER TABLE individual_scores ADD COLUMN scoring_details jsonb NOT NULL DEFAULT '{}'
    CHECK(jsonb_typeof(scoring_details)='object');
CREATE OR REPLACE FUNCTION guard_individual_score_scale() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE method text; value double precision;
BEGIN
    SELECT coalesce(config_snapshot->>'individual_method','binary') INTO method
      FROM recommendation_runs WHERE run_id=NEW.run_id;
    IF TG_TABLE_NAME='individual_scores' THEN value=NEW.score; ELSE value=NEW.mean_score; END IF;
    IF method NOT IN ('binary','feature_mean','tfidf','e5_hybrid')
       OR NOT (value >= 0 AND value <= 5)
       OR (method IN ('tfidf','e5_hybrid') AND value>1)
       OR (method IN ('binary','feature_mean') AND value<1) THEN
        RAISE EXCEPTION 'SCORE_SCALE_MISMATCH' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
INSERT INTO schema_migrations(version) VALUES(5);
