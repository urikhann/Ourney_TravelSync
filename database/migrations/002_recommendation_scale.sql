-- Existing normalized results require operator review; never silently rescale.
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM individual_scores) OR EXISTS (SELECT 1 FROM group_scores)
 THEN RAISE EXCEPTION 'EXISTING_RESULTS_REQUIRE_MANUAL_SCALE_MIGRATION'; END IF;
END $$;
ALTER TABLE individual_scores DROP CONSTRAINT individual_scores_score_check;
ALTER TABLE individual_scores ADD CHECK(score BETWEEN 1 AND 5);
ALTER TABLE group_scores DROP CONSTRAINT group_scores_mean_score_check;
ALTER TABLE group_scores ADD CHECK(mean_score BETWEEN 1 AND 5);
ALTER TABLE group_scores ALTER COLUMN selection_gain DROP NOT NULL;
ALTER TABLE group_scores ALTER COLUMN group_utility DROP NOT NULL;
ALTER TABLE group_scores ALTER COLUMN feasible_dates DROP NOT NULL;
INSERT INTO schema_migrations(version) VALUES(2);
