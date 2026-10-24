-- TravelSync baseline 001. Empty database only; apply via scripts.init_db.
CREATE TABLE schema_migrations(version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE users(user_id uuid PRIMARY KEY, auth_subject text NOT NULL UNIQUE, display_name text NOT NULL CHECK(btrim(display_name)<>''), is_admin boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE preferences(user_id uuid REFERENCES users, version integer CHECK(version>0),
food smallint NOT NULL CHECK(food BETWEEN 1 AND 5),
cafe smallint NOT NULL CHECK(cafe BETWEEN 1 AND 5),
nature smallint NOT NULL CHECK(nature BETWEEN 1 AND 5),
history smallint NOT NULL CHECK(history BETWEEN 1 AND 5),
art smallint NOT NULL CHECK(art BETWEEN 1 AND 5),
shopping smallint NOT NULL CHECK(shopping BETWEEN 1 AND 5),
activity smallint NOT NULL CHECK(activity BETWEEN 1 AND 5),
entertainment smallint NOT NULL CHECK(entertainment BETWEEN 1 AND 5),
relax smallint NOT NULL CHECK(relax BETWEEN 1 AND 5),
festival smallint NOT NULL CHECK(festival BETWEEN 1 AND 5),
created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(user_id,version));
CREATE TABLE travel_groups(group_id uuid PRIMARY KEY, name text NOT NULL, owner_id uuid NOT NULL REFERENCES users, status text NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','CLOSED')), created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE group_members(group_id uuid REFERENCES travel_groups, user_id uuid REFERENCES users, role text NOT NULL CHECK(role IN ('OWNER','MEMBER')), joined_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(group_id,user_id));
CREATE UNIQUE INDEX one_owner ON group_members(group_id) WHERE role='OWNER';
ALTER TABLE travel_groups ADD CONSTRAINT owner_is_member FOREIGN KEY(group_id,owner_id) REFERENCES group_members(group_id,user_id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE mapping_versions(mapping_version text PRIMARY KEY, feature_schema_version text NOT NULL, code_sha256 text NOT NULL CHECK(length(code_sha256)=64), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(mapping_version,feature_schema_version));
CREATE TABLE dataset_versions(dataset_version uuid PRIMARY KEY, parent_version uuid REFERENCES dataset_versions, mapping_version text NOT NULL, feature_schema_version text NOT NULL, status text NOT NULL DEFAULT 'BUILDING' CHECK(status IN ('BUILDING','READY')), input_sha256 text NOT NULL CHECK(length(input_sha256)=64), row_count integer NOT NULL DEFAULT 0 CHECK(row_count>=0), created_at timestamptz NOT NULL DEFAULT now(), FOREIGN KEY(mapping_version,feature_schema_version) REFERENCES mapping_versions(mapping_version,feature_schema_version), UNIQUE(dataset_version,mapping_version,feature_schema_version));
CREATE TABLE dataset_head(singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), dataset_version uuid NOT NULL REFERENCES dataset_versions);
CREATE TABLE poi_submission(submission_id uuid PRIMARY KEY, submitted_by uuid NOT NULL REFERENCES users, place_name text NOT NULL CHECK(btrim(place_name)<>''), category_name text NOT NULL CHECK(btrim(category_name)<>''), address text NOT NULL CHECK(btrim(address)<>''), latitude numeric(10,7) NOT NULL CHECK(latitude BETWEEN 32 AND 39), longitude numeric(10,7) NOT NULL CHECK(longitude BETWEEN 124 AND 132), description text, status text NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','APPROVED','REJECTED')), review_note text, validation_evidence jsonb, reviewed_by uuid REFERENCES users, created_at timestamptz NOT NULL DEFAULT now(), reviewed_at timestamptz,
CHECK((status='PENDING' AND reviewed_at IS NULL AND reviewed_by IS NULL) OR (status<>'PENDING' AND reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL)));
CREATE TABLE poi(poi_id uuid NOT NULL, dataset_version uuid NOT NULL REFERENCES dataset_versions, place_id text NOT NULL CHECK(btrim(place_id)<>''), place_name text NOT NULL CHECK(btrim(place_name)<>''), category text, category_name text, cat1 text, cat2 text, cat3 text, address text, road_address text, latitude numeric(10,7) NOT NULL CHECK(latitude BETWEEN 32 AND 39), longitude numeric(10,7) NOT NULL CHECK(longitude BETWEEN 124 AND 132), phone text, place_url text, image_url text, source text NOT NULL CHECK(source IN ('TOUR_API','KAKAO','TOUR_API+KAKAO','USER')), source_place_id text NOT NULL, kakao_place_ids text, feature_rules jsonb NOT NULL, feature_rules_raw text, exclude_reason text,
food smallint NOT NULL CHECK(food IN (0,1)),
cafe smallint NOT NULL CHECK(cafe IN (0,1)),
nature smallint NOT NULL CHECK(nature IN (0,1)),
history smallint NOT NULL CHECK(history IN (0,1)),
art smallint NOT NULL CHECK(art IN (0,1)),
shopping smallint NOT NULL CHECK(shopping IN (0,1)),
activity smallint NOT NULL CHECK(activity IN (0,1)),
entertainment smallint NOT NULL CHECK(entertainment IN (0,1)),
relax smallint NOT NULL CHECK(relax IN (0,1)),
festival smallint NOT NULL CHECK(festival IN (0,1)),
description text, operating_hours jsonb, closed_days jsonb, cost integer CHECK(cost>=0), estimated_stay_time integer CHECK(estimated_stay_time>0), feature_schema_version text NOT NULL, mapping_version text NOT NULL, field_status jsonb NOT NULL DEFAULT '{"operating_hours":"UNKNOWN","closed_days":"UNKNOWN","cost":"UNKNOWN","estimated_stay_time":"UNKNOWN"}', verified_at timestamptz, active boolean NOT NULL DEFAULT true, submission_id uuid REFERENCES poi_submission, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(poi_id,dataset_version), UNIQUE(dataset_version,place_id),
FOREIGN KEY(dataset_version,mapping_version,feature_schema_version) REFERENCES dataset_versions(dataset_version,mapping_version,feature_schema_version),
CHECK((source='USER')=(submission_id IS NOT NULL)),
CHECK(NOT active OR (coalesce(exclude_reason,'')='' AND (food+cafe+nature+history+art+shopping+activity+entertainment+relax+festival)>0)),
CHECK(jsonb_typeof(field_status)='object'));
CREATE TABLE poi_source(poi_id uuid NOT NULL, dataset_version uuid NOT NULL, source text NOT NULL CHECK(source IN ('TOUR_API','KAKAO','USER')), source_id text NOT NULL, source_url text, collected_at timestamptz, raw_ref text, PRIMARY KEY(dataset_version,source,source_id), FOREIGN KEY(poi_id,dataset_version) REFERENCES poi(poi_id,dataset_version));
CREATE TABLE merge_audit(audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, dataset_version uuid NOT NULL REFERENCES dataset_versions, details jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE trip_constraints(constraint_id uuid PRIMARY KEY, group_id uuid NOT NULL REFERENCES travel_groups, version integer NOT NULL CHECK(version>0), daily_windows jsonb NOT NULL CHECK(jsonb_typeof(daily_windows)='array' AND jsonb_array_length(daily_windows) BETWEEN 1 AND 3), budget_total integer NOT NULL CHECK(budget_total>=0), transport_mode text NOT NULL, max_leg_minutes integer CHECK(max_leg_minutes>0), max_total_walk_minutes integer CHECK(max_total_walk_minutes>=0), member_exclusions jsonb NOT NULL DEFAULT '{}', accessibility_requirements jsonb, timezone text NOT NULL DEFAULT 'Asia/Seoul' CHECK(timezone='Asia/Seoul'), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(group_id,version), UNIQUE(constraint_id,group_id));
CREATE TABLE recommendation_runs(run_id uuid PRIMARY KEY, group_id uuid NOT NULL REFERENCES travel_groups, constraint_id uuid NOT NULL, dataset_version uuid NOT NULL REFERENCES dataset_versions, mapping_version text NOT NULL, feature_schema_version text NOT NULL, member_snapshot jsonb NOT NULL, config_snapshot jsonb NOT NULL, input_hash text NOT NULL CHECK(length(input_hash)=64), status text NOT NULL DEFAULT 'CREATED' CHECK(status IN ('CREATED','RUNNING','COMPLETED','FAILED')), error_code text, created_at timestamptz NOT NULL DEFAULT now(), FOREIGN KEY(constraint_id,group_id) REFERENCES trip_constraints(constraint_id,group_id), FOREIGN KEY(dataset_version,mapping_version,feature_schema_version) REFERENCES dataset_versions(dataset_version,mapping_version,feature_schema_version), UNIQUE(run_id,dataset_version));
CREATE TABLE run_preferences(run_id uuid REFERENCES recommendation_runs, user_id uuid NOT NULL, preference_version integer NOT NULL, PRIMARY KEY(run_id,user_id), FOREIGN KEY(user_id,preference_version) REFERENCES preferences(user_id,version));
CREATE TABLE individual_scores(run_id uuid NOT NULL, user_id uuid NOT NULL, poi_id uuid NOT NULL, dataset_version uuid NOT NULL, score double precision NOT NULL CHECK(score BETWEEN 0 AND 1), rank integer CHECK(rank>0), eligible boolean NOT NULL, PRIMARY KEY(run_id,user_id,poi_id), FOREIGN KEY(run_id,user_id) REFERENCES run_preferences, FOREIGN KEY(run_id,dataset_version) REFERENCES recommendation_runs(run_id,dataset_version), FOREIGN KEY(poi_id,dataset_version) REFERENCES poi, CHECK(eligible OR rank IS NULL));
CREATE TABLE group_scores(run_id uuid NOT NULL, poi_id uuid NOT NULL, dataset_version uuid NOT NULL, mean_score double precision NOT NULL CHECK(mean_score BETWEEN 0 AND 1), group_rank integer NOT NULL CHECK(group_rank>0), selection_gain double precision NOT NULL CHECK(selection_gain>=0 AND selection_gain<'Infinity'::float8), group_utility integer NOT NULL CHECK(group_utility BETWEEN 0 AND 1000), feasible_dates jsonb NOT NULL CHECK(jsonb_typeof(feasible_dates)='array'), PRIMARY KEY(run_id,poi_id), UNIQUE(run_id,group_rank), UNIQUE(run_id,poi_id,dataset_version), FOREIGN KEY(run_id,dataset_version) REFERENCES recommendation_runs(run_id,dataset_version), FOREIGN KEY(poi_id,dataset_version) REFERENCES poi);
CREATE TABLE itineraries(itinerary_id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES recommendation_runs, dataset_version uuid NOT NULL, solver_status text NOT NULL CHECK(solver_status IN ('OPTIMAL','FEASIBLE','INFEASIBLE','UNKNOWN','MODEL_INVALID')), validation_status text NOT NULL CHECK(validation_status IN ('PENDING','PASSED','FAILED')), total_cost integer NOT NULL CHECK(total_cost>=0), metrics jsonb NOT NULL, routing_version text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(itinerary_id,run_id,dataset_version), FOREIGN KEY(run_id,dataset_version) REFERENCES recommendation_runs(run_id,dataset_version), CHECK(validation_status<>'PASSED' OR solver_status IN ('OPTIMAL','FEASIBLE')));
CREATE TABLE itinerary_items(itinerary_id uuid NOT NULL, run_id uuid NOT NULL, dataset_version uuid NOT NULL, travel_date date NOT NULL, sequence integer NOT NULL CHECK(sequence>0), poi_id uuid NOT NULL, arrival_min integer NOT NULL CHECK(arrival_min BETWEEN 0 AND 1440), start_min integer NOT NULL, departure_min integer NOT NULL CHECK(departure_min<=1440), travel_minutes integer NOT NULL CHECK(travel_minutes>=0), stay_minutes integer NOT NULL CHECK(stay_minutes>0), group_cost integer NOT NULL CHECK(group_cost>=0), PRIMARY KEY(itinerary_id,travel_date,sequence), UNIQUE(itinerary_id,poi_id), FOREIGN KEY(itinerary_id,run_id,dataset_version) REFERENCES itineraries(itinerary_id,run_id,dataset_version), FOREIGN KEY(run_id,poi_id,dataset_version) REFERENCES group_scores(run_id,poi_id,dataset_version), CHECK(arrival_min<=start_min AND departure_min=start_min+stay_minutes));
CREATE INDEX poi_active ON poi(dataset_version) WHERE active;
CREATE INDEX submissions_pending ON poi_submission(created_at) WHERE status='PENDING';
CREATE INDEX runs_group ON recommendation_runs(group_id,created_at);
CREATE FUNCTION immutable_row() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'IMMUTABLE_RECORD' USING ERRCODE='23514'; END $$;
CREATE TRIGGER immutable_preferences BEFORE UPDATE OR DELETE ON preferences FOR EACH ROW EXECUTE FUNCTION immutable_row();
CREATE TRIGGER immutable_trip_constraints BEFORE UPDATE OR DELETE ON trip_constraints FOR EACH ROW EXECUTE FUNCTION immutable_row();
CREATE TRIGGER immutable_mapping_versions BEFORE UPDATE OR DELETE ON mapping_versions FOR EACH ROW EXECUTE FUNCTION immutable_row();
CREATE TRIGGER immutable_run_preferences BEFORE UPDATE OR DELETE ON run_preferences FOR EACH ROW EXECUTE FUNCTION immutable_row();
CREATE TRIGGER immutable_individual_scores BEFORE UPDATE OR DELETE ON individual_scores FOR EACH ROW EXECUTE FUNCTION immutable_row();
CREATE TRIGGER immutable_group_scores BEFORE UPDATE OR DELETE ON group_scores FOR EACH ROW EXECUTE FUNCTION immutable_row();
CREATE TRIGGER immutable_itineraries BEFORE UPDATE OR DELETE ON itineraries FOR EACH ROW EXECUTE FUNCTION immutable_row();
CREATE TRIGGER immutable_itinerary_items BEFORE UPDATE OR DELETE ON itinerary_items FOR EACH ROW EXECUTE FUNCTION immutable_row();
CREATE FUNCTION freeze_dataset() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.status='READY' THEN RAISE EXCEPTION 'DATASET_IMMUTABLE' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER frozen_dataset BEFORE UPDATE OR DELETE ON dataset_versions FOR EACH ROW EXECUTE FUNCTION freeze_dataset();
CREATE FUNCTION guard_dataset_row() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v uuid; st text;
BEGIN
 IF TG_OP='DELETE' THEN v=OLD.dataset_version; ELSE v=NEW.dataset_version; END IF;
 SELECT status INTO st FROM dataset_versions WHERE dataset_version=v FOR SHARE;
 IF st='READY' THEN RAISE EXCEPTION 'DATASET_IMMUTABLE' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' AND OLD.dataset_version<>NEW.dataset_version THEN RAISE EXCEPTION 'DATASET_MOVE_FORBIDDEN' USING ERRCODE='23514'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
END $$;
CREATE TRIGGER frozen_poi BEFORE INSERT OR UPDATE OR DELETE ON poi FOR EACH ROW EXECUTE FUNCTION guard_dataset_row();
CREATE TRIGGER frozen_source BEFORE INSERT OR UPDATE OR DELETE ON poi_source FOR EACH ROW EXECUTE FUNCTION guard_dataset_row();
CREATE TRIGGER frozen_audit BEFORE INSERT OR UPDATE OR DELETE ON merge_audit FOR EACH ROW EXECUTE FUNCTION guard_dataset_row();
CREATE FUNCTION guard_user_poi() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NEW.source='USER' AND NOT EXISTS(SELECT 1 FROM poi_submission WHERE submission_id=NEW.submission_id AND status='APPROVED') THEN RAISE EXCEPTION 'SUBMISSION_NOT_APPROVED' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER user_poi_approved BEFORE INSERT OR UPDATE ON poi FOR EACH ROW EXECUTE FUNCTION guard_user_poi();
CREATE FUNCTION guard_submission() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.status<>'PENDING' THEN RAISE EXCEPTION 'INITIAL_STATUS_PENDING' USING ERRCODE='23514'; END IF;
 ELSE
  IF OLD.status<>'PENDING' THEN RAISE EXCEPTION 'REVIEW_IMMUTABLE' USING ERRCODE='23514'; END IF;
  IF NEW.status<>'PENDING' AND NOT EXISTS(SELECT 1 FROM users WHERE user_id=NEW.reviewed_by AND is_admin) THEN RAISE EXCEPTION 'ADMIN_REQUIRED' USING ERRCODE='23514'; END IF;
  IF NEW.status='APPROVED' AND (NEW.validation_evidence IS NULL OR NOT (NEW.validation_evidence @> '{"busan_verified":true,"coordinates_verified":true,"tourism_verified":true,"duplicates_checked":true}'::jsonb)) THEN RAISE EXCEPTION 'VALIDATION_REQUIRED' USING ERRCODE='23514'; END IF;
 END IF; RETURN NEW; END $$;
CREATE TRIGGER submission_review BEFORE INSERT OR UPDATE ON poi_submission FOR EACH ROW EXECUTE FUNCTION guard_submission();
CREATE FUNCTION require_registered_submission() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NEW.status='APPROVED' AND NOT EXISTS(SELECT 1 FROM poi WHERE submission_id=NEW.submission_id AND active) THEN RAISE EXCEPTION 'APPROVAL_WITHOUT_POI' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE CONSTRAINT TRIGGER approval_atomic AFTER UPDATE ON poi_submission DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION require_registered_submission();
CREATE FUNCTION require_ready_dataset() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM dataset_versions WHERE dataset_version=NEW.dataset_version AND status='READY') THEN RAISE EXCEPTION 'DATASET_NOT_READY' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER head_ready BEFORE INSERT OR UPDATE ON dataset_head FOR EACH ROW EXECUTE FUNCTION require_ready_dataset();
CREATE TRIGGER run_ready BEFORE INSERT ON recommendation_runs FOR EACH ROW EXECUTE FUNCTION require_ready_dataset();
CREATE FUNCTION protect_run_input() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (to_jsonb(OLD)-'status'-'error_code')<>(to_jsonb(NEW)-'status'-'error_code') THEN RAISE EXCEPTION 'RUN_INPUT_IMMUTABLE' USING ERRCODE='23514'; END IF; RETURN NEW; END $$;
CREATE TRIGGER frozen_run_input BEFORE UPDATE ON recommendation_runs FOR EACH ROW EXECUTE FUNCTION protect_run_input();
INSERT INTO schema_migrations(version) VALUES(1);

CREATE FUNCTION validate_poi_status() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE field text; state text; val jsonb;
BEGIN
 FOREACH field IN ARRAY ARRAY['operating_hours','closed_days','cost','estimated_stay_time'] LOOP
  state=NEW.field_status->>field; val=to_jsonb(NEW)->field;
  IF state IS NULL OR state NOT IN ('UNKNOWN','KNOWN','ESTIMATED') THEN RAISE EXCEPTION 'INVALID_FIELD_STATUS' USING ERRCODE='23514'; END IF;
  IF (state='UNKNOWN') <> (val IS NULL OR val='null'::jsonb) THEN RAISE EXCEPTION 'FIELD_STATUS_VALUE_MISMATCH' USING ERRCODE='23514'; END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER poi_field_status BEFORE INSERT OR UPDATE ON poi FOR EACH ROW EXECUTE FUNCTION validate_poi_status();
CREATE FUNCTION validate_dataset_publish() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NEW.status='READY' AND (NEW.row_count<>(SELECT count(*) FROM poi WHERE dataset_version=NEW.dataset_version) OR NEW.row_count=0) THEN RAISE EXCEPTION 'DATASET_COUNT_MISMATCH' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER dataset_publish BEFORE UPDATE ON dataset_versions FOR EACH ROW EXECUTE FUNCTION validate_dataset_publish();
CREATE FUNCTION validate_run_preferences() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE rid uuid; snap jsonb; actual jsonb;
BEGIN
 rid=NEW.run_id;
 SELECT member_snapshot INTO snap FROM recommendation_runs WHERE run_id=rid;
 SELECT jsonb_agg(jsonb_build_object('user_id',user_id::text,'preference_version',preference_version) ORDER BY user_id) INTO actual FROM run_preferences WHERE run_id=rid;
 IF actual IS NULL OR actual<>snap THEN RAISE EXCEPTION 'RUN_PREFERENCE_SNAPSHOT_MISMATCH' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER run_members_complete AFTER INSERT ON recommendation_runs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_run_preferences();
CREATE CONSTRAINT TRIGGER run_members_match AFTER INSERT ON run_preferences DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_run_preferences();
