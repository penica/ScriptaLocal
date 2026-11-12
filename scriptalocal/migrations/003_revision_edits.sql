CREATE TABLE sl_revision_edits (
 job_id uuid NOT NULL,
 revision integer NOT NULL,
 base_revision integer NOT NULL,
 actor text NOT NULL CHECK (length(actor) BETWEEN 1 AND 128),
 submitted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 request jsonb NOT NULL CHECK (jsonb_typeof(request)='object'),
 PRIMARY KEY(job_id,revision),
 FOREIGN KEY(job_id,revision) REFERENCES sl_revisions(job_id,revision) ON DELETE CASCADE,
 FOREIGN KEY(job_id,base_revision) REFERENCES sl_revisions(job_id,revision) ON DELETE CASCADE,
 CHECK (revision>base_revision)
);
