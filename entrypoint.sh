#!/bin/sh
set -e
cd /app

# NO privilege drop, unlike the twin: here the process stays ROOT on purpose.
#
# The vault wants its files writable from the share, because a person editing a
# note by hand is a legitimate use. This service wants the opposite: the
# database and its copies are born 0644 and the directories 0755, so whoever
# mounts the share over SMB READS and does not touch. A write made by hand on
# the .db would bypass the triggers and break history in silence — and silence
# is the part that matters, because nothing would ever say so.
#
# preflight checks this again from the inside, and refuses to start if the file
# has become writable by group or others.

DB="${DB_PATH:-/db/rules.db}"
DBDIR=$(dirname "$DB")
BACKUP="${BACKUP_DIR:-$DBDIR/backup}"

echo "== init (root): $DBDIR =="
mkdir -p "$DBDIR" "$BACKUP" /data

echo "== permissions: root:root, 755 on directories, 644 on files =="
chown -R 0:0 "$DBDIR" /data
find "$DBDIR" -type d -exec chmod 755 {} +
find "$DBDIR" -type f -exec chmod 644 {} +

export HOME=/data/home
mkdir -p "$HOME"
umask 022

# Blocking, and it is the point of the whole file: a failed check exits 2 and
# the server is never reached. A service that starts anyway and warns is a
# service nobody reads the warnings of.
echo "== preflight =="
python3 preflight.py || exit $?

echo "== server (uid $(id -u)) =="
exec python3 server.py
