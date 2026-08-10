"""
preflight.py — blocking checks that do not warn: if one fails the process exits 2
and the service does NOT start. A check that crashes counts as FAILED, never as
passed.

The count is printed from len(RESULTS). No other file repeats it, and no
docstring states a number: four places once claimed how many checks there were
and three of them were wrong. Where the count needs mentioning, the true thing
to say is that they are all blocking.

Shared with the twin (archivist-mcp): oauth · token_store · funnel · node_key ·
cidrs · public_dns. The placeholder, CIDR and log-level helpers come from
mcp-common-engine — the twins had written them twice — and importing the
engine's ROOT drags no FastMCP in, by its own contract: a preflight has to be
able to run, and to report, on an image where fastmcp is missing or broken.

Its own, because this service keeps a database instead of files:
  db          it opens, it is whole, it is in WAL
  schema      every table AND trigger is there — a missing trigger raises no
              error, it just stops writing history, and nobody notices
  ownership   the process is root and the database is NOT writable by anyone
              else: a write from the share would bypass the triggers
  admin_code  ADMIN_ACCESS_CODE present, long enough, not a placeholder
  approval    the one knob the approval lifecycle reads, PROVISIONAL_DAYS,
              validated at the edge instead of at the first approval

Selective skip (for local testing only, never in production):
  PREFLIGHT_SKIP="funnel,node_key"
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import subprocess
import sys

from mcp_common_engine import (RESULTS, SKIP, check, cidrs_from_env,
                               describe_cidrs, is_placeholder)

DB = os.environ.get("DB_PATH", "/db/rules.db")
DBDIR = os.path.dirname(DB) or "/db"


# =====================================================================
# The checks
# =====================================================================

@check("db")
def c_db():
    from rules import Registry            # applies the schema if the file is new
    r = Registry(DB)
    try:
        integrity = r.cx.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"integrity_check: {integrity} — restore from the ZFS snapshot")
        journal = r.cx.execute("PRAGMA journal_mode").fetchone()[0]
        if journal.lower() != "wal":
            raise RuntimeError(f"journal_mode={journal}: WAL expected "
                               "(does the mount support locking?)")
        rules = r.cx.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
        projects = r.cx.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        repaired = r.repaired
        migrated = r.migrated
    finally:
        r.close()
    tail = f" — REBUILT: {', '.join(repaired)}" if repaired else ""
    # THIS open is the one that migrates: the preflight touches the database
    # before the server does, so by the time the server opens it there is
    # nothing left to declare — at v1.6.0 the "schema migrated at open" line
    # never appeared, because its only possible reader ran second. Whoever
    # performs a one-way change on a database in service is the one who says
    # so, and here that is this check.
    if migrated:
        tail += f" — migrated: {', '.join(migrated)}"
    # Not a fault (the schema repaired itself) but it must not be silent:
    # somebody had removed those objects from the database.
    return f"{DB}: whole, WAL, {projects} projects, {rules} rules{tail}"


@check("schema")
def c_schema():
    """Post-condition on what the engine says it needs — TABLES and TRIGGERS are
    imported, never retyped here. A list copied into a second file is a list that
    drifts, and this one would drift silently."""
    from rules import INDEXES, TABLES, TRIGGERS
    cx = sqlite3.connect(DB, timeout=10)
    try:
        present = {r[0] for r in cx.execute("SELECT name FROM sqlite_master")}
        columns = {r[1] for r in cx.execute("PRAGMA table_info(projects)")}
    finally:
        cx.close()
    if "code" not in columns:
        raise RuntimeError("table `projects` has no `code` column: this database belongs to an "
                           "earlier schema. Recreate it, or migrate it, before starting.")
    missing = [x for x in TABLES + INDEXES + TRIGGERS if x not in present]
    if missing:
        raise RuntimeError(f"missing from the schema: {', '.join(missing)} — "
                           "the automatic repair did not work")
    return (f"{len(TABLES)} tables + {len(INDEXES)} unique "
            f"{'index' if len(INDEXES) == 1 else 'indexes'} + "
            f"{len(TRIGGERS)} triggers, all present")


@check("writable")
def c_writable():
    p = os.path.join(DBDIR, f".preflight-{secrets.token_hex(4)}")
    open(p, "w").write("x")
    os.unlink(p)      # on some mounts deletion fails where writing succeeds
    return f"{DBDIR}: writes AND deletes"


@check("ownership")
def c_ownership():
    # The opposite of the vault, and deliberately so: here the files must NOT be
    # writable from outside. A change made from the share bypasses the triggers
    # and breaks history in silence.
    if os.geteuid() != 0:
        raise RuntimeError(f"the process runs as uid {os.geteuid()}, not root: the database "
                           "files would be born owned by somebody else")
    st = os.stat(DB)
    if st.st_uid != 0:
        raise RuntimeError(f"{DB} belongs to uid {st.st_uid}, not to root")
    if st.st_mode & 0o022:
        raise RuntimeError(f"{DB} is {oct(st.st_mode & 0o777)}: writable by group or others. "
                           "It must be 644 — from the share you read, and nothing else.")
    return f"root, {oct(st.st_mode & 0o777)} (read-only from the share)"


@check("admin_code")
def c_admin_code():
    v = os.environ.get("ADMIN_ACCESS_CODE", "")
    if not v or is_placeholder(v):
        raise RuntimeError("ADMIN_ACCESS_CODE missing or still a placeholder: without it, "
                           "writing would be open to any chat that connects")
    if len(v) < 12:
        raise RuntimeError(f"ADMIN_ACCESS_CODE is {len(v)} characters: too short (>=12)")
    return f"present ({len(v)} characters)"


@check("approval")
def c_approval():
    """What is left of this check after the signature's exit is the one knob
    the approval lifecycle still reads. Validated AT THE EDGE, at boot: a bad
    number found here is one line with a name, found at the first approval it
    is a traceback in a chat."""
    days = os.environ.get("PROVISIONAL_DAYS", "").strip()
    if days:
        if not days.isdigit() or int(days) < 1:
            raise RuntimeError(f"PROVISIONAL_DAYS={days!r}: a positive whole number of days")
    cap = os.environ.get("PENDING_CAP", "").strip()
    if cap:
        if not cap.isdigit() or int(cap) < 1:
            raise RuntimeError(f"PENDING_CAP={cap!r}: a positive whole number of "
                               "pending proposals")
    return (f"provisional {days or 90} days · pending cap {cap or 5} · "
            "admin code approves, no signature")


@check("oauth")
def c_oauth():
    # The most important check: without credentials this would be an authless
    # Funnel, indexed within minutes via certificate transparency logs.
    for k in ("GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "ALLOWED_GITHUB_LOGIN", "BASE_URL"):
        v = os.environ.get(k, "")
        if not v or is_placeholder(v):
            raise RuntimeError(f"{k} missing or still a placeholder")
    jwt = os.environ.get("JWT_SIGNING_KEY", "")
    if len(jwt) < 32:
        raise RuntimeError("JWT_SIGNING_KEY missing or too short (openssl rand -hex 32)")
    # Length alone was not enough: a 64-character placeholder walked straight
    # through, and the failure only surfaced at the first login.
    if is_placeholder(jwt):
        raise RuntimeError("JWT_SIGNING_KEY is still a placeholder (openssl rand -hex 32)")
    if not os.environ["BASE_URL"].startswith("https://"):
        raise RuntimeError("BASE_URL must be https")
    return "credentials present"


@check("token_store")
def c_token_store():
    # The OAuth store must live on a PERSISTENT volume: inside the container
    # filesystem every recreation would throw the tokens away, and the client
    # would ask for re-authorisation at every piece of maintenance.
    h = os.environ.get("FASTMCP_HOME", "")
    if not h.startswith("/data"):
        raise RuntimeError(f"FASTMCP_HOME={h!r}: it must live under /data (persistent volume)")
    os.makedirs(h, exist_ok=True)
    p = os.path.join(h, ".w")
    open(p, "w").write("x")
    os.unlink(p)
    return h


@check("funnel")
def c_funnel():
    r = subprocess.run(["tailscale", "funnel", "status"], capture_output=True, text=True, timeout=10)
    out = r.stdout + r.stderr
    if r.returncode != 0:
        raise RuntimeError(f"tailscale funnel status: {out.strip()[:200]}")
    if "Funnel on" not in out:
        raise RuntimeError(f"Funnel is NOT on: {out.strip()[:200]}")
    port = os.environ.get("PORT", "3001")
    if port not in out:
        raise RuntimeError(f"Funnel is on but not towards port {port}: {out.strip()[:200]}")
    return "Funnel on, correct port"


@check("node_key")
def c_node_key():
    # "expires in 179 days" is a scheduled silent outage.
    r = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("tailscale status does not answer")
    import json
    ke = (json.loads(r.stdout).get("Self") or {}).get("KeyExpiry")
    if ke:
        raise RuntimeError(f"the node key EXPIRES ({ke}): disable expiry in the Tailscale "
                           "admin console")
    return "key expiry disabled"


@check("cidrs")
def c_cidrs():
    # A malformed entry must BLOCK, not be skipped: the whole point of the filter
    # is knowing exactly what it lets through.
    return describe_cidrs(cidrs_from_env())


@check("public_dns")
def c_dns():
    # The BASE_URL hostname must resolve, or the client never arrives.
    import socket
    host = os.environ["BASE_URL"].split("//", 1)[1].split("/")[0]
    socket.getaddrinfo(host, 443)
    return f"{host} resolves"


MANUALS = ("reference-guide.md",)


@check("manuals")
def c_manuals():
    # The manual is a file, COPIED into the image, and it is the only thing
    # the server serves that the schema cannot vouch for. Left out of the
    # image nothing fails at boot: the tool is announced, the surface looks
    # whole, and the gap opens in a chat weeks later, which is the shape of
    # defect this project has already paid for once with a guide that pointed
    # at a file nobody had written. It is cheap to ask here. test_surface
    # holds this tuple against the files server.py actually serves, so it
    # cannot fall behind a manual added later.
    here = os.path.dirname(os.path.abspath(__file__))
    missing = [m for m in MANUALS if not os.path.isfile(os.path.join(here, m))]
    if missing:
        raise RuntimeError(f"not in the image: {', '.join(missing)} — check the Dockerfile COPY")
    return f"{len(MANUALS)} manual{'s' if len(MANUALS) != 1 else ''}, in the image"


CHECKS = [c_db, c_schema, c_writable, c_ownership, c_admin_code, c_approval,
          c_oauth, c_token_store, c_funnel, c_node_key, c_cidrs, c_dns,
          c_manuals]

if __name__ == "__main__":
    for fn in CHECKS:
        fn()
    width = max(len(n) for n, _, _ in RESULTS)
    failed = sum(0 if p else 1 for _, p, _ in RESULTS)
    for name, passed, msg in RESULTS:
        print(f"  {'OK ' if passed else 'FAIL'}  {name:<{width}}  {msg}")
    if failed:
        print(f"PREFLIGHT: {failed} checks failed — the service will NOT start.")
        sys.exit(2)
    print(f"PREFLIGHT: {len(RESULTS)}/{len(RESULTS)} — starting.")
