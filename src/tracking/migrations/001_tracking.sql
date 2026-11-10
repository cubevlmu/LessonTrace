PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS tracking_schema_versions(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO tracking_schema_versions VALUES(1);
CREATE TABLE IF NOT EXISTS tracking_runs(
 run_id TEXT PRIMARY KEY,video_id TEXT NOT NULL,clip_id TEXT NOT NULL,
 fingerprint TEXT NOT NULL,parent_run_id TEXT REFERENCES tracking_runs(run_id),
 status TEXT NOT NULL CHECK(status IN ('running','completed','failed','interrupted')),
 started_at TEXT NOT NULL,finished_at TEXT,metadata TEXT NOT NULL,report TEXT,error_message TEXT
);
CREATE INDEX IF NOT EXISTS tracking_run_video ON tracking_runs(video_id,clip_id,started_at);
CREATE INDEX IF NOT EXISTS tracking_run_fingerprint ON tracking_runs(fingerprint,status);
CREATE UNIQUE INDEX IF NOT EXISTS tracking_one_active ON tracking_runs(fingerprint) WHERE status='running';
CREATE TABLE IF NOT EXISTS person_tracks(
 run_id TEXT NOT NULL REFERENCES tracking_runs(run_id),track_id INTEGER NOT NULL CHECK(track_id>0),
 video_id TEXT NOT NULL,local_start_time REAL NOT NULL,local_end_time REAL NOT NULL,
 global_start_time REAL NOT NULL,global_end_time REAL NOT NULL,first_frame INTEGER NOT NULL,last_frame INTEGER NOT NULL,
 observed_frame_count INTEGER NOT NULL,quality_flags TEXT NOT NULL DEFAULT '[]',
 PRIMARY KEY(run_id,track_id)
);
CREATE INDEX IF NOT EXISTS tracking_track_time ON person_tracks(video_id,global_start_time,global_end_time);
CREATE TABLE IF NOT EXISTS tracking_frames(
 run_id TEXT NOT NULL REFERENCES tracking_runs(run_id),frame_index INTEGER NOT NULL,processed_index INTEGER NOT NULL,
 local_timestamp REAL NOT NULL,global_timestamp REAL NOT NULL,pts INTEGER NOT NULL,time_base_num INTEGER NOT NULL,time_base_den INTEGER NOT NULL,
 detection_count INTEGER NOT NULL,tracked_count INTEGER NOT NULL,
 PRIMARY KEY(run_id,frame_index),UNIQUE(run_id,processed_index)
);
CREATE INDEX IF NOT EXISTS tracking_frame_time ON tracking_frames(run_id,global_timestamp,frame_index);
CREATE TABLE IF NOT EXISTS track_observations(
 run_id TEXT NOT NULL,track_id INTEGER NOT NULL,frame_index INTEGER NOT NULL,
 local_timestamp REAL NOT NULL,global_timestamp REAL NOT NULL,
 bbox_x1 REAL NOT NULL,bbox_y1 REAL NOT NULL,bbox_x2 REAL NOT NULL,bbox_y2 REAL NOT NULL,
 confidence REAL NOT NULL CHECK(confidence>=0 AND confidence<=1),frame_width INTEGER NOT NULL,frame_height INTEGER NOT NULL,
 tracker_state TEXT NOT NULL CHECK(tracker_state='matched_visible_detection'),
 PRIMARY KEY(run_id,track_id,frame_index),
 FOREIGN KEY(run_id,track_id) REFERENCES person_tracks(run_id,track_id),
 FOREIGN KEY(run_id,frame_index) REFERENCES tracking_frames(run_id,frame_index)
);
CREATE INDEX IF NOT EXISTS tracking_observation_time ON track_observations(run_id,global_timestamp,track_id);
CREATE TABLE IF NOT EXISTS person_detections(
 run_id TEXT NOT NULL,frame_index INTEGER NOT NULL,detection_index INTEGER NOT NULL,track_id INTEGER,
 payload TEXT NOT NULL,PRIMARY KEY(run_id,frame_index,detection_index),
 FOREIGN KEY(run_id,frame_index) REFERENCES tracking_frames(run_id,frame_index),
 FOREIGN KEY(run_id,track_id) REFERENCES person_tracks(run_id,track_id)
);
CREATE TABLE IF NOT EXISTS track_visibility_segments(
 run_id TEXT NOT NULL,track_id INTEGER NOT NULL,segment_index INTEGER NOT NULL,
 first_frame INTEGER NOT NULL,last_frame INTEGER NOT NULL,local_start REAL NOT NULL,local_end REAL NOT NULL,
 global_start REAL NOT NULL,global_end REAL NOT NULL,
 PRIMARY KEY(run_id,track_id,segment_index),FOREIGN KEY(run_id,track_id) REFERENCES person_tracks(run_id,track_id)
);
CREATE TABLE IF NOT EXISTS track_gaps(
 run_id TEXT NOT NULL,track_id INTEGER NOT NULL,gap_index INTEGER NOT NULL,
 last_visible_frame INTEGER NOT NULL,first_missing_frame INTEGER NOT NULL,first_missing_global REAL NOT NULL,
 next_visible_frame INTEGER,next_visible_global REAL,reason TEXT NOT NULL,
 PRIMARY KEY(run_id,track_id,gap_index),FOREIGN KEY(run_id,track_id) REFERENCES person_tracks(run_id,track_id)
);
