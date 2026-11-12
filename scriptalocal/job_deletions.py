"""Replayable physical collection after an owned job has been logically deleted."""
import os
from uuid import UUID

from .job_artifacts import ArtifactRecord,ArtifactError
from .ocr_access import ServiceError


def collect_deletions(queue,sources,*,limit=10,maximum_attempts=3,request_id=None):
    if type(limit)is not int or limit<1 or type(maximum_attempts)is not int or maximum_attempts<1:raise ValueError('Invalid deletion limits')
    counts={'done':0,'deferred':0,'failed':0}
    with queue.connect() as db:
        rows=db.execute("SELECT * FROM sl_deletions WHERE status='queued' AND (%s::uuid IS NULL OR id=%s) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT %s",(request_id,request_id,limit)).fetchall()
        for row in rows:
            try:
                records=[ArtifactRecord(**{**item,'id':UUID(item['id'])}) for item in row['artifacts']]
                if any(record.parts()[0]!=str(row['job_id']) for record in records):raise ArtifactError('Deletion manifest crosses job scope')
                with sources.locked(row['source_sha256']):
                    for record in records:
                        parts=record.parts()
                        try:
                            with queue.artifacts.directory(parts[:-1],False) as directory:
                                try:os.unlink(parts[-1],dir_fd=directory)
                                except FileNotFoundError:pass
                                os.fsync(directory)
                        except FileNotFoundError:pass
                    referenced=db.execute("SELECT EXISTS(SELECT 1 FROM sl_jobs WHERE source->>'sha256'=%s) AS present",(row['source_sha256'],)).fetchone()['present']
                    if not referenced:sources.remove_locked(row['source_sha256'])
                db.execute("UPDATE sl_deletions SET status='done',error_code=NULL,updated_at=clock_timestamp() WHERE id=%s",(row['id'],))
                counts['done']+=1
            except ServiceError as exc:
                if exc.code!='source_busy':raise
                counts['deferred']+=1
            except (OSError,ValueError,KeyError,TypeError):
                attempts=row['attempts']+1;state='failed' if attempts>=maximum_attempts else 'queued'
                db.execute('UPDATE sl_deletions SET status=%s,attempts=%s,error_code=%s,updated_at=clock_timestamp() WHERE id=%s',
                           (state,attempts,'deletion_storage_unavailable',row['id']))
                counts['failed' if state=='failed' else 'deferred']+=1
    return counts
