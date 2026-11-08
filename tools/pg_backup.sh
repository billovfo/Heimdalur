#!/usr/bin/env bash
# Canonical dev-DB backup (P0.1). One script, pg_dump -Fc, sha256, prune.
# Usage: tools/pg_backup.sh [--dry-run]
# Cron: 17 3 * * 0  cd <repo> && ./tools/pg_backup.sh >> ~/.local/share/heimdal/pg/backup.log 2>&1
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BKP="$ROOT/backups"
# Cluster lives outside the repo (moved from tmp-pg/ 2026-11-05: /tmp-* is git-ignored scratch).
PGBIN="${HEIMDAL_PGBIN:-$HOME/.local/share/heimdal/pg/server/postgresql-18.4.0-x86_64-unknown-linux-gnu/bin}"
HOST=127.0.0.1; PORT=5432; USER=postgres
# Authentication: PGPASSFILE (see tools/dev_env.sh) or PGPASSWORD from env.
# No password defaults live in this script.
: "${PGPASSFILE:=$ROOT/tools/.dev-pgpass}"
export PGPASSFILE
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
TS=$(date +%Y%m%dT%H%M%S)
mkdir -p "$BKP"
# Dev DBs (W2: recovered 2026-09-04) plus the untouched pre-recovery fallbacks.
BACKUP_DBS="${BACKUP_DBS:-heimdal_recovered_20260904 pdfhist_pilot_recovered_20260904 heimdal pdfhist_pilot}"
for DB in $BACKUP_DBS; do
  F="$BKP/${DB}_${TS}.dump"
  if [ $DRY -eq 1 ]; then echo "would dump $DB -> $F"; continue; fi
  # Dump to .part and verify the archive is readable before it becomes a
  # real backup: a failed dump must never count toward retention below.
  if "$PGBIN/pg_dump" -h $HOST -p $PORT -U $USER -Fc -f "$F.part" "$DB" \
     && "$PGBIN/pg_restore" -l "$F.part" > /dev/null; then
    mv "$F.part" "$F" && sha256sum "$F" > "$F.sha256" && echo "backed up $DB -> $F"
  else
    rm -f "$F.part"; echo "BACKUP FAILED for $DB - nothing pruned" >&2; exit 1
  fi
done
[ $DRY -eq 1 ] && exit 0
# Retention: keep newest 4 per DB, prune older (dump + checksum together).
for DB in $BACKUP_DBS; do
  # Only timestamped dumps rotate; hand-named baselines (*_adopted.dump) are kept.
  ls -t "$BKP/${DB}_"[0-9]*T[0-9]*.dump 2>/dev/null | tail -n +5 | while read -r OLD; do
    rm -f "$OLD" "$OLD.sha256" && echo "pruned $OLD"
  done
done
# Observable age manifest (STATUS.md reads this).
python3 - "$BKP" <<'EOF'
import json, sys, glob, os
bkp = sys.argv[1]
latest = {}
for f in glob.glob(bkp + "/*_[0-9]*T[0-9]*.dump"):
    db = os.path.basename(f).rsplit("_", 1)[0]
    if db not in latest or f > latest[db]:
        latest[db] = f
out = {"backups": {db: {"file": os.path.basename(f), "sha256_file": os.path.basename(f) + ".sha256"} for db, f in latest.items()}}
open(bkp + "/LATEST.json", "w").write(json.dumps(out, indent=1) + "\n")
print("LATEST.json updated")
EOF
