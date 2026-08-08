"""
rules.py — a rules registry on SQLite. ONE database, N projects.

The model
---------
- A project is a COLUMN, not a table: the key is (project, id), so VA-02 of one
  project and VA-02 of another coexist with separate histories.
- A project is addressed by an opaque CODE, never by its name. No read tool
  lists projects and no error names one: whoever lacks the code cannot find the
  door. It is a guard against MISTAKE, not against will — the real boundary is
  the OAuth gate upstream.
- CONSUMERS are whoever downloads rules: chats and skills. A person is not a
  consumer: a rule that binds a person says so in its body.
- SCOPES are named sets of consumers. There is no separate notion of "group":
  a single consumer is a set with one element, and its singleton scope is
  created by a TRIGGER when the consumer is born. One kind of pointer only.
- The reading order falls out of the model: a rule reaches a consumer through
  one or more scopes, and the widest of them decides where it sits in the
  block. Breadth is a COUNT, not a convention to maintain.

Invariants
----------
- an ID is a pointer and is NEVER reused, not even by a retired rule;
- history is written by TRIGGERS, not by tool code, so a change made by hand
  with sqlite3 is recorded too;
- deletion does not exist: a rule is retired;
- whole versions are kept, not diffs: a chain of diffs rots, and 177 rules of
  text weigh nothing;
- every operation returns a VERDICT, not a dump;
- a new rule reaches nobody until it is approved. Approval is signed, covers a
  BATCH, and the signature is verified against a PUBLIC key: the private half
  never enters a conversation.
- an approved rule is PROVISIONAL and expires. Staying costs a decision, going
  is free — which is the asymmetry that stops rules from piling up.

The database is owned by root and its files are 0644: whoever mounts the share
READS it and does not touch it. Writing by hand would bypass the triggers and
break history in silence.
"""
from __future__ import annotations

import base64
import difflib
import functools
import hashlib
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

VERSION = "1.0.3"

TYPES = ("R", "M", "F")                 # R binding · M method · F technical fact
ALL = "_ALL_"                           # reaches every consumer, present and future
ALL_ALIASES = {"_all_", "*", "all", "tutti", "chiunque"}
KINDS = ("chat", "skill")
STATUSES = ("proposed", "active", "retired", "denied")
PERMANENCE = ("provisional", "permanent")

RE_ID = re.compile(r"^([A-Z]{2})-(\d{2,3})$")
RE_REF = re.compile(r"\b([A-Z]{2})-(\d{2,3})\b")
RE_CODE = re.compile(r"^[A-Za-z0-9]{8,32}$")
RE_NAME = re.compile(r"^[a-z0-9][a-z0-9 _-]{0,40}$")

FILE_MODE = 0o644                       # root writes, everyone else reads
DIR_MODE = 0o755
DEFAULT_PROVISIONAL_DAYS = 90
MAX_BODY_BYTES = 64_000
MAX_IMPORT = 500
MAX_GET_IDS = 50

# Identical answer for a missing code and a wrong one: a message that told them
# apart would be an oracle.
ERR_PROJECT = ("project not specified: this needs the project CODE, the one at the top "
               "of its instructions. Without it the registry does not answer — and there "
               "is no way to list projects: either you have it, or you ask for it.")


class RulesError(Exception):
    """A talking error: says what happened AND what to do about it."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _plus_days(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_id(rid: str) -> str:
    s = (rid or "").strip().upper()
    if s.count("-") == 2:               # 'VA-02-R': cite it bare, but do not argue
        s = s.rsplit("-", 1)[0]
    if not RE_ID.match(s):
        raise RulesError(f"malformed ID {rid!r}: it must be DOMAIN-NN, e.g. VA-02")
    return s


def _norm_name(name: str, what: str) -> str:
    s = (name or "").strip().lower()
    if not RE_NAME.match(s):
        raise RulesError(
            f"invalid {what} name {name!r}: lowercase letters, digits, space, '-' and '_', "
            "max 41 characters, and it cannot start with a separator")
    return s


def _norm_scope_list(scopes) -> list[str]:
    if isinstance(scopes, str):
        scopes = [scopes]
    out: list[str] = []
    for s in scopes or []:
        t = (s or "").strip()
        if t.lower() in ALL_ALIASES:
            t = ALL
        elif t != ALL:
            t = _norm_name(t, "scope")
        if t not in out:
            out.append(t)
    return out


# =====================================================================
# Signature — ed25519, and the database holds only the PUBLIC half
# =====================================================================

def verify_signature(public_key_b64: str, message: str, signature_b64: str) -> None:
    """Raise RulesError unless signature_b64 is a valid ed25519 signature of
    `message` under public_key_b64. Both are raw base64, 32 and 64 bytes.

    Deliberately not the SSH signature format: this way there is no archaeology
    on either side, and the signer is twenty lines the user can read."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as e:                                    # pragma: no cover
        raise RulesError(f"signature verification unavailable: {e}")
    try:
        raw = base64.b64decode(public_key_b64.strip(), validate=True)
        sig = base64.b64decode(signature_b64.strip(), validate=True)
    except Exception:
        raise RulesError("public key or signature is not valid base64")
    if len(raw) != 32:
        raise RulesError(f"public key is {len(raw)} bytes, expected 32 (raw ed25519)")
    if len(sig) != 64:
        raise RulesError(f"signature is {len(sig)} bytes, expected 64 (raw ed25519)")
    try:
        Ed25519PublicKey.from_public_bytes(raw).verify(sig, message.encode("utf-8"))
    except InvalidSignature:
        raise RulesError(
            "signature does not match this digest. Either it was produced for a different "
            "batch — someone proposed a rule in the meantime — or it was signed with "
            "another key. Ask for the digest again and re-sign it.")


# =====================================================================
# Schema
# =====================================================================

# The perimeter of a rule, in two shapes, both written into history.
#   scopes    what was DECLARED  (e.g. 'deliberativi')
#   consumers who was REACHED    (resolved and expanded)
# A version is a photograph: if only the scope name were stored, changing the
# membership of that scope tomorrow would rewrite what was true yesterday.
_SCOPES_OF = """(SELECT IFNULL(GROUP_CONCAT(scope, ','), '') FROM
    (SELECT scope FROM rule_scopes
      WHERE project = {R}.project AND rule_id = {R}.id ORDER BY scope))"""

_CONSUMERS_OF = """(SELECT IFNULL(GROUP_CONCAT(c, ','), '') FROM
    (SELECT DISTINCT m.consumer AS c
       FROM rule_scopes s
       JOIN scope_members m ON m.project = s.project AND m.scope = s.scope
      WHERE s.project = {R}.project AND s.rule_id = {R}.id
      UNION
     SELECT k.name FROM consumers k
      WHERE k.project = {R}.project
        AND EXISTS (SELECT 1 FROM rule_scopes z
                     WHERE z.project = {R}.project AND z.rule_id = {R}.id
                       AND z.scope = '_ALL_')
      ORDER BY 1))"""

_NEXT_VERSION = """(SELECT IFNULL(MAX(version), 0) + 1 FROM rule_versions
    WHERE project = {R}.project AND rule_id = {R}.id)"""

_VCOLS = ("project, rule_id, version, type, title, body, status, permanence, "
          "expires_at, superseded_by, changelog, scopes, consumers, ts, action, reason")


def _f(sql: str, row: str) -> str:
    return sql.format(R=row)


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS projects (
  name        TEXT PRIMARY KEY,
  code        TEXT NOT NULL UNIQUE,      -- opaque handle: the only way in
  description TEXT,
  created     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_domains (
  project     TEXT NOT NULL REFERENCES projects(name) ON DELETE CASCADE,
  domain      TEXT NOT NULL,
  description TEXT,
  PRIMARY KEY (project, domain)
);

-- Whoever downloads rules. A skill is not a chat, but it acts, and what acts
-- is under rules: calling list_rules is the only requirement.
CREATE TABLE IF NOT EXISTS consumers (
  project TEXT NOT NULL REFERENCES projects(name) ON DELETE CASCADE,
  name    TEXT NOT NULL,
  kind    TEXT NOT NULL CHECK (kind IN ('chat','skill')),
  created TEXT NOT NULL,
  PRIMARY KEY (project, name)
);

-- Named sets of consumers. managed=1 means the row was generated (a consumer's
-- singleton, or _ALL_) and must keep telling the truth about its own name.
CREATE TABLE IF NOT EXISTS scopes (
  project TEXT NOT NULL REFERENCES projects(name) ON DELETE CASCADE,
  name    TEXT NOT NULL,
  managed INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (project, name)
);

CREATE TABLE IF NOT EXISTS scope_members (
  project  TEXT NOT NULL,
  scope    TEXT NOT NULL,
  consumer TEXT NOT NULL,
  PRIMARY KEY (project, scope, consumer),
  FOREIGN KEY (project, scope)    REFERENCES scopes(project, name)    ON DELETE CASCADE,
  FOREIGN KEY (project, consumer) REFERENCES consumers(project, name) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rules (
  project       TEXT NOT NULL REFERENCES projects(name) ON DELETE CASCADE,
  id            TEXT NOT NULL,
  domain        TEXT NOT NULL,
  seq           INTEGER NOT NULL,
  type          TEXT NOT NULL CHECK (type IN ('R','M','F')),
  title         TEXT NOT NULL,
  body          TEXT NOT NULL,          -- free Markdown, rendered verbatim
  status        TEXT NOT NULL DEFAULT 'proposed'
                CHECK (status IN ('proposed','active','retired','denied')),
  permanence    TEXT NOT NULL DEFAULT 'provisional'
                CHECK (permanence IN ('provisional','permanent')),
  expires_at    TEXT,                   -- NULL for permanent rules
  superseded_by TEXT,
  denied_reason TEXT,
  changelog     TEXT,
  source        TEXT,                   -- where it came from: the renewal criterion
  reason        TEXT NOT NULL DEFAULT 'created',
  proposed_by   TEXT,
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (project, id),
  UNIQUE (project, domain, seq)
);

-- A rule points to a SET of scopes: widening it is one more row, and the group
-- it already belonged to is not touched.
-- The foreign key is DEFERRED on purpose: the engine writes the perimeter
-- BEFORE the rule, inside one transaction, so the AFTER INSERT trigger on
-- rules already sees a complete perimeter to photograph.
CREATE TABLE IF NOT EXISTS rule_scopes (
  project TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  scope   TEXT NOT NULL,
  PRIMARY KEY (project, rule_id, scope),
  FOREIGN KEY (project, rule_id) REFERENCES rules(project, id)
      ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (project, scope) REFERENCES scopes(project, name)
      DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS rule_refs (
  project TEXT NOT NULL,
  src     TEXT NOT NULL,
  dst     TEXT NOT NULL,
  PRIMARY KEY (project, src, dst),
  FOREIGN KEY (project, src) REFERENCES rules(project, id)
      ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS rule_versions (
  project       TEXT NOT NULL,
  rule_id       TEXT NOT NULL,
  version       INTEGER NOT NULL,
  type          TEXT,
  title         TEXT,
  body          TEXT,
  status        TEXT,
  permanence    TEXT,
  expires_at    TEXT,
  superseded_by TEXT,
  changelog     TEXT,
  scopes        TEXT,                   -- declared
  consumers     TEXT,                   -- reached, resolved
  ts            TEXT NOT NULL,
  action        TEXT NOT NULL,
  reason        TEXT,
  PRIMARY KEY (project, rule_id, version)
);

CREATE TABLE IF NOT EXISTS approvals (
  project     TEXT NOT NULL,
  digest      TEXT NOT NULL,
  signature   TEXT,
  n_rules     INTEGER NOT NULL,
  rule_ids    TEXT NOT NULL,
  approved_at TEXT NOT NULL,
  signed      INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (project, digest, approved_at)
);

CREATE INDEX IF NOT EXISTS ix_scope_members ON scope_members(project, consumer);
CREATE INDEX IF NOT EXISTS ix_rule_scopes   ON rule_scopes(project, scope);
CREATE INDEX IF NOT EXISTS ix_refs_dst      ON rule_refs(project, dst);
CREATE INDEX IF NOT EXISTS ix_rules_status  ON rules(project, status);

-- History is written by the ENGINE, not by tool code.
CREATE TRIGGER IF NOT EXISTS trg_rules_ins AFTER INSERT ON rules BEGIN
  INSERT INTO rule_versions ({_VCOLS})
  VALUES (NEW.project, NEW.id, {_f(_NEXT_VERSION, 'NEW')},
          NEW.type, NEW.title, NEW.body, NEW.status, NEW.permanence, NEW.expires_at,
          NEW.superseded_by, NEW.changelog,
          {_f(_SCOPES_OF, 'NEW')}, {_f(_CONSUMERS_OF, 'NEW')},
          NEW.updated_at, 'created', NEW.reason);
END;

CREATE TRIGGER IF NOT EXISTS trg_rules_upd AFTER UPDATE ON rules BEGIN
  INSERT INTO rule_versions ({_VCOLS})
  VALUES (NEW.project, NEW.id, {_f(_NEXT_VERSION, 'NEW')},
          NEW.type, NEW.title, NEW.body, NEW.status, NEW.permanence, NEW.expires_at,
          NEW.superseded_by, NEW.changelog,
          {_f(_SCOPES_OF, 'NEW')}, {_f(_CONSUMERS_OF, 'NEW')},
          NEW.updated_at, 'amended', NEW.reason);
END;

-- Safety net: if someone deletes by hand, the trace stays.
CREATE TRIGGER IF NOT EXISTS trg_rules_del AFTER DELETE ON rules BEGIN
  INSERT INTO rule_versions ({_VCOLS})
  VALUES (OLD.project, OLD.id, {_f(_NEXT_VERSION, 'OLD')},
          OLD.type, OLD.title, OLD.body, OLD.status, OLD.permanence, OLD.expires_at,
          OLD.superseded_by, OLD.changelog, '', '',
          strftime('%Y-%m-%dT%H:%M:%SZ','now'), 'DELETED', 'DELETE outside the tools');
END;

-- Changing a rule's perimeter is a change worth a version. The guard keeps the
-- trigger quiet while the rule itself does not exist yet (creation) or no
-- longer exists (cascade).
CREATE TRIGGER IF NOT EXISTS trg_scope_link_ins AFTER INSERT ON rule_scopes
WHEN EXISTS (SELECT 1 FROM rules WHERE project = NEW.project AND id = NEW.rule_id)
BEGIN
  INSERT INTO rule_versions ({_VCOLS})
  SELECT r.project, r.id, {_f(_NEXT_VERSION, 'r')},
         r.type, r.title, r.body, r.status, r.permanence, r.expires_at,
         r.superseded_by, r.changelog,
         {_f(_SCOPES_OF, 'r')}, {_f(_CONSUMERS_OF, 'r')},
         strftime('%Y-%m-%dT%H:%M:%SZ','now'), 'scope added', 'perimeter widened'
    FROM rules r WHERE r.project = NEW.project AND r.id = NEW.rule_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_scope_link_del AFTER DELETE ON rule_scopes
WHEN EXISTS (SELECT 1 FROM rules WHERE project = OLD.project AND id = OLD.rule_id)
BEGIN
  INSERT INTO rule_versions ({_VCOLS})
  SELECT r.project, r.id, {_f(_NEXT_VERSION, 'r')},
         r.type, r.title, r.body, r.status, r.permanence, r.expires_at,
         r.superseded_by, r.changelog,
         {_f(_SCOPES_OF, 'r')}, {_f(_CONSUMERS_OF, 'r')},
         strftime('%Y-%m-%dT%H:%M:%SZ','now'), 'scope removed', 'perimeter narrowed'
    FROM rules r WHERE r.project = OLD.project AND r.id = OLD.rule_id;
END;

-- Every consumer needs a scope holding itself alone, or no rule could be
-- addressed to it. The database makes it, so it exists even for a consumer
-- inserted by hand.
CREATE TRIGGER IF NOT EXISTS trg_consumer_scope AFTER INSERT ON consumers BEGIN
  INSERT INTO scopes (project, name, managed) VALUES (NEW.project, NEW.name, 1);
  INSERT INTO scope_members (project, scope, consumer)
       VALUES (NEW.project, NEW.name, NEW.name);
END;

-- A managed scope must keep telling the truth about its own name.
CREATE TRIGGER IF NOT EXISTS trg_managed_no_extra_member
BEFORE INSERT ON scope_members
WHEN (SELECT managed FROM scopes
       WHERE project = NEW.project AND name = NEW.scope) = 1
 AND NEW.consumer <> NEW.scope
BEGIN
  SELECT RAISE(ABORT, 'managed scope: it is a consumer singleton and takes no other member');
END;

CREATE TRIGGER IF NOT EXISTS trg_managed_no_member_update
BEFORE UPDATE ON scope_members
WHEN (SELECT managed FROM scopes
       WHERE project = OLD.project AND name = OLD.scope) = 1
BEGIN
  SELECT RAISE(ABORT, 'managed scope: its membership is not editable');
END;

CREATE TRIGGER IF NOT EXISTS trg_managed_no_rename
BEFORE UPDATE OF name ON scopes
WHEN OLD.managed = 1 AND NEW.name <> OLD.name
BEGIN
  SELECT RAISE(ABORT, 'managed scope: its name is its consumer''s and is not renamed here');
END;

-- A renamed consumer is a different consumer: the rules that reached it must be
-- reviewed, not dragged along behind a name. Same reasoning as rule IDs.
CREATE TRIGGER IF NOT EXISTS trg_consumer_no_rename
BEFORE UPDATE OF name ON consumers
WHEN NEW.name <> OLD.name
BEGIN
  SELECT RAISE(ABORT, 'a consumer is not renamed: create the new one and retire the old');
END;
"""

TABLES = ("projects", "project_domains", "consumers", "scopes", "scope_members",
          "rules", "rule_scopes", "rule_refs", "rule_versions", "approvals")
TRIGGERS = ("trg_rules_ins", "trg_rules_upd", "trg_rules_del",
            "trg_scope_link_ins", "trg_scope_link_del", "trg_consumer_scope",
            "trg_managed_no_extra_member", "trg_managed_no_member_update",
            "trg_managed_no_rename", "trg_consumer_no_rename")


# =====================================================================
# Registry
# =====================================================================

def _serialised(cls):
    """Wrap every public method so that only one runs against the connection at
    a time.

    This is not an optimisation, it is a correctness fix, and it was found in
    production on the very first call that touched the database. The server
    runs sync tools in a THREAD POOL — FastMCP hands them to
    anyio.to_thread.run_sync — so the connection is opened on the import thread
    and used from a worker. sqlite3 refuses that outright unless
    check_same_thread is off.

    Turning that check off alone would not be enough: this engine writes multi
    statement transactions with an explicit BEGIN, and two of those interleaving
    on one connection would produce a COMMIT that closes somebody else's
    transaction. So the check goes off AND the calls are serialised.

    The lock is re-entrant because public methods call each other: status()
    calls list_rules(), import_rules() calls check().

    Serialising costs nothing here — one user, a few calls a minute — and it
    buys the property that matters: whatever the pool does, the database sees
    one caller."""
    for name, fn in list(vars(cls).items()):
        if name.startswith("_") or not callable(fn) or isinstance(fn, staticmethod):
            continue

        def wrap(f):
            @functools.wraps(f)
            def guarded(self, *a, **kw):
                with self._lock:
                    return f(self, *a, **kw)
            return guarded

        setattr(cls, name, wrap(fn))
    return cls


@_serialised
class Registry:
    def __init__(self, db_path: str, *, public_key: str = "",
                 grace_until: str = "", provisional_days: int = DEFAULT_PROVISIONAL_DAYS) -> None:
        self.path = db_path
        self.public_key = (public_key or "").strip()
        self.grace_until = (grace_until or "").strip()
        self.provisional_days = int(provisional_days or DEFAULT_PROVISIONAL_DAYS)
        # Re-entrant, and it must exist before anything else: every public
        # method acquires it (see _serialised).
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        fresh = not os.path.exists(db_path)
        # check_same_thread=False because the server calls tools from a thread
        # pool. It is safe ONLY together with the lock above.
        self.cx = sqlite3.connect(db_path, timeout=10, isolation_level=None,
                                  check_same_thread=False)
        self.cx.row_factory = sqlite3.Row
        self.cx.execute("PRAGMA journal_mode=WAL")
        self.cx.execute("PRAGMA synchronous=FULL")
        self.cx.execute("PRAGMA foreign_keys=ON")
        self.cx.execute("PRAGMA busy_timeout=10000")
        # The schema is re-applied at every open: a missing object — typically a
        # trigger dropped by hand — is rebuilt. But the repair is DECLARED: a
        # trigger that vanishes raises no error, it just stops writing history.
        before = {r[0] for r in self.cx.execute("SELECT name FROM sqlite_master")}
        self.cx.executescript(SCHEMA)
        after = {r[0] for r in self.cx.execute("SELECT name FROM sqlite_master")}
        self.repaired = [] if fresh else sorted(after - before)
        self._fix_modes()

    # ---------- housekeeping ----------

    def _fix_modes(self) -> None:
        """0644 is DELIBERATE: whoever mounts the share reads and does not touch."""
        for f in (self.path, self.path + "-wal", self.path + "-shm"):
            if os.path.exists(f):
                try:
                    os.chmod(f, FILE_MODE)
                except OSError:
                    pass

    def close(self) -> None:
        self.cx.close()

    def in_grace(self) -> bool:
        """True while signatures are not yet required. A lock you must remember
        to switch on is a lock that stays off, so this one is a DATE and closes
        by itself."""
        return bool(self.grace_until) and _today() <= self.grace_until

    def _require_signature(self, project: str, message: str, signature: str,
                           n: int, ids: list[str]) -> bool:
        """Returns True if the batch was signed, False if it passed under grace.
        Records the approval either way — including that it was unsigned."""
        signed = True
        if self.in_grace() and not signature:
            signed = False
        elif not self.public_key:
            raise RulesError(
                "no approval public key is configured (APPROVAL_PUBKEY) and the grace "
                "window is closed: nothing can be approved. Set the key, or reopen grace.")
        else:
            verify_signature(self.public_key, message, signature)
        self.cx.execute(
            "INSERT INTO approvals (project, digest, signature, n_rules, rule_ids, "
            "approved_at, signed) VALUES (?,?,?,?,?,?,?)",
            (project, message, signature or None, n, ",".join(ids), _now(), 1 if signed else 0))
        return signed

    # ---------- projects ----------

    def _project(self, code: str) -> str:
        """From the CODE to the internal name. Never from the name: the name is
        not an access key. Identical error for a missing and a wrong code."""
        c = (code or "").strip()
        if not c:
            raise RulesError(ERR_PROJECT)
        row = self.cx.execute("SELECT name FROM projects WHERE code=?", (c,)).fetchone()
        if row is None:
            raise RulesError(ERR_PROJECT)
        return row[0]

    def _consumer(self, project: str, name: str) -> str:
        n = (name or "").strip().lower()
        allowed = [r[0] for r in self.cx.execute(
            "SELECT name FROM consumers WHERE project=? ORDER BY name", (project,))]
        if not n:
            raise RulesError(f"consumer not specified. This project has: {', '.join(allowed)}")
        if n not in allowed:
            raise RulesError(
                f"unknown consumer {name!r}. This project has: {', '.join(allowed) or '(none)'}")
        return n

    def _domains(self, project: str) -> list[str]:
        return [r[0] for r in self.cx.execute(
            "SELECT domain FROM project_domains WHERE project=? ORDER BY domain", (project,))]

    def projects(self) -> dict:
        rows = self.cx.execute("SELECT name, code, description, created FROM projects "
                               "ORDER BY name").fetchall()
        out = []
        for r in rows:
            n = self.cx.execute("SELECT COUNT(*) FROM rules WHERE project=? AND status='active'",
                                (r["name"],)).fetchone()[0]
            out.append({"name": r["name"], "code": r["code"], "description": r["description"],
                        "created": r["created"], "active_rules": n})
        return {"projects": out, "count": len(out)}

    def project_info(self, code: str) -> dict:
        p = self._project(code)
        cons = self.cx.execute("SELECT name, kind FROM consumers WHERE project=? ORDER BY kind, name",
                               (p,)).fetchall()
        scopes = []
        for s in self.cx.execute("SELECT name, managed FROM scopes WHERE project=? ORDER BY name",
                                 (p,)).fetchall():
            scopes.append({"name": s["name"], "managed": bool(s["managed"]),
                           "breadth": self._breadth(p, s["name"]),
                           "members": self._members(p, s["name"])})
        doms = self.cx.execute("SELECT domain, description FROM project_domains "
                               "WHERE project=? ORDER BY domain", (p,)).fetchall()
        return {
            "project": p,
            "consumers": [{"name": c["name"], "kind": c["kind"]} for c in cons],
            "scopes": scopes,
            "domains": {d["domain"]: d["description"] for d in doms},
            "registry_version": VERSION,
            "approval": {"required": not self.in_grace(),
                         "grace_until": self.grace_until or None,
                         "provisional_days": self.provisional_days},
        }

    def create_project(self, code: str, name: str, consumers, domains,
                       description: str = "") -> dict:
        code = (code or "").strip()
        if not RE_CODE.match(code):
            raise RulesError("project code: 8 to 32 alphanumeric characters, nothing else")
        name = (name or "").strip()
        if not name:
            raise RulesError("the project needs a name")
        if self.cx.execute("SELECT 1 FROM projects WHERE name=?", (name,)).fetchone():
            raise RulesError(f"a project named {name!r} already exists")
        if self.cx.execute("SELECT 1 FROM projects WHERE code=?", (code,)).fetchone():
            raise RulesError("that code is already in use")
        if isinstance(domains, (list, tuple)):
            domains = {d: "" for d in domains}
        if not domains:
            raise RulesError("declare at least one domain, e.g. {'VA': 'vault and files'}")
        for d in domains:
            if not re.match(r"^[A-Z]{2}$", d):
                raise RulesError(f"domain {d!r}: exactly two uppercase letters")
        cons = self._normalise_consumers(consumers)
        if not cons:
            raise RulesError("declare at least one consumer, e.g. [['architect','chat']]")
        try:
            self.cx.execute("BEGIN")
            self.cx.execute("INSERT INTO projects (name, code, description, created) "
                            "VALUES (?,?,?,?)", (name, code, description or None, _now()))
            for d, desc in domains.items():
                self.cx.execute("INSERT INTO project_domains (project, domain, description) "
                                "VALUES (?,?,?)", (name, d, desc or None))
            self.cx.execute("INSERT INTO scopes (project, name, managed) VALUES (?,?,1)",
                            (name, ALL))
            for cname, kind in cons:
                self.cx.execute("INSERT INTO consumers (project, name, kind, created) "
                                "VALUES (?,?,?,?)", (name, cname, kind, _now()))
            self.cx.execute("COMMIT")
        except Exception:
            self.cx.execute("ROLLBACK")
            raise
        return {"created": name, "code": code, "consumers": [c for c, _ in cons],
                "domains": sorted(domains), "note": "put the code at the top of the "
                "project instructions: it is the only way to reach this registry"}

    @staticmethod
    def _normalise_consumers(consumers) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for item in consumers or []:
            if isinstance(item, str):
                cname, kind = item, "chat"
            elif isinstance(item, dict):
                cname, kind = item.get("name", ""), item.get("kind", "chat")
            else:
                cname, kind = (list(item) + ["chat"])[:2]
            cname = _norm_name(cname, "consumer")
            kind = (kind or "chat").strip().lower()
            if kind not in KINDS:
                raise RulesError(f"kind {kind!r}: it must be one of {', '.join(KINDS)}")
            if cname == ALL.lower() or cname == ALL:
                raise RulesError(f"{ALL} is reserved and is not a consumer name")
            if cname not in [c for c, _ in out]:
                out.append((cname, kind))
        return out

    def rekey_project(self, code: str, new_code: str) -> dict:
        p = self._project(code)
        new_code = (new_code or "").strip()
        if not RE_CODE.match(new_code):
            raise RulesError("new code: 8 to 32 alphanumeric characters")
        if self.cx.execute("SELECT 1 FROM projects WHERE code=?", (new_code,)).fetchone():
            raise RulesError("that code is already in use")
        self.cx.execute("UPDATE projects SET code=? WHERE name=?", (new_code, p))
        return {"project": p, "rekeyed": True,
                "note": "update the project instructions BEFORE closing this chat: "
                        "the old code no longer reaches anything"}

    def add_consumers(self, code: str, consumers) -> dict:
        """Only adds. Removing a consumer would orphan the rules aimed at it."""
        p = self._project(code)
        cons = self._normalise_consumers(consumers)
        added = []
        for cname, kind in cons:
            if self.cx.execute("SELECT 1 FROM consumers WHERE project=? AND name=?",
                               (p, cname)).fetchone():
                continue
            if self.cx.execute("SELECT 1 FROM scopes WHERE project=? AND name=?",
                               (p, cname)).fetchone():
                raise RulesError(
                    f"a scope named {cname!r} already exists: a consumer and a scope share "
                    "one namespace, because every consumer gets a scope with its own name")
            self.cx.execute("INSERT INTO consumers (project, name, kind, created) VALUES (?,?,?,?)",
                            (p, cname, kind, _now()))
            added.append(cname)
        return {"project": p, "added": added,
                "note": "each one also got a scope of its own, made by the database"}

    def add_domains(self, code: str, domains) -> dict:
        p = self._project(code)
        if isinstance(domains, (list, tuple)):
            domains = {d: "" for d in domains}
        added = []
        for d, desc in (domains or {}).items():
            if not re.match(r"^[A-Z]{2}$", d):
                raise RulesError(f"domain {d!r}: exactly two uppercase letters")
            if self.cx.execute("SELECT 1 FROM project_domains WHERE project=? AND domain=?",
                               (p, d)).fetchone():
                continue
            self.cx.execute("INSERT INTO project_domains (project, domain, description) "
                            "VALUES (?,?,?)", (p, d, desc or None))
            added.append(d)
        return {"project": p, "added": added}

    # ---------- scopes ----------

    def _breadth(self, project: str, scope: str) -> int:
        """How many consumers a scope reaches. _ALL_ is not a listed set: it must
        reach consumers that do not exist yet, so its breadth is computed."""
        if scope == ALL:
            return self.cx.execute("SELECT COUNT(*) FROM consumers WHERE project=?",
                                   (project,)).fetchone()[0]
        return self.cx.execute("SELECT COUNT(*) FROM scope_members WHERE project=? AND scope=?",
                               (project, scope)).fetchone()[0]

    def _members(self, project: str, scope: str) -> list[str]:
        if scope == ALL:
            return [r[0] for r in self.cx.execute(
                "SELECT name FROM consumers WHERE project=? ORDER BY name", (project,))]
        return [r[0] for r in self.cx.execute(
            "SELECT consumer FROM scope_members WHERE project=? AND scope=? ORDER BY consumer",
            (project, scope))]

    def create_scope(self, code: str, name: str, members) -> dict:
        p = self._project(code)
        name = _norm_name(name, "scope")
        if self.cx.execute("SELECT 1 FROM scopes WHERE project=? AND name=?",
                           (p, name)).fetchone():
            raise RulesError(f"a scope named {name!r} already exists")
        if self.cx.execute("SELECT 1 FROM consumers WHERE project=? AND name=?",
                           (p, name)).fetchone():
            raise RulesError(f"{name!r} is a consumer: its singleton scope already exists")
        members = [self._consumer(p, m) for m in (members or [])]
        if len(members) < 2:
            raise RulesError(
                "a scope with fewer than two members adds nothing: every consumer already "
                "has its own singleton, made by the database")
        self.cx.execute("BEGIN")
        try:
            self.cx.execute("INSERT INTO scopes (project, name, managed) VALUES (?,?,0)",
                            (p, name))
            for m in members:
                self.cx.execute("INSERT INTO scope_members (project, scope, consumer) "
                                "VALUES (?,?,?)", (p, name, m))
            self.cx.execute("COMMIT")
        except Exception:
            self.cx.execute("ROLLBACK")
            raise
        return {"project": p, "scope": name, "members": members, "breadth": len(members)}

    def edit_scope(self, code: str, name: str, add=None, remove=None) -> dict:
        """Careful: this changes the perimeter of EVERY rule pointing at this
        scope. To widen a single rule use widen_rule instead."""
        p = self._project(code)
        name = _norm_name(name, "scope")
        row = self.cx.execute("SELECT managed FROM scopes WHERE project=? AND name=?",
                              (p, name)).fetchone()
        if row is None:
            raise RulesError(f"no scope named {name!r} in this project")
        if row["managed"]:
            raise RulesError(f"{name!r} is a managed scope (a consumer singleton, or {ALL}): "
                             "its membership is fixed by construction")
        for m in (add or []):
            c = self._consumer(p, m)
            self.cx.execute("INSERT OR IGNORE INTO scope_members (project, scope, consumer) "
                            "VALUES (?,?,?)", (p, name, c))
        for m in (remove or []):
            self.cx.execute("DELETE FROM scope_members WHERE project=? AND scope=? AND consumer=?",
                            (p, name, (m or '').strip().lower()))
        n = self.cx.execute("SELECT COUNT(*) FROM rule_scopes WHERE project=? AND scope=?",
                            (p, name)).fetchone()[0]
        return {"project": p, "scope": name, "members": self._members(p, name),
                "rules_affected": n}

    # ---------- reading rules ----------

    def _row(self, p: str, rid: str):
        return self.cx.execute("SELECT * FROM rules WHERE project=? AND id=?", (p, rid)).fetchone()

    def _scopes_of(self, p: str, rid: str) -> list[str]:
        return [r[0] for r in self.cx.execute(
            "SELECT scope FROM rule_scopes WHERE project=? AND rule_id=? ORDER BY scope", (p, rid))]

    def _version(self, p: str, rid: str) -> int:
        r = self.cx.execute("SELECT IFNULL(MAX(version),0) FROM rule_versions "
                            "WHERE project=? AND rule_id=?", (p, rid)).fetchone()
        return r[0]

    def _dict(self, row, p: str) -> dict:
        d = {"id": row["id"], "type": row["type"], "title": row["title"], "body": row["body"],
             "status": row["status"], "permanence": row["permanence"],
             "expires_at": row["expires_at"], "scopes": self._scopes_of(p, row["id"]),
             "version": self._version(p, row["id"]), "changelog": row["changelog"],
             "source": row["source"], "updated_at": row["updated_at"]}
        if row["superseded_by"]:
            d["superseded_by"] = row["superseded_by"]
        if row["denied_reason"]:
            d["denied_reason"] = row["denied_reason"]
        return d

    _IN_FORCE = ("status = 'active' AND (permanence = 'permanent' "
                 "OR expires_at IS NULL OR expires_at > :now)")

    def _reaching(self, p: str, consumer: str) -> dict[str, tuple[int, list[str]]]:
        """rule_id -> (breadth of the widest scope it arrives through, scopes)."""
        rows = self.cx.execute(
            "SELECT s.rule_id, s.scope FROM rule_scopes s "
            " WHERE s.project = :p AND (s.scope = :all OR EXISTS ("
            "   SELECT 1 FROM scope_members m WHERE m.project = :p "
            "     AND m.scope = s.scope AND m.consumer = :c))",
            {"p": p, "c": consumer, "all": ALL}).fetchall()
        out: dict[str, tuple[int, list[str]]] = {}
        cache: dict[str, int] = {}
        for r in rows:
            sc = r["scope"]
            if sc not in cache:
                cache[sc] = self._breadth(p, sc)
            b, lst = out.get(r["rule_id"], (0, []))
            out[r["rule_id"]] = (max(b, cache[sc]), sorted(lst + [sc]))
        return out

    def list_rules(self, code: str, consumer: str) -> dict:
        """Every rule in force for one consumer, in ONE call, ordered from the
        most widespread to the most specific. The order IS the breadth of the
        scope: it stays right on its own when a consumer is added."""
        p = self._project(code)
        c = self._consumer(p, consumer)
        reaching = self._reaching(p, c)
        now = _now()
        rules = []
        for rid, (breadth, scopes) in reaching.items():
            row = self._row(p, rid)
            if row is None:
                continue
            if row["status"] != "active":
                continue
            if row["permanence"] != "permanent" and row["expires_at"] and row["expires_at"] <= now:
                continue
            d = self._dict(row, p)
            d["via"] = scopes
            d["breadth"] = breadth
            rules.append(d)
        rules.sort(key=lambda d: (-d["breadth"], d["id"][:2], int(d["id"].split("-")[1])))
        total = self.cx.execute(
            "SELECT COUNT(*) FROM rules WHERE project=:p AND " + self._IN_FORCE,
            {"p": p, "now": now}).fetchone()[0]
        return {"project": p, "consumer": c, "rules": rules, "count": len(rules),
                "outside_your_scope": total - len(rules),
                "note": "ordered by breadth: what comes first binds everyone. If an ID you "
                        "need is missing it is not undefined — it belongs to someone else"}

    def get_rules(self, code: str, ids, consumer: str) -> dict:
        """One or MANY rules by ID. Three different answers, kept apart per ID:
        the rule · it exists but is not yours · never defined, which means a
        broken citation and must be reported."""
        p = self._project(code)
        c = self._consumer(p, consumer)
        if isinstance(ids, str):
            ids = [ids]
        ids = [_norm_id(i) for i in (ids or [])]
        if not ids:
            raise RulesError("no ID asked for")
        if len(ids) > MAX_GET_IDS:
            raise RulesError(f"{len(ids)} IDs at once: the ceiling is {MAX_GET_IDS}")
        reaching = self._reaching(p, c)
        found, not_yours, never = [], [], []
        for rid in ids:
            row = self._row(p, rid)
            if row is None:
                never.append(rid)
            elif rid in reaching:
                d = self._dict(row, p)
                d["via"] = reaching[rid][1]
                found.append(d)
            else:
                not_yours.append({"id": rid, "held_by": self._holders(p, rid)})
        out = {"project": p, "consumer": c, "found": found,
               "not_yours": not_yours, "never_defined": never}
        if never:
            out["warning"] = ("never_defined means a BROKEN CITATION: those IDs were never "
                              "defined in this project. Report them to the Architect — or you "
                              "are using another project's code.")
        return out

    def _holders(self, p: str, rid: str) -> list[str]:
        rows = self.cx.execute(
            "SELECT DISTINCT m.consumer FROM rule_scopes s "
            "  JOIN scope_members m ON m.project=s.project AND m.scope=s.scope "
            " WHERE s.project=? AND s.rule_id=? ORDER BY m.consumer", (p, rid)).fetchall()
        return [r[0] for r in rows]

    def search(self, code: str, text: str, consumer: str) -> dict:
        p = self._project(code)
        c = self._consumer(p, consumer)
        q = (text or "").strip().lower()
        if len(q) < 2:
            raise RulesError("search text: at least two characters")
        reaching = self._reaching(p, c)
        now = _now()
        hits, outside = [], 0
        rows = self.cx.execute("SELECT * FROM rules WHERE project=:p AND " + self._IN_FORCE,
                               {"p": p, "now": now}).fetchall()
        for row in rows:
            if q not in (row["title"] or "").lower() and q not in (row["body"] or "").lower():
                continue
            if row["id"] in reaching:
                d = self._dict(row, p)
                d["via"] = reaching[row["id"]][1]
                hits.append(d)
            else:
                outside += 1
        hits.sort(key=lambda d: (d["id"][:2], int(d["id"].split("-")[1])))
        return {"project": p, "consumer": c, "hits": hits, "count": len(hits),
                "outside_your_scope": outside}

    def pending(self, code: str, consumer: str = "") -> dict:
        """The consumer's noticeboard: my proposals waiting, my denied ones with
        the reason, my provisional rules about to expire. This replaces the
        notes a chat used to keep in its own memory."""
        p = self._project(code)
        c = self._consumer(p, consumer) if consumer else ""
        where = "project=:p" + (" AND proposed_by=:c" if c else "")
        args = {"p": p, "c": c} if c else {"p": p}
        waiting = [self._dict(r, p) for r in self.cx.execute(
            f"SELECT * FROM rules WHERE {where} AND status='proposed' ORDER BY id",
            args).fetchall()]
        denied = [self._dict(r, p) for r in self.cx.execute(
            f"SELECT * FROM rules WHERE {where} AND status='denied' ORDER BY updated_at DESC",
            args).fetchall()]
        soon = _plus_days(30)
        now = _now()
        expiring = []
        if c:
            for rid in self._reaching(p, c):
                row = self._row(p, rid)
                if row is None or row["status"] != "active":
                    continue
                if row["permanence"] == "permanent" or not row["expires_at"]:
                    continue
                if now < row["expires_at"] <= soon:
                    expiring.append(self._dict(row, p))
        return {"project": p, "consumer": c or "(all)",
                "waiting": waiting, "denied": denied, "expiring_within_30_days": expiring,
                "approval_required": not self.in_grace(),
                "note": "a denied proposal is kept on purpose: the same idea cannot come "
                        "back through another chat in three weeks"}

    # ---------- verdicts ----------

    def status(self, code: str) -> dict:
        p = self._project(code)
        now = _now()
        q = lambda sql, **kw: self.cx.execute(sql, {"p": p, "now": now, **kw}).fetchone()[0]
        by_domain = {r[0]: r[1] for r in self.cx.execute(
            "SELECT domain, COUNT(*) FROM rules WHERE project=:p AND " + self._IN_FORCE +
            " GROUP BY domain ORDER BY domain", {"p": p, "now": now})}
        by_consumer = {}
        for c in [r[0] for r in self.cx.execute(
                "SELECT name FROM consumers WHERE project=? ORDER BY name", (p,))]:
            by_consumer[c] = len(self.list_rules(code, c)["rules"])
        return {
            "project": p,
            "database": {"path": self.path,
                         "integrity": self.cx.execute("PRAGMA integrity_check").fetchone()[0],
                         "journal_mode": self.cx.execute("PRAGMA journal_mode").fetchone()[0],
                         "mode": oct(os.stat(self.path).st_mode & 0o777),
                         "owner_uid": os.stat(self.path).st_uid},
            "rules": {
                "in_force": q("SELECT COUNT(*) FROM rules WHERE project=:p AND " + self._IN_FORCE),
                "proposed": q("SELECT COUNT(*) FROM rules WHERE project=:p AND status='proposed'"),
                "denied": q("SELECT COUNT(*) FROM rules WHERE project=:p AND status='denied'"),
                "retired": q("SELECT COUNT(*) FROM rules WHERE project=:p AND status='retired'"),
                "expired_not_retired": q(
                    "SELECT COUNT(*) FROM rules WHERE project=:p AND status='active' "
                    "AND permanence='provisional' AND expires_at IS NOT NULL "
                    "AND expires_at <= :now"),
                "permanent": q("SELECT COUNT(*) FROM rules WHERE project=:p "
                               "AND status='active' AND permanence='permanent'"),
            },
            "by_domain": by_domain,
            "by_consumer": by_consumer,
            "approval": {"required": not self.in_grace(),
                         "grace_until": self.grace_until or None,
                         "public_key_configured": bool(self.public_key),
                         "batches_approved": q(
                             "SELECT COUNT(*) FROM approvals WHERE project=:p")},
            "registry_version": VERSION,
            "repaired_at_open": self.repaired,
        }

    def check(self, code: str) -> dict:
        """Audit: broken pointers, citations of retired rules, rules with no
        perimeter, numbering gaps, redundancy candidates."""
        p = self._project(code)
        now = _now()
        known = {r[0] for r in self.cx.execute("SELECT id FROM rules WHERE project=?", (p,))}
        retired = {r[0] for r in self.cx.execute(
            "SELECT id FROM rules WHERE project=? AND status='retired'", (p,))}
        broken, to_retired = [], []
        for r in self.cx.execute("SELECT src, dst FROM rule_refs WHERE project=?", (p,)):
            if r[1] not in known:
                broken.append({"from": r[0], "cites": r[1]})
            elif r[1] in retired:
                to_retired.append({"from": r[0], "cites": r[1]})
        no_scope = [r[0] for r in self.cx.execute(
            "SELECT id FROM rules r WHERE r.project=? AND r.status='active' AND NOT EXISTS "
            "(SELECT 1 FROM rule_scopes s WHERE s.project=r.project AND s.rule_id=r.id)", (p,))]
        gaps = []
        for d in self._domains(p):
            seqs = sorted(r[0] for r in self.cx.execute(
                "SELECT seq FROM rules WHERE project=? AND domain=?", (p, d)))
            if seqs:
                missing = [n for n in range(1, max(seqs) + 1) if n not in seqs]
                if missing:
                    gaps.append({"domain": d, "missing": missing})
        # Redundancy CANDIDATES, not a verdict: two rules in force, in the same
        # perimeter, citing the same IDs. The registry puts the pairs under your
        # eyes; deciding they say the same thing is a judgement.
        cand = []
        rows = self.cx.execute("SELECT id FROM rules WHERE project=:p AND " + self._IN_FORCE,
                               {"p": p, "now": now}).fetchall()
        info = {r[0]: (frozenset(self._scopes_of(p, r[0])),
                       frozenset(x[0] for x in self.cx.execute(
                           "SELECT dst FROM rule_refs WHERE project=? AND src=?", (p, r[0]))))
                for r in rows}
        ids = sorted(info)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                sa, ra = info[a]
                sb, rb = info[b]
                if sa and sa == sb and ra and ra == rb:
                    cand.append({"pair": [a, b], "same_scopes": sorted(sa),
                                 "same_citations": sorted(ra)})
        clean = not (broken or to_retired or no_scope)
        return {"project": p, "coherent": clean,
                "broken_pointers": broken, "citations_to_retired": to_retired,
                "rules_without_perimeter": no_scope, "numbering_gaps": gaps,
                "redundancy_candidates": cand,
                "verdict": "coherent" if clean else "there are things to fix"}

    def history(self, code: str, rid: str) -> dict:
        p = self._project(code)
        rid = _norm_id(rid)
        rows = self.cx.execute(
            "SELECT version, ts, action, reason, status, permanence, scopes, consumers "
            "  FROM rule_versions WHERE project=? AND rule_id=? ORDER BY version", (p, rid)).fetchall()
        if not rows:
            raise RulesError(f"{rid}: no history — this ID was never defined in this project")
        return {"project": p, "id": rid, "versions": [dict(r) for r in rows], "count": len(rows)}

    def compare(self, code: str, rid: str, va: int, vb: int) -> dict:
        p = self._project(code)
        rid = _norm_id(rid)
        def grab(v):
            r = self.cx.execute("SELECT * FROM rule_versions WHERE project=? AND rule_id=? "
                                "AND version=?", (p, rid, v)).fetchone()
            if r is None:
                raise RulesError(f"{rid}: version {v} does not exist (see history)")
            return r
        a, b = grab(int(va)), grab(int(vb))
        def text(r):
            return (f"type: {r['type']}\ntitle: {r['title']}\nstatus: {r['status']}\n"
                    f"permanence: {r['permanence']}\nscopes: {r['scopes']}\n"
                    f"consumers: {r['consumers']}\n\n{r['body']}\n").splitlines()
        diff = list(difflib.unified_diff(text(a), text(b),
                                         fromfile=f"v{va}", tofile=f"v{vb}", lineterm=""))
        return {"project": p, "id": rid, "from": int(va), "to": int(vb),
                "identical": not diff, "diff": "\n".join(diff)}

    # ---------- writing ----------

    def _refs(self, p: str, rid: str, body: str) -> int:
        self.cx.execute("DELETE FROM rule_refs WHERE project=? AND src=?", (p, rid))
        n = 0
        for m in RE_REF.finditer(body or ""):
            dst = f"{m.group(1)}-{m.group(2)}"
            if dst == rid:
                continue
            self.cx.execute("INSERT OR IGNORE INTO rule_refs (project, src, dst) VALUES (?,?,?)",
                            (p, rid, dst))
            n += 1
        return n

    def _check_scopes(self, p: str, scopes: list[str]) -> list[str]:
        if not scopes:
            raise RulesError("a rule with no perimeter reaches nobody: give at least one "
                             f"scope, or {ALL} if it binds everyone")
        for s in scopes:
            if not self.cx.execute("SELECT 1 FROM scopes WHERE project=? AND name=?",
                                   (p, s)).fetchone():
                raise RulesError(
                    f"{s!r} is neither a consumer nor a scope of this project. "
                    "Every consumer has a scope with its own name; groups are made "
                    "with create_scope.")
        return scopes

    def _split_id(self, p: str, rid: str) -> tuple[str, int]:
        m = RE_ID.match(rid)
        dom, seq = m.group(1), int(m.group(2))
        if dom not in self._domains(p):
            raise RulesError(f"domain {dom!r} is not declared by this project "
                             f"(declared: {', '.join(self._domains(p)) or 'none'})")
        return dom, seq

    def propose(self, code: str, rid: str, rtype: str, title: str, body: str,
                scopes, reason: str, proposed_by: str = "",
                changelog: str = "", source: str = "") -> dict:
        """File a proposal. It reaches NOBODY until the batch is approved — which
        is why this needs only the project code: an unapproved proposal cannot
        do harm, and a chat that deposits one stops keeping a note about it."""
        p = self._project(code)
        rid = _norm_id(rid)
        dom, seq = self._split_id(p, rid)
        rtype = (rtype or "").strip().upper()
        if rtype not in TYPES:
            raise RulesError(f"type {rtype!r}: R binding, M method, F technical fact. "
                             "Retirement is a STATE, not a type.")
        if not (title or "").strip():
            raise RulesError("the rule needs a title")
        if not (body or "").strip():
            raise RulesError("the rule needs a body")
        if len(body.encode()) > MAX_BODY_BYTES:
            raise RulesError(f"body over {MAX_BODY_BYTES} bytes: split the rule")
        if not (reason or "").strip():
            raise RulesError("reason is mandatory: without the why a rule cannot be "
                             "defended, and at the first opportunity it gets reopened")
        prior = self.cx.execute("SELECT id, denied_reason, updated_at FROM rules "
                                "WHERE project=? AND id=? AND status='denied'",
                                (p, rid)).fetchone()
        if prior:
            raise RulesError(
                f"{rid} was already DENIED on {prior['updated_at'][:10]} — reason: "
                f"{prior['denied_reason']}. The registry keeps refusals so the same idea "
                "cannot come back through another chat. If things have changed, say so to "
                "Alfredo rather than proposing it again.")
        if self._row(p, rid):
            raise RulesError(f"{rid} already exists in this project. IDs are never reused, "
                             "not even by a retired rule: pick the next free number.")
        scopes = self._check_scopes(p, _norm_scope_list(scopes))
        by = _norm_name(proposed_by, "consumer") if proposed_by else None
        if by:
            self._consumer(p, by)
        self.cx.execute("BEGIN")
        try:
            for s in scopes:
                self.cx.execute("INSERT INTO rule_scopes (project, rule_id, scope) VALUES (?,?,?)",
                                (p, rid, s))
            self.cx.execute(
                "INSERT INTO rules (project, id, domain, seq, type, title, body, status, "
                "permanence, changelog, source, reason, proposed_by, updated_at) "
                "VALUES (?,?,?,?,?,?,?,'proposed','provisional',?,?,?,?,?)",
                (p, rid, dom, seq, rtype, title.strip(), body, changelog or None,
                 source or None, reason.strip(), by, _now()))
            self._refs(p, rid, body)
            self.cx.execute("COMMIT")
        except sqlite3.IntegrityError as e:
            self.cx.execute("ROLLBACK")
            raise RulesError(f"{rid} refused: {e}")
        except Exception:
            self.cx.execute("ROLLBACK")
            raise
        return {"project": p, "id": rid, "status": "proposed", "scopes": scopes,
                "reaches_now": [],
                "note": "it reaches nobody until the batch is approved. Check back with "
                        "pending: you do not need to keep a note about it."}

    def batch(self, code: str) -> dict:
        """The pending batch plus its DIGEST — sha256 over the ordered list of
        IDs and bodies. You sign the batch, not the single rule: that is what
        makes the signature worth reading, and it is where three proposals that
        say the same thing become visible next to each other."""
        p = self._project(code)
        rows = self.cx.execute("SELECT * FROM rules WHERE project=? AND status='proposed' "
                               "ORDER BY id", (p,)).fetchall()
        ids = [r["id"] for r in rows]
        h = hashlib.sha256()
        h.update(p.encode())
        for r in rows:
            h.update(b"\x00")
            h.update(r["id"].encode())
            h.update(b"\x00")
            h.update((r["body"] or "").encode())
        digest = h.hexdigest()
        return {"project": p, "count": len(ids), "ids": ids,
                "proposals": [self._dict(r, p) for r in rows],
                "digest": digest,
                "approval_required": not self.in_grace(),
                "how_to_sign": ("on your own machine: python3 sign.py <digest>, over this "
                                "exact digest string, then pass the base64 signature to "
                                "approve. The private key never enters this conversation."),
                "note": "if a proposal arrives after you read this, the digest changes and "
                        "the old signature is refused. That is on purpose."}

    def approve(self, code: str, digest: str, signature: str = "") -> dict:
        p = self._project(code)
        current = self.batch(code)
        if (digest or "").strip() != current["digest"]:
            raise RulesError(
                "that digest is not the current one: the batch changed after you read it "
                "(someone proposed or denied something). Ask for the batch again and "
                "re-sign. You cannot sign one batch and have another approved.")
        if not current["ids"]:
            raise RulesError("nothing to approve: the batch is empty")
        signed = self._require_signature(p, current["digest"], signature,
                                         len(current["ids"]), current["ids"])
        expires = _plus_days(self.provisional_days)
        for rid in current["ids"]:
            self.cx.execute(
                "UPDATE rules SET status='active', permanence='provisional', expires_at=?, "
                "reason=?, updated_at=? WHERE project=? AND id=?",
                (expires, f"approved{'' if signed else ' under grace'}", _now(), p, rid))
        return {"project": p, "approved": current["ids"], "count": len(current["ids"]),
                "signed": signed, "expires_at": expires,
                "note": ("they are PROVISIONAL: unless renewed they leave the lists by "
                         "themselves. Staying costs a decision, going is free.")}

    def deny(self, code: str, ids, reason: str) -> dict:
        """No signature: denying cannot do harm. And an explicit denial turns
        silence into an answer — the chat learns instead of guessing."""
        p = self._project(code)
        if not (reason or "").strip():
            raise RulesError("a denial without a reason teaches nothing: say why")
        if isinstance(ids, str):
            ids = [ids]
        out = []
        for rid in [_norm_id(i) for i in (ids or [])]:
            row = self._row(p, rid)
            if row is None:
                raise RulesError(f"{rid}: no such proposal")
            if row["status"] != "proposed":
                raise RulesError(f"{rid} is {row['status']}, not a pending proposal")
            self.cx.execute("UPDATE rules SET status='denied', denied_reason=?, reason=?, "
                            "updated_at=? WHERE project=? AND id=?",
                            (reason.strip(), "denied", _now(), p, rid))
            out.append(rid)
        return {"project": p, "denied": out, "reason": reason.strip(),
                "note": "the row stays: the ID is burnt and the same idea cannot come back "
                        "through another chat"}

    def renew(self, code: str, ids, signature: str = "", days: int = 0) -> dict:
        """Keeping a rule alive is letting it in again, so it is signed too."""
        p = self._project(code)
        if isinstance(ids, str):
            ids = [ids]
        ids = sorted(_norm_id(i) for i in (ids or []))
        if not ids:
            raise RulesError("no ID to renew")
        for rid in ids:
            row = self._row(p, rid)
            if row is None or row["status"] != "active":
                raise RulesError(f"{rid}: not an active rule")
        message = hashlib.sha256(("renew|" + p + "|" + ",".join(ids)).encode()).hexdigest()
        signed = self._require_signature(p, message, signature, len(ids), ids)
        expires = _plus_days(int(days) or self.provisional_days)
        for rid in ids:
            self.cx.execute("UPDATE rules SET expires_at=?, reason=?, updated_at=? "
                            "WHERE project=? AND id=?",
                            (expires, "renewed", _now(), p, rid))
        return {"project": p, "renewed": ids, "expires_at": expires, "signed": signed,
                "digest": message}

    def promote(self, code: str, ids, signature: str = "") -> dict:
        """From provisional to permanent. Rare, deliberate, and signed."""
        p = self._project(code)
        if isinstance(ids, str):
            ids = [ids]
        ids = sorted(_norm_id(i) for i in (ids or []))
        if not ids:
            raise RulesError("no ID to promote")
        for rid in ids:
            row = self._row(p, rid)
            if row is None or row["status"] != "active":
                raise RulesError(f"{rid}: not an active rule")
        message = hashlib.sha256(("promote|" + p + "|" + ",".join(ids)).encode()).hexdigest()
        signed = self._require_signature(p, message, signature, len(ids), ids)
        for rid in ids:
            self.cx.execute("UPDATE rules SET permanence='permanent', expires_at=NULL, "
                            "reason=?, updated_at=? WHERE project=? AND id=?",
                            ("promoted to permanent", _now(), p, rid))
        return {"project": p, "promoted": ids, "signed": signed, "digest": message}

    def amend(self, code: str, rid: str, expected_version: int, reason: str,
              title: str = None, body: str = None, rtype: str = None,
              changelog: str = None) -> dict:
        """Fix a DEFECT in place: a wrong number, a broken pointer, a sentence
        that says something false. Same ID, the rule stays in force.
        A superseded DECISION is not fixed this way: propose the new one and
        retire the old pointing at it."""
        p = self._project(code)
        rid = _norm_id(rid)
        row = self._row(p, rid)
        if row is None:
            raise RulesError(f"{rid}: never defined in this project")
        if not (reason or "").strip():
            raise RulesError("reason is mandatory")
        cur = self._version(p, rid)
        if int(expected_version) != cur:
            raise RulesError(f"{rid} is at version {cur}, you read {expected_version}: "
                             "someone wrote in the meantime. Re-read and retry.")
        if rtype is not None and rtype.strip().upper() not in TYPES:
            raise RulesError(f"type {rtype!r}: R, M or F")
        new_body = row["body"] if body is None else body
        self.cx.execute(
            "UPDATE rules SET type=?, title=?, body=?, changelog=?, reason=?, updated_at=? "
            "WHERE project=? AND id=?",
            (row["type"] if rtype is None else rtype.strip().upper(),
             row["title"] if title is None else title.strip(),
             new_body,
             row["changelog"] if changelog is None else changelog,
             reason.strip(), _now(), p, rid))
        self._refs(p, rid, new_body)
        return {"project": p, "id": rid, "version": self._version(p, rid), "amended": True}

    def widen(self, code: str, rid: str, scopes, reason: str = "") -> dict:
        """Make a rule also reach someone else. One more row in rule_scopes: the
        scope it already belonged to is NOT touched, because that scope has other
        tenants who have nothing to do with this."""
        p = self._project(code)
        rid = _norm_id(rid)
        if self._row(p, rid) is None:
            raise RulesError(f"{rid}: never defined in this project")
        scopes = self._check_scopes(p, _norm_scope_list(scopes))
        added = []
        for s in scopes:
            if self.cx.execute("SELECT 1 FROM rule_scopes WHERE project=? AND rule_id=? "
                               "AND scope=?", (p, rid, s)).fetchone():
                continue
            self.cx.execute("INSERT INTO rule_scopes (project, rule_id, scope) VALUES (?,?,?)",
                            (p, rid, s))
            added.append(s)
        return {"project": p, "id": rid, "added": added,
                "scopes": self._scopes_of(p, rid), "reaches": self._holders(p, rid)}

    def narrow(self, code: str, rid: str, scopes) -> dict:
        p = self._project(code)
        rid = _norm_id(rid)
        if self._row(p, rid) is None:
            raise RulesError(f"{rid}: never defined in this project")
        removed = []
        for s in _norm_scope_list(scopes):
            n = self.cx.execute("DELETE FROM rule_scopes WHERE project=? AND rule_id=? "
                                "AND scope=?", (p, rid, s)).rowcount
            if n:
                removed.append(s)
        left = self._scopes_of(p, rid)
        return {"project": p, "id": rid, "removed": removed, "scopes": left,
                "warning": "this rule now reaches nobody" if not left else None}

    def retire(self, code: str, rid: str, reason: str, superseded_by: str = "",
               changelog: str = "") -> dict:
        p = self._project(code)
        rid = _norm_id(rid)
        row = self._row(p, rid)
        if row is None:
            raise RulesError(f"{rid}: never defined in this project")
        if row["status"] == "retired":
            raise RulesError(f"{rid} is already retired")
        if not (reason or "").strip():
            raise RulesError("reason is mandatory")
        sb = None
        if superseded_by:
            sb = _norm_id(superseded_by)
            if self._row(p, sb) is None:
                raise RulesError(f"{sb} does not exist: create the new rule first")
        self.cx.execute("UPDATE rules SET status='retired', superseded_by=?, changelog=?, "
                        "reason=?, updated_at=? WHERE project=? AND id=?",
                        (sb, changelog or row["changelog"], reason.strip(), _now(), p, rid))
        citing = [r[0] for r in self.cx.execute(
            "SELECT DISTINCT f.src FROM rule_refs f JOIN rules r "
            "  ON r.project=f.project AND r.id=f.src "
            " WHERE f.project=? AND f.dst=? AND r.status='active' ORDER BY f.src", (p, rid))]
        return {"project": p, "id": rid, "retired": True, "superseded_by": sb,
                "still_cited_by": citing,
                "note": "the row stays: the ID is never reused and citations must keep "
                        "resolving. Active rules still citing it need fixing."}

    # ---------- migration ----------

    def import_rules(self, code: str, rules, reason: str, permanent: bool = True) -> dict:
        """Bulk import for the MIGRATION from the Markdown files. Runs only on an
        EMPTY project: a migration happens once, on a clean table. This is the
        door that is already designed to open a single time, which is why the
        approval lock needs no global off switch."""
        p = self._project(code)
        n = self.cx.execute("SELECT COUNT(*) FROM rules WHERE project=?", (p,)).fetchone()[0]
        if n:
            raise RulesError(f"this project already holds {n} rules: import runs only on an "
                             "empty project. Use propose for one rule at a time.")
        if not rules:
            raise RulesError("nothing to import")
        if len(rules) > MAX_IMPORT:
            raise RulesError(f"{len(rules)} rules at once: the ceiling is {MAX_IMPORT}")
        if not (reason or "").strip():
            raise RulesError("reason is mandatory")
        taken, rejected = [], []
        for item in rules:
            try:
                rid = _norm_id(item.get("id", ""))
                dom, seq = self._split_id(p, rid)
                rtype = (item.get("type") or "").strip().upper()
                if rtype not in TYPES:
                    raise RulesError(f"type {rtype!r}: R, M or F")
                scopes = self._check_scopes(p, _norm_scope_list(item.get("scopes")))
                body = item.get("body") or ""
                if not body.strip():
                    raise RulesError("empty body")
                self.cx.execute("BEGIN")
                for s in scopes:
                    self.cx.execute("INSERT INTO rule_scopes (project, rule_id, scope) "
                                    "VALUES (?,?,?)", (p, rid, s))
                self.cx.execute(
                    "INSERT INTO rules (project, id, domain, seq, type, title, body, status, "
                    "permanence, expires_at, changelog, source, reason, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,'active',?,?,?,?,?,?)",
                    (p, rid, dom, seq, rtype, (item.get("title") or rid).strip(), body,
                     "permanent" if permanent else "provisional",
                     None if permanent else _plus_days(self.provisional_days),
                     item.get("changelog"), item.get("source"), reason.strip(), _now()))
                self._refs(p, rid, body)
                self.cx.execute("COMMIT")
                taken.append(rid)
            except Exception as e:
                try:
                    self.cx.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                rejected.append({"id": item.get("id"), "why": str(e)})
        audit = self.check(code)
        return {"project": p, "imported": len(taken), "ids": taken,
                "rejected": rejected, "audit": audit,
                "note": "the broken pointers listed by the audit were already in the "
                        "Markdown files: they were just not visible."}

    # ---------- derivatives ----------

    def export(self, code: str, consumer: str = "") -> dict:
        """A Markdown snapshot. It is a DERIVATIVE: the truth stays in the
        database and this regenerates. With a consumer it is the block to paste
        into that chat's memory; without one, the whole project."""
        p = self._project(code)
        lines = [f"# {p} — rules", "",
                 f"> Generated {_now()} by codifier-mcp {VERSION}. This file is a "
                 f"DERIVATIVE: the truth is the registry, and this regenerates.", ""]
        if consumer:
            c = self._consumer(p, consumer)
            data = self.list_rules(code, c)
            lines[0] = f"# {p} — rules for {c}"
            lines += [f"{data['count']} rules in force, widest first. "
                      f"{data['outside_your_scope']} are outside your perimeter.", ""]
            groups: dict[int, list] = {}
            for r in data["rules"]:
                groups.setdefault(r["breadth"], []).append(r)
            for breadth in sorted(groups, reverse=True):
                block = groups[breadth]
                via = sorted({v for r in block for v in r["via"]})
                lines += [f"## Reaching {breadth} consumer(s) — via {', '.join(via)}", ""]
                for r in block:
                    lines += [f"### {r['id']} · {r['title']}  `{r['type']}`", "", r["body"], ""]
        else:
            now = _now()
            rows = self.cx.execute("SELECT * FROM rules WHERE project=? ORDER BY domain, seq",
                                   (p,)).fetchall()
            for d in self._domains(p):
                block = [r for r in rows if r["domain"] == d]
                if not block:
                    continue
                lines += [f"## {d}", ""]
                for r in block:
                    mark = "" if r["status"] == "active" else f"  _{r['status']}_"
                    lines += [f"### {r['id']} · {r['title']}  `{r['type']}`{mark}", "",
                              f"*scopes: {', '.join(self._scopes_of(p, r['id'])) or 'none'} · "
                              f"{r['permanence']}"
                              + (f" · expires {r['expires_at'][:10]}" if r["expires_at"] else "")
                              + "*", "", r["body"], ""]
        md = "\n".join(lines)
        return {"project": p, "consumer": consumer or None,
                "markdown": md, "bytes": len(md.encode())}

    def backup(self, dest_dir: str) -> dict:
        """A quiescent copy of the WHOLE database (VACUUM INTO): it opens without
        recovery. In WAL the database is THREE files, so copying one is a
        corrupt backup."""
        os.makedirs(dest_dir, exist_ok=True)
        try:
            os.chmod(dest_dir, DIR_MODE)
        except OSError:
            pass
        name = f"codifier-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.db"
        dest = os.path.join(dest_dir, name)
        self.cx.execute("VACUUM INTO ?", (dest,))
        try:
            os.chmod(dest, FILE_MODE)
        except OSError:
            pass
        return {"backup": dest, "bytes": os.path.getsize(dest),
                "note": "quiescent copy: opens without recovery, and it is the one to take "
                        "off-site. ZFS snapshots stay the main net."}
