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

Its own, because this service keeps databases instead of files — and since
v4.0.0 there are SEVERAL of them: the registry file says which, and every one
of these checks walks the whole list rather than the one file there used to be.
A registry that names four projects and a preflight that looks at one is a
preflight that passes while three databases are broken.
  db          the registry parses, and every project it serves opens, is whole
              and is in WAL — and a file from an earlier schema goes RED here,
              with the cure in the line: there is no migration, the corpus goes
              back in by hand
  schema      every table AND trigger is there, in every served file — a
              missing trigger raises no error, it just stops writing history,
              and nobody notices
  ownership   the process is root, no database is writable by anyone else — a
              write from the share would bypass the triggers — and the registry
              file, which holds the codes in clear, is readable by root alone
  approval    the two knobs the lifecycle reads, PROVISIONAL_DAYS and
              ADMIN_AUTH_CODE_DURATION, validated at the edge instead of at the
              first approval and the first minting
  web         the administration UI: its password present, long enough and not
              a placeholder, and its port neither publishable by the Funnel nor
              the MCP's own

ADMIN_ACCESS_CODE has no check any more because it has no reader any more:
the maintenance credential is the per-project architect key, a hash on the
project's own row, born at create and reborn at rekey — there is nothing of
it in the environment to validate.

Selective skip (for local testing only, never in production):
  PREFLIGHT_SKIP="funnel,node_key"
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import subprocess
import sys

import web
from mcp_common_engine import (RESULTS, SKIP, check, cidrs_from_env,
                               describe_cidrs, is_placeholder)
from rules import DB_ROOT, DEFAULT_AUTH_CODE_MINUTES

# `import web` and not `from web import ...`: this file must keep running on an
# image where the web stack is broken, and web.py earns that by importing
# starlette inside build() rather than at its top. What is taken from it here
# is the resolution of the port and the list of publishable ones — the same
# expression the service uses, so the two cannot disagree about whether the
# page is reachable from the internet.

# The FOLDER the container sees. Everything below it — the registry file and
# one folder per project — is the router's business, and from v4.0.0 this
# preflight has to walk EVERY file of the registry instead of the one database
# there used to be. That rewrite is not in this commit; what is, is the
# variable, so that opening the registry cannot land on a path that was a file
# name until yesterday.
DBDIR = os.environ.get("DB_DIR") or DB_ROOT


def _served() -> list[dict]:
    """Every project the registry serves, opened. ONE reading, used by three
    checks, and it is the reading the service itself will do a second later:
    the parse refusal, the generation refusal and the create-if-missing line
    all happen HERE, before anything binds a port.

    The registry is opened and closed around each check rather than held: a
    preflight that kept a connection open would be holding the WAL of a
    database the service is about to open for itself."""
    from rules import Registry            # applies the schema if a file is new
    r = Registry(DBDIR)
    try:
        return list(r.projects()["projects"]), r.repaired(), r.born_empty(), r.file
    finally:
        r.close()


# =====================================================================
# The checks
# =====================================================================

@check("db")
def c_db():
    """The registry parses and every file it names opens, whole and in WAL.

    Since v4.0.0 this is a LIST and not a file, and the difference matters at
    exactly one moment: the schema generation. A file from an earlier
    generation is refused by the engine — not migrated, by decision — and the
    refusal has to arrive HERE, where it is one red line with the path in it,
    rather than in a chat three hours later."""
    served, repaired, born_empty, registry_file = _served()
    if not served:
        # Not a failure: a fresh installation has an empty registry, and the
        # engine has just written the template into it. Silence would be the
        # wrong answer, though — a service that serves nothing looks exactly
        # like a service that is fine.
        return (f"{registry_file}: parses, and serves NO project yet — add a "
                f"line `name | reference code | admin code`")
    notes = []
    for p in served:
        cx = sqlite3.connect(p["path"], timeout=10)
        try:
            integrity = cx.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"{p['path']}: integrity_check says {integrity} — "
                                   "restore from the ZFS snapshot")
            journal = cx.execute("PRAGMA journal_mode").fetchone()[0]
            if journal.lower() != "wal":
                raise RuntimeError(f"{p['path']}: journal_mode={journal}, WAL expected "
                                   "(does the mount support locking?)")
            rules = cx.execute("SELECT COUNT(*) FROM rule").fetchone()[0]
        finally:
            cx.close()
        notes.append(f"{p['name']} ({p['slug']}.db, schema {p['schema']}, "
                     f"{rules} rules)")
    tail = ""
    # Not a fault — the schema repaired itself — but it must not be silent:
    # somebody had removed those objects from that database.
    for name, objects in sorted(repaired.items()):
        tail += f" — REBUILT in {name}: {', '.join(objects)}"
    # The signature of ONE specific accident: a folder renamed without its
    # registry line, or the other way round. The project answers every call
    # with an empty corpus and nothing else says so.
    if born_empty:
        tail += (f" — BORN EMPTY this boot: {', '.join(born_empty)}. If any of "
                 f"those already existed, its folder was not renamed along with "
                 f"its registry line")
    return f"{len(served)} served: {'; '.join(notes)}{tail}"


@check("schema")
def c_schema():
    """Post-condition on what the engine says it needs, in EVERY served file —
    TABLES, INDEXES and TRIGGERS are imported, never retyped here. A list
    copied into a second file is a list that drifts, and this one would drift
    silently.

    A raw connection on purpose: opening through the engine would re-apply the
    schema and this check would be observing its own repair."""
    from rules import INDEXES, TABLES, TRIGGERS
    served, _, _, registry_file = _served()
    if not served:
        return f"no project served by {registry_file}: nothing to check"
    for p in served:
        cx = sqlite3.connect(p["path"], timeout=10)
        try:
            present = {r[0] for r in cx.execute("SELECT name FROM sqlite_master")}
        finally:
            cx.close()
        missing = [x for x in TABLES + INDEXES + TRIGGERS if x not in present]
        if missing:
            raise RuntimeError(f"{p['path']} is missing {', '.join(missing)} — "
                               "the automatic repair did not work")
    return (f"{len(TABLES)} tables + {len(INDEXES)} unique "
            f"{'index' if len(INDEXES) == 1 else 'indexes'} + "
            f"{len(TRIGGERS)} triggers, in each of {len(served)}")


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
    served, _, _, registry_file = _served()
    for p in served:
        st = os.stat(p["path"])
        if st.st_uid != 0:
            raise RuntimeError(f"{p['path']} belongs to uid {st.st_uid}, not to root")
        if st.st_mode & 0o022:
            raise RuntimeError(
                f"{p['path']} is {oct(st.st_mode & 0o777)}: writable by group or "
                "others. It must be 644 — from the share you read, and nothing else.")
    # AND THE REGISTRY, which is the file the databases do not protect: it
    # holds every reference code and every admin code IN CLEAR, so it is the
    # one file here that must not be readable from the share at all. The engine
    # re-applies 0600 at every reload and `entrypoint.sh` leaves it out of the
    # 644 sweep; this is where a mount that ignored both says so.
    st = os.stat(registry_file)
    if st.st_mode & 0o077:
        raise RuntimeError(
            f"{registry_file} is {oct(st.st_mode & 0o777)}: it carries every code "
            "of every project in clear and must be 600 — readable by root alone")
    return (f"root · {len(served)} database"
            f"{'' if len(served) == 1 else 's'} 644 (read-only from the share) · "
            f"the registry 600")


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
    # PENDING_CAP is gone: the proposal ceiling is `queue_cap`, policy of each
    # project, because the container is multi-tenant. What is validated here in
    # its place is the life of a one-time auth code — the same reason, at the
    # same edge: a bad number caught at boot is a line with a name on it, and
    # caught at the first minting it is a traceback in a browser.
    mins = os.environ.get("ADMIN_AUTH_CODE_DURATION", "").strip()
    if mins:
        if not mins.isdigit() or int(mins) < 1:
            raise RuntimeError(f"ADMIN_AUTH_CODE_DURATION={mins!r}: a positive whole "
                               "number of minutes")
    return (f"provisional {days or 90} days · auth codes live "
            f"{mins or DEFAULT_AUTH_CODE_MINUTES} minutes · the UI approves, behind "
            "its password")


@check("web")
def c_web():
    """The administration UI, refused AT THE EDGE. Two mistakes live here and
    neither one announces itself.

    A master still on its placeholder is an open approval page: the UI is what
    promulgates rules, and it is the one door in this system a person comes
    through. The failure is not a traceback, it is a rule in force that nobody
    decided.

    A UI on 443, 8443 or 10000 is a page on the internet. Those are the only
    three ports Tailscale Funnel can publish, and the Funnel runs in this
    container: on one of them the page stops being on the LAN, silently, and
    nothing anywhere would say so. The port stayed a VARIABLE on purpose — a
    constant would close the door on a second product on the same machine —
    so the guarantee moved here, where it is a refusal with a name on it
    instead of a property of the source.

    And a UI on the MCP's own port is a service that comes up half-started:
    whichever of the two binds second dies, and the log line above it has
    already said everything is fine."""
    v = os.environ.get("WEB_UI_PASSWORD", "")
    if not v or is_placeholder(v):
        raise RuntimeError("WEB_UI_PASSWORD missing or still a placeholder: the "
                           "administration UI is what approves rules, and without a "
                           "password its pages would be open to whoever reaches the port")
    if len(v) < 12:
        raise RuntimeError(f"WEB_UI_PASSWORD is {len(v)} characters: too short (>=12)")
    try:
        port = web.port_from_env()
    except ValueError as e:
        raise RuntimeError(str(e)) from e
    if port in web.FUNNEL_PORTS:
        raise RuntimeError(
            f"WEB_PORT={port}: the Funnel can publish "
            f"{', '.join(str(x) for x in web.FUNNEL_PORTS)}, and it runs in this container — "
            "the administration UI would be on the internet. Choose any other port.")
    mcp_port = (os.environ.get("PORT") or "3001").strip()
    if not mcp_port.isdigit():
        raise RuntimeError(f"PORT={mcp_port!r}: a whole port number")
    if port == int(mcp_port):
        raise RuntimeError(f"WEB_PORT={port} is the MCP's own port: two servers cannot bind "
                           "the same one, and the one that loses dies after the startup "
                           "line has already said everything is fine")
    # There is NO ceiling to validate here any more, and its absence is the
    # point rather than an omission: WEB_ACTION_CAP left the template with
    # PENDING_CAP when the two became one number, `queue_cap`, which is policy
    # of each PROJECT and lives in that project's database. A number in the
    # environment would be a container-wide opinion about a multi-tenant
    # container, and there is nowhere left to put it.
    return (f"password present ({len(v)} characters) · UI on {port}, "
            f"which the Funnel cannot publish")


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


MANUALS = ("reference-guide.md", "reference-guide-admin.md")


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
    # AND IT HAS THE RIGHT SHAPE. From 4.1.0 the manual is served one card at a
    # time, so a file that is present but has lost its `# COMMANDS` separator is
    # a manual that answers every single call with a fault — present, announced,
    # and useless, which is the same defect as absent wearing a better face. The
    # cut is asked for with the engine's own function, so there is no second
    # expression here to disagree with it later.
    from rules import split_guide                    # stdlib-only module
    counts = []
    for m in MANUALS:
        with open(os.path.join(here, m), encoding="utf-8") as f:
            text = f.read()
        try:
            _, cards = split_guide(text)
        except Exception as e:
            raise RuntimeError(f"{m}: {e} — the file in the image is not the "
                               f"manual this version serves") from e
        if not cards:
            raise RuntimeError(f"{m}: no command cards past the separator — "
                               f"reference_guide(name) could answer nothing")
        counts.append(f"{m.removesuffix('.md')} {len(cards)}")
    return f"{len(MANUALS)} manuals, in the image · cards: {', '.join(counts)}"


CHECKS = [c_db, c_schema, c_writable, c_ownership, c_approval,
          c_web, c_oauth, c_token_store, c_funnel, c_node_key, c_cidrs, c_dns,
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
