CREATE TABLE sl_jobs (
 id uuid PRIMARY KEY,
 owner text NOT NULL CHECK (length(owner) BETWEEN 1 AND 128),
 idempotency_key text,
 fingerprint text NOT NULL CHECK (fingerprint ~ '^[a-f0-9]{64}$'),
 configuration_sha256 text NOT NULL CHECK (configuration_sha256 ~ '^[a-f0-9]{64}$'),
 source jsonb NOT NULL CHECK (jsonb_typeof(source)='object'),
 options jsonb NOT NULL CHECK (jsonb_typeof(options)='object'),
 status text NOT NULL CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
 current_revision integer NOT NULL CHECK (current_revision>0),
 cancel_requested boolean NOT NULL DEFAULT false,
 error_code text,
 review_required boolean NOT NULL DEFAULT false,
 warnings jsonb NOT NULL DEFAULT '[]',
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(owner,idempotency_key),
 CHECK ((status='failed')=(error_code IS NOT NULL))
);
CREATE TABLE sl_revisions (
 job_id uuid NOT NULL REFERENCES sl_jobs(id) ON DELETE CASCADE,
 revision integer NOT NULL CHECK (revision>0),
 options jsonb NOT NULL CHECK (jsonb_typeof(options)='object'),
 state text NOT NULL CHECK (state IN ('pending','succeeded','failed','cancelled')),
 result_artifact_id uuid,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(job_id,revision),
 CHECK ((state='succeeded')=(result_artifact_id IS NOT NULL))
);
ALTER TABLE sl_jobs ADD CONSTRAINT sl_current_revision_fk FOREIGN KEY(id,current_revision)
 REFERENCES sl_revisions(job_id,revision) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE sl_stages (
 id uuid PRIMARY KEY,
 job_id uuid NOT NULL,
 revision integer NOT NULL,
 name text NOT NULL CHECK (name IN ('layout','segmentation','recognition')),
 status text NOT NULL CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
 attempts integer NOT NULL DEFAULT 0,
 max_attempts integer NOT NULL CHECK (max_attempts>0),
 worker_id text,
 lease_token uuid,
 lease_expires_at timestamptz,
 completion_token uuid,
 completion_fingerprint text,
 output_artifact_id uuid,
 last_error_code text,
 available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(job_id,revision,name),
 FOREIGN KEY(job_id,revision) REFERENCES sl_revisions(job_id,revision) ON DELETE CASCADE,
 CHECK (attempts>=0 AND attempts<=max_attempts),
 CHECK (status<>'queued' OR attempts<max_attempts),
 CHECK ((status='running' AND worker_id IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
     OR (status<>'running' AND worker_id IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL)),
 CHECK (status<>'succeeded' OR (completion_token IS NOT NULL AND completion_fingerprint IS NOT NULL AND output_artifact_id IS NOT NULL))
);
CREATE UNIQUE INDEX sl_single_running_stage ON sl_stages(job_id,revision) WHERE status='running';
CREATE INDEX sl_stage_claim ON sl_stages(status,available_at,name);
CREATE INDEX sl_stage_expiry ON sl_stages(lease_expires_at) WHERE status='running';
CREATE TABLE sl_artifacts (
 id uuid PRIMARY KEY,
 job_id uuid NOT NULL,
 revision integer NOT NULL,
 stage_id uuid NOT NULL REFERENCES sl_stages(id) ON DELETE CASCADE,
 lease_token uuid NOT NULL,
 logical_key text NOT NULL CHECK (length(logical_key) BETWEEN 1 AND 128),
 relative_path text NOT NULL UNIQUE,
 sha256 text NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
 bytes bigint NOT NULL CHECK (bytes>=0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(stage_id,logical_key),
 FOREIGN KEY(job_id,revision) REFERENCES sl_revisions(job_id,revision) ON DELETE CASCADE
);
ALTER TABLE sl_stages ADD CONSTRAINT sl_output_artifact_fk FOREIGN KEY(output_artifact_id)
 REFERENCES sl_artifacts(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE sl_revisions ADD CONSTRAINT sl_result_artifact_fk FOREIGN KEY(result_artifact_id)
 REFERENCES sl_artifacts(id) DEFERRABLE INITIALLY DEFERRED;
