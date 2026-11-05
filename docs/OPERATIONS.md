# Running and recovering Homecoach

The operator is the owner of the Docker host. The application uses Postgres and the shared `/data` directory. Both web
and collector need the same writable data mount: it contains the FIT archive and the lock that prevents overlapping syncs.

## Check the service

`GET /api/health` checks database connectivity and returns 503 if the database cannot be reached. It is available without
HTTP basic authentication so container health checks can use it. Set both `HC_AUTH_USER` and `HC_AUTH_PASSWORD` to enable
authentication; setting only one prevents startup. Use HTTPS when credentials cross an untrusted network.

The collector heartbeat checks that the process is alive, not whether COROS is up to date. The header displays the last
sync error; `/api/config` includes `sync.collector-status` with attempt/success timestamps and an error class. It never
returns connection tokens. `provider_synced=false` means the job completed without syncing a provider, for example before
COROS is connected. Inspect web and collector logs when a sync fails. A later scheduled run retries automatically.

The shared file lock permits one sync at a time across containers. A duplicate request is skipped; a full resync reports
that it could not run rather than claiming completion. After a failure, imported activity writes roll back while any
rotated COROS token is retained, unless the account was disconnected or changed meanwhile.

## Back up and verify

The backup service runs `scripts/backup.sh` daily. It writes a temporary SQL dump, compresses it, then atomically publishes
`homecoach-YYYY-MM-DD.sql.gz`. Failed dumps or compression do not publish a backup or delete older files. Only a successful
backup triggers retention according to `HC_BACKUP_KEEP_DAYS` (default 14). Check backup container logs and file dates;
container uptime alone does not prove that backups succeeded.

Keep a separate backup of the `/data` dataset, including FIT files. The database dump does not include that directory.
Protect backups as personal data: the database also contains COROS connection tokens and check-ins.

To validate an archive, first run `gzip -t /path/to/homecoach-YYYY-MM-DD.sql.gz`. This checks compression only. Periodically
restore into an isolated Postgres 16 database and verify activity counts, dates, races and check-ins before relying on it.

## Restore

Stop web and collector first. Restore into an empty database, using a backup compatible with the application version.
Keep the current database and data directory available until the restored service has been checked.

1. Find the actual database container name with `sudo docker ps`. TrueNAS may prefix it with `ix-`.
2. Decompress the backup into a temporary file; continue only if decompression succeeds:
   `gunzip -c /path/to/homecoach-YYYY-MM-DD.sql.gz > /tmp/homecoach-restore.sql`.
3. Restore into the empty database: `sudo docker exec -i <db-container> psql -v ON_ERROR_STOP=1 -U homecoach homecoach < /tmp/homecoach-restore.sql`.
4. Restore the matching `/data` backup and its ownership (UID 10001). Start web to apply migrations, then collector.
5. Check `/api/health`, recent activities and the next sync. Remove the temporary SQL file after checking the restore.

Record the image tag with each backup. For an application rollback, stop the app and select its previous `HC_VERSION`
image tag. If the new version changed the schema incompatibly, restore its pre-upgrade database and matching data backup
before starting the previous image. Do not assume that changing the image tag reverses database migrations.
