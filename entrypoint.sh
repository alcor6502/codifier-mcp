#!/bin/sh
set -e
cd /app
# NIENTE drop di privilegi, al contrario del vault-mcp: qui si resta ROOT di
# proposito. Il database e le sue copie nascono 644 e le directory 755, cosi'
# chi monta la share via SMB LEGGE e non tocca: una scrittura a mano sul .db
# aggirerebbe i trigger e romperebbe lo storico in silenzio.
DB="${DB_PATH:-/db/regole.db}"
DBDIR=$(dirname "$DB")
BK="${BACKUP_DIR:-$DBDIR/backup}"
echo "== init (root): $DBDIR =="
mkdir -p "$DBDIR" "$BK" /data
echo "== permessi: root:root, 755 sulle directory, 644 sui file =="
chown -R 0:0 /db /data
find /db -type d -exec chmod 755 {} +
find /db -type f -exec chmod 644 {} +
export HOME=/data/home; mkdir -p "$HOME"
umask 022
echo "== preflight =="
python3 preflight.py || exit $?
echo "== server (uid $(id -u)) =="
exec python3 server.py
