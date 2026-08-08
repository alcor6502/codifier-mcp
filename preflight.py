"""
preflight.py — blocking checks that do not warn: if one fails the process exits 2
and the service does NOT start. A check that crashes counts as FAILED, never as
passed.

The count is printed from len(RESULTS). No other file repeats it, and no
docstring states a number: four places once claimed how many checks there were
and three of them were wrong. Where the count needs mentioning, the true thing
to say is that they are all blocking.

Shared with the twin (archivist-mcp): oauth · token_store · funnel · node_key ·
cidrs · public_dns, and the placeholder and CIDR helpers.

Its own, because this service keeps a database instead of files:
  db          it opens, it is whole, it is in WAL
  schema      every table AND trigger is there — a missing trigger raises no
              error, it just stops writing history, and nobody notices
  ownership   the process is root and the database is NOT writable by anyone
              else: a write from the share would bypass the triggers
  admin_code  ADMIN_ACCESS_CODE present, long enough, not a placeholder
  approval    the approval key and the grace window, so the registry cannot
              come up in a state where nothing can ever be approved

Selective skip (for local testing only, never in production):
  PREFLIGHT_SKIP="funnel,node_key"
"""
from __future__ import annotations

import base64
import ipaddress
import os
import re
import secrets
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

SKIP = {s.strip() for s in os.environ.get("PREFLIGHT_SKIP", "").split(",") if s.strip()}
RESULTS: list[tuple[str, bool, str]] = []


# =====================================================================
# Helpers shared with the service, so the two can never disagree
# =====================================================================

_SEPARATORS = re.compile(r"[\s._\-]")
# Not preceded by a letter: the word has to START here. Without that guard,
# "exchange mechanism" squeezes to "exchangemechanism", which contains
# "changeme" — and so does a perfectly legitimate https://exchange.me.ts.net.
# A check that refuses to start the service on a real value is worse than the
# hole it closes.
_PLACEHOLDER = re.compile(r"(?<![A-Za-z])(CHANGEME|CAMBIAMI)", re.IGNORECASE)


def is_placeholder(v: str) -> bool:
    """True if the value is still a template placeholder.

    Separators are stripped before matching, so CHANGE_ME, CHANGE-ME, CHANGE.ME
    and 'change me' are all recognised. The first version of this matched the
    literal string only, which made the guard depend on whoever wrote the
    template spelling it exactly right — a guard that holds until the day it is
    needed.

    Only separators are stripped, never / or :, so the word boundary at the
    start of the placeholder survives: that is what tells CHANGEME inside
    https://CHANGEME.your-tailnet.ts.net (caught, and it teaches the syntax
    while being caught) from the one hiding inside exchange (let through)."""
    return bool(_PLACEHOLDER.search(_SEPARATORS.sub("", v)))


DEFAULT_CIDRS = "160.79.104.0/21 # documented egress of the model provider"


def parse_cidrs(raw: str) -> list[tuple[str, str]]:
    """Parse an ALLOWED_CIDRS list into [(cidr, description), ...].

    Entries are separated by ';' and '#' opens a description that runs to the
    end of the entry:

        160.79.104.0/21 # Anthropic egress ; 100.64.0.0/10 # tailnet

    The separator is not a comma precisely so a description may contain one. An
    empty string yields [], which means NO filter.

    A malformed entry RAISES; it is never skipped. A filter wider or narrower
    than you believe is worse than a service that refuses to start, because it
    is the failure nobody notices. Empty entries between separators are
    tolerated: a trailing ';' cannot change what the filter means."""
    out: list[tuple[str, str]] = []
    for chunk in raw.split(";"):
        entry = chunk.strip()
        if not entry:
            continue
        net_s, _, desc = entry.partition("#")
        net_s, desc = net_s.strip(), desc.strip()
        if not net_s:
            raise ValueError(f"entry with a description but no network: {entry!r}")
        try:
            net = ipaddress.ip_network(net_s, strict=True)
        except ValueError as e:
            raise ValueError(f"{net_s!r} is not a valid CIDR ({e})")
        out.append((str(net), desc))
    return out


def cidrs_from_env() -> list[tuple[str, str]]:
    """The IP filter as configured, resolved in ONE place.

    ALLOWED_CIDRS wins when it is DEFINED, even if empty — "defined and empty"
    means the filter is off, and is not the same thing as "not defined". The
    deprecated ANTHROPIC_CIDR is still honoured, so a container updated without
    touching its template keeps working exactly as before: a new variable is
    always born optional.

    server.py and preflight must never answer this question differently, which
    is why they both come here."""
    raw = os.environ.get("ALLOWED_CIDRS")
    if raw is None:
        raw = os.environ.get("ANTHROPIC_CIDR")      # deprecated, still supported
    if raw is None:
        raw = DEFAULT_CIDRS
    return parse_cidrs(raw)


def describe_cidrs(parsed: list[tuple[str, str]]) -> str:
    """What was UNDERSTOOD, not what was given. The way this breaks is mute: a
    comma in place of a semicolon and a range disappears without a word."""
    if not parsed:
        return "OFF (no IP filter)"
    n = len(parsed)
    body = ", ".join(f"{c} ({d})" if d else c for c, d in parsed)
    return f"{n} range{'s' if n != 1 else ''} — {body}"


def check(name):
    def deco(fn):
        def run():
            if name in SKIP:
                RESULTS.append((name, True, "SKIPPED (PREFLIGHT_SKIP)"))
                return
            try:
                msg = fn()
                RESULTS.append((name, True, msg or "ok"))
            except Exception as e:                  # a crash counts as a failure
                RESULTS.append((name, False, f"{type(e).__name__}: {e}"))
        return run
    return deco


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
    finally:
        r.close()
    tail = f" — REBUILT: {', '.join(repaired)}" if repaired else ""
    # Not a fault (the schema repaired itself) but it must not be silent:
    # somebody had removed those objects from the database.
    return f"{DB}: whole, WAL, {projects} projects, {rules} rules{tail}"


@check("schema")
def c_schema():
    """Post-condition on what the engine says it needs — TABLES and TRIGGERS are
    imported, never retyped here. A list copied into a second file is a list that
    drifts, and this one would drift silently."""
    from rules import TABLES, TRIGGERS
    cx = sqlite3.connect(DB, timeout=10)
    try:
        present = {r[0] for r in cx.execute("SELECT name FROM sqlite_master")}
        columns = {r[1] for r in cx.execute("PRAGMA table_info(projects)")}
    finally:
        cx.close()
    if "code" not in columns:
        raise RuntimeError("table `projects` has no `code` column: this database belongs to an "
                           "earlier schema. Recreate it, or migrate it, before starting.")
    missing = [x for x in TABLES + TRIGGERS if x not in present]
    if missing:
        raise RuntimeError(f"missing from the schema: {', '.join(missing)} — "
                           "the automatic repair did not work")
    return f"{len(TABLES)} tables + {len(TRIGGERS)} triggers, all present"


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
    """The registry must not come up in a state where nothing can ever be
    approved — a service that accepts proposals and can never let one through
    looks healthy and is not."""
    key = os.environ.get("APPROVAL_PUBKEY", "").strip()
    if key:
        if is_placeholder(key):
            raise RuntimeError("APPROVAL_PUBKEY is still a placeholder")
        try:
            raw = base64.b64decode(key, validate=True)
        except Exception:
            raise RuntimeError("APPROVAL_PUBKEY is not valid base64: it wants the raw ed25519 "
                               "public key, 32 bytes, not the OpenSSH one-line format")
        if len(raw) == 64:
            # 64 bytes is the seed+public pair. Saying so is worth a line: the
            # mistake is easy to make and the generic message would send you
            # looking at the wrong thing.
            raise RuntimeError("APPROVAL_PUBKEY is 64 bytes — that is a PRIVATE key. Only the "
                               "public half belongs here, and the private half never leaves "
                               "the machine that signs.")
        if len(raw) != 32:
            raise RuntimeError(f"APPROVAL_PUBKEY decodes to {len(raw)} bytes, 32 expected "
                               "(raw ed25519 public key)")

    grace = os.environ.get("APPROVAL_GRACE_UNTIL", "").strip()
    open_grace = False
    if grace:
        try:
            until = datetime.strptime(grace, "%Y-%m-%d").date()
        except ValueError:
            raise RuntimeError(f"APPROVAL_GRACE_UNTIL={grace!r}: it wants a DATE, YYYY-MM-DD. "
                               "It is a date and not a switch on purpose — it closes by itself, "
                               "and a lock you have to remember to switch on stays off.")
        open_grace = until >= datetime.now(timezone.utc).date()

    if not key and not open_grace:
        raise RuntimeError("no APPROVAL_PUBKEY and the grace window is closed or unset: nothing "
                           "could ever be approved. Set the key, or set APPROVAL_GRACE_UNTIL to "
                           "a future date.")

    days = os.environ.get("PROVISIONAL_DAYS", "").strip()
    if days:
        if not days.isdigit() or int(days) < 1:
            raise RuntimeError(f"PROVISIONAL_DAYS={days!r}: a positive whole number of days")

    parts = ["key present" if key else "no key"]
    parts.append(f"grace open until {grace}" if open_grace
                 else (f"grace closed on {grace}" if grace else "no grace window"))
    parts.append(f"provisional {days or 90} days")
    return " · ".join(parts)


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


CHECKS = [c_db, c_schema, c_writable, c_ownership, c_admin_code, c_approval,
          c_oauth, c_token_store, c_funnel, c_node_key, c_cidrs, c_dns]

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
