-- Preserve historical 1-5 results; new TF-IDF runs store raw cosine (0-1).
ALTER TABLE preferences ADD COLUMN preference_text text NOT NULL DEFAULT '';
ALTER TABLE individual_scores DROP CONSTRAINT individual_scores_score_check;
ALTER TABLE individual_scores ADD CHECK(score BETWEEN 0 AND 5);
ALTER TABLE group_scores DROP CONSTRAINT group_scores_mean_score_check;
ALTER TABLE group_scores ADD CHECK(mean_score BETWEEN 0 AND 5);
INSERT INTO schema_migrations(version) VALUES(4);
