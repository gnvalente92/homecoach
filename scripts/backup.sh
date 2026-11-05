#!/bin/sh
# Publish and rotate backups only after both pg_dump and compression succeed.
set -u
backup_dir=${BACKUP_DIR:-/backups}
keep_days=${KEEP_DAYS:-14}
case "$keep_days" in ''|*[!0-9]*) echo 'KEEP_DAYS must be a non-negative integer' >&2; exit 1;; esac
mkdir -p "$backup_dir" || exit 1
dump="$backup_dir/.homecoach-$$.sql"
archive="$dump.gz"
trap 'rm -f "$dump" "$archive"' EXIT
trap 'exit 1' HUP INT TERM

backup_once() {
    destination="$backup_dir/homecoach-$(date +%F).sql.gz"
    if pg_dump -h db -U homecoach -f "$dump" homecoach &&
       gzip -c "$dump" > "$archive" &&
       mv "$archive" "$destination"; then
        rm -f "$dump"
        echo "backup $destination"
        find "$backup_dir" -name 'homecoach-*.sql.gz' -mtime +"$keep_days" -delete
    else
        echo 'backup failed; previous backups retained' >&2
        rm -f "$dump" "$archive"
        return 1
    fi
}

if [ "${BACKUP_ONCE:-0}" = 1 ]; then
    backup_once
    exit $?
fi
while true; do
    backup_once || true
    sleep 86400
done
