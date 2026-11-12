# Initial local backup and restoration validation

On 12 November 2026, the three successful HTTP/GPU jobs were preserved before API integration tests truncated the dedicated queue database. Both inference/API validation containers were stopped, so their referenced image and artifact files were immutable during this exercise. The custom-format `pg_dump` is private at `data/validation/pre-artifact-queue-snapshot/queue.dump` with its checksum and original roots in `manifest.json`.

The dump was restored into a new database, `scriptalocal_artifact_restore_validation`, in the dedicated validation PostgreSQL container. No other application's database was changed. The restored database passed the installed migration/checksum readiness check and contained three successful jobs, each at revision 1.

The operator commands were:

```sh
docker exec scriptalocal-jobs-validation-db-1 pg_dump \
  -U scriptalocal_test -d scriptalocal_queue_validation -Fc
docker exec scriptalocal-jobs-validation-db-1 createdb \
  -U scriptalocal_test scriptalocal_artifact_restore_validation
docker exec -i scriptalocal-jobs-validation-db-1 pg_restore --exit-on-error \
  -U scriptalocal_test -d scriptalocal_artifact_restore_validation
```

The first command's bytes were saved to the private dump file; that file was passed on stdin to the third command. Commands shown here describe the completed exercise; database creation is intentionally not an idempotent rerun command. Database credentials stayed in private mounted files. The restore-only connection string changed only the database name and was never printed.

Every referenced artifact was checked through `ArtifactStore` before copying. The dump, a manifest, 102 artifact files and one shared original image were copied to `/srv/storage/scriptalocal-backups/resident-http-001`, within the user-selected local destination. Files were fsynced, restricted to mode 0600, and checksummed after writing. Backup directories use mode 0700. The backup dump is byte-identical to the successfully restored dump. All 103 data files were then restored from the backup copies into separate paths under `data/validation/artifact-restore/`, with matching checksums. Total artifact/original bytes: 2,373,555.

A temporary loopback API used only the restored database and restored filesystem paths, with a read-only caller identity. It enumerated all three jobs' artifacts in five pages per job and downloaded 102 derived artifacts plus three job-scoped original-image responses. Every response matched its declared size and SHA-256. Reusing one job's original artifact ID under another job returned 404; the read-only caller's cancel/delete attempts returned 403. The temporary API is stopped; private restored data and the local backup remain retained.

Evidence: `docs/validation/backup-restore-first.json` and `docs/validation/artifact-access.json`. This is an initial manual validation, not an implemented scheduled backup facility. It covers raw machine outputs and revision 1 only. HTR-025 remains open for correction history, full backup automation/configuration, independent-machine/Synology NFS backup and deployment/migration rollback. The local destination is not protection against loss of this host. A broader retention policy must account for these private backup and restore copies.
