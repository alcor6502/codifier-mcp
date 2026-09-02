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

DBDIR="${DB_DIR:-/db}"
BACKUP="${BACKUP_DIR:-$DBDIR/backup}"
# The registry is the one file here that is NOT world-readable: it holds the
# reference and admin codes in clear, and that is the decision — the file is
# the safe, and root is the process. It has to be named to be spared, because
# the sweep below is deliberately blind.
REGISTRY="$DBDIR/projects.txt"

echo "== init (root): $DBDIR =="
mkdir -p "$DBDIR" "$BACKUP" /data

echo "== permissions: root:root, 755 on directories, 644 on files, 600 on the registry =="
# ONE pass over the volume. It was a chown -R and two finds — three walks of
# every backup ever taken, at every boot — for what one find does in one:
# the owner and the mode set as each entry is met, the registry skipped by
# the mode and given its own below. `-exec … +` batches the calls and always
# succeeds, so the -o chain reads as: a directory gets its two, a file gets
# its owner and then either IS the registry or gets 644.
find "$DBDIR" \( -type d -exec chown 0:0 {} + -exec chmod 755 {} + \) \
    -o \( -type f -exec chown 0:0 {} + \( -path "$REGISTRY" -o -exec chmod 644 {} + \) \)
chown -R 0:0 /data
# `if`, and not `[ -f … ] && chmod`: with `set -e` a false test is a failed
# command and the container would exit 1 on the ONE boot where the registry
# does not exist yet — the first one.
if [ -f "$REGISTRY" ]; then chmod 600 "$REGISTRY"; fi

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
