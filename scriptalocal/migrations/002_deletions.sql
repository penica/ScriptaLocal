CREATE TABLE sl_deletions (
 id uuid PRIMARY KEY,
 job_id uuid UNIQUE NOT NULL,
 owner text NOT NULL,
 source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[a-f0-9]{64}$'),
 artifacts jsonb NOT NULL CHECK (jsonb_typeof(artifacts)='array'),
 status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','done','failed')),
 attempts integer NOT NULL DEFAULT 0 CHECK (attempts>=0),
 error_code text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX sl_pending_deletions ON sl_deletions(created_at) WHERE status='queued';
