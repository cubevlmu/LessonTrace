PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS event_track_candidates(
 candidate_id TEXT PRIMARY KEY,event_id TEXT NOT NULL,run_id TEXT NOT NULL,track_id INTEGER NOT NULL,
 clip_id TEXT NOT NULL,video_id TEXT NOT NULL,start REAL NOT NULL,end REAL NOT NULL,
 generation_version TEXT NOT NULL,source_sha256 TEXT NOT NULL,event_signature TEXT NOT NULL,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS attribution_candidates_event ON event_track_candidates(event_id,run_id,track_id);
CREATE TABLE IF NOT EXISTS event_track_reviews(
 review_id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL REFERENCES event_track_candidates(candidate_id),
 event_id TEXT NOT NULL,run_id TEXT NOT NULL,track_id INTEGER NOT NULL,created_at TEXT NOT NULL,
 raw_model_decision TEXT,final_decision TEXT NOT NULL,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS attribution_reviews_track ON event_track_reviews(run_id,track_id,created_at);
CREATE TABLE IF NOT EXISTS event_track_bindings(
 binding_id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL REFERENCES event_track_candidates(candidate_id),
 event_id TEXT NOT NULL,run_id TEXT NOT NULL,track_id INTEGER NOT NULL,start REAL NOT NULL,end REAL NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('candidate','ambiguous','human_confirmed','rejected','unknown','revoked')),
 actor TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL,CHECK(end>=start));
CREATE INDEX IF NOT EXISTS attribution_binding_time ON event_track_bindings(run_id,track_id,start,end,status);
CREATE TABLE IF NOT EXISTS attribution_audit(
 audit_id TEXT PRIMARY KEY,binding_id TEXT NOT NULL REFERENCES event_track_bindings(binding_id),
 operation TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL);
