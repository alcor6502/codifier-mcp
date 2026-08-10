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
- an ID is ASSIGNED BY THE DATABASE, four digits, counting up per domain.
  Whoever files a rule gives the domain and gets the number back: a number is
  not a choice, it is a position in a sequence. Whoever does not pass it cannot
  pick it — the same structural guarantee, not a rule to remember;
- a CITATION is what is marked as one, `(VA-0002)`, and it is validated at the
  door: it must resolve, it must point at a rule ALREADY APPROVED, and a bare ID
  left outside the brackets is refused. So a chat cannot hallucinate a pointer,
  and a batch cannot be approved into a state where its own pointers only ever
  made sense while it was being written. On the way out every citation is
  expanded with the CURRENT title of what it points at — the gloss is generated,
  never stored, so it cannot go stale;
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

VERSION = "1.6.0"

TYPES = ("R", "M", "F")                 # R binding · M method · F technical fact
ALL = "_ALL_"                           # reaches every consumer, present and future
ALL_ALIASES = {"_all_", "*", "all", "tutti", "chiunque"}
KINDS = ("chat", "skill")
STATUSES = ("proposed", "active", "retired", "denied")
PERMANENCE = ("provisional", "permanent")

# The canonical ID is FOUR digits — the same width as the changelog. Two
# conventions where one is enough get confused, and IDs are never reused, so a
# domain that retires and rewrites burns numbers even while only twenty are
# alive: with two digits the ceiling is 99 forever, and there is no remedy the
# day you touch it.
RE_ID = re.compile(r"^([A-Z]{2})-(\d{4})$")
# What is ACCEPTED as input, everywhere an ID is read: two to four digits,
# padded to four by _norm_id. So a citation written 'VA-02' before the change
# resolves onto VA-0002 and is the same rule. No text has to be rewritten.
RE_ID_IN = re.compile(r"^([A-Z]{2})-(\d{2,4})$")
ID_DIGITS = 4
MAX_SEQ = 10 ** ID_DIGITS - 1

# A CITATION is what is MARKED as one — ROUND BRACKETS — not whatever happens
# to look like an acronym. The old pattern caught any XX-NN anywhere in the
# prose, so a sentence that merely NAMED an acronym became a citation nobody
# wanted, and a citation written slightly differently was not seen at all. A
# citation that disappears is worse than a broken one: nobody looks for it.
#
# ROUND and not double square. Double square is the vault's own link syntax, and
# reserving it here would mean a rule could never link a note — a door closed on
# something that may well be wanted later, in exchange for nothing. Round
# brackets cost nothing because the check does not hang on the bracket: it hangs
# on the SHAPE XX-NNNN, which is what makes a token an ID. An ordinary
# parenthesis is ordinary prose; a parenthesis holding an ID is a citation; an
# ID outside one is a mistake. The strictness lives in the shape, so the
# delimiter is free to be the cheap one.
#
# The gloss reading adds goes INSIDE the same brackets, which is why the pattern
# accepts it: `(VA-0002 — its title)` comes back in and the title is dropped.
# The gloss slot is DELIBERATELY narrow: one em dash, and text with no bracket
# and no newline. A wide slot was tried and it was a silent shredder — anything
# after the separator was swallowed by the compaction before the bare-ID check
# ever saw it, so `(VA-0001 | VA-0002)` stored `(VA-0001)` and lost both the
# second pointer and the author's words without a word. Narrow here means the
# odd shapes fall THROUGH the pattern, and then the bare-ID scan finds the ID
# inside and refuses out loud. Losing text quietly is the one outcome a registry
# must never have.
RE_CITE = re.compile(
    r"\(\s*([A-Za-z]{2}-\d{2,4}(?:-[RMFrmf])?)\s*(?:—\s*([^()\n]*?))?\s*\)")
# A bare ID, hunted OUTSIDE the brackets: that is a forgotten bracket, and a typo
# must not be able to become a mute citation. CASE-INSENSITIVE on both letters —
# `va-0001`, `Va-0001`, `vA-0001` are the same mistake, not three different ones,
# and everywhere else an ID is read case does not matter either.
RE_BARE = re.compile(r"\b([A-Za-z]{2})-(\d{2,})")
# What the engine looked for BEFORE this change: uppercase, two or three digits.
# Kept because import_rules has to read a legacy body exactly as the engine that
# wrote it would have, and not one token more.
RE_BARE_LEGACY = re.compile(r"\b[A-Z]{2}-\d{2,3}\b")
# Reading expands a citation with the current title; this lets that expanded
# form come back in through rules_fix without the gloss having to be stripped
# by hand. Only the pointer is ever stored, so the gloss cannot go stale.
RE_CITE_GLOSSED = re.compile(
    r"^([A-Za-z]{2}-\d{2,4}(?:-[RMFrmf])?)\s*(?:—|--|·|\|).*$", re.S)
# The separator the gloss is generated with. It is written once, here, because
# the parser has to be able to take back exactly what the writer put out.
GLOSS_SEP = " — "

RE_CODE = re.compile(r"^[A-Za-z0-9]{8,32}$")
RE_NAME = re.compile(r"^[a-z0-9][a-z0-9 _-]{0,40}$")
RE_LEGACY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,31}$")

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
    """A talking error: says what happened AND what to do about it.

    By default it means a DESIGNED REFUSAL: the caller asked for something the
    rules do not allow, or asked with stale information. A wrong project code,
    a citation that does not resolve, a version that moved under them, a reason
    left empty. Nothing is broken — the answer is no, and the message says what
    to do instead. server.py turns these into one quiet line."""


class RulesFault(RulesError):
    """A refusal's opposite: the machinery failed, and the caller could not have
    prevented it.

    It exists because server.py has to tell the two apart. A designed refusal
    becomes one line at INFO; a fault keeps its full traceback at ERROR, which
    is what a fault deserves. Without the distinction, the decorator would take
    a broken image and log it as a line beginning with the word "refused" — and
    at LOG_LEVEL=WARNING as nothing at all, inverting the very defect it exists
    to close.

    It SUBCLASSES RulesError on purpose: everything that already catches
    RulesError — the suites' must_fail, the boot path — keeps catching it, and
    the text still reaches the caller. Only its fate in the log differs.

    The line between the two is not the wording, it is WHO CAUSED IT. In this
    engine almost everything is the caller: of the eighty-odd refusals, one is
    a fault, and it is the missing signature library — an image built wrong,
    which no caller can do anything about. Two neighbours that deliberately
    stay ordinary refusals, because the reasoning is not obvious:

    - the UNIQUE collision on (project, domain, seq) when two writers take the
      same number. It comes from the database, but nothing was written and the
      message says filing it again is safe — the twin decided the same for its
      CAS conflicts, which are the same shape;
    - "no approval public key configured and the grace window is closed". It is
      configuration, not machinery, and the preflight refuses to start in that
      state — so reaching it means the window expired while the service ran,
      and the message is exactly the instruction to fix it.

    Anything sqlite raises on its own — a locked file, a half-written page, a
    full disk — never becomes a RulesError at all: this engine lets it rise
    untouched, so it already keeps its traceback."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _plus_days(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _strip_gloss(s: str) -> str:
    """Take the pointer out of anything reading may have handed back: the
    brackets, and the title the expansion added inside them."""
    s = (s or "").strip()
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1].strip()
    g = RE_CITE_GLOSSED.match(s)
    return g.group(1) if g else s


def _norm_id(rid: str) -> str:
    """The one door every ID goes through. It PADS to four digits, so the old
    two-digit form written in some older text still resolves onto the same
    rule."""
    s = _strip_gloss(rid).upper()
    if s.count("-") == 2 and s.rsplit("-", 1)[1] in TYPES:
        s = s.rsplit("-", 1)[0]         # 'VA-02-R': cite it bare, but do not argue
    m = RE_ID_IN.match(s)
    if not m:
        raise RulesError(f"malformed ID {rid!r}: it must be DOMAIN-NNNN, e.g. VA-0002")
    return f"{m.group(1)}-{int(m.group(2)):0{ID_DIGITS}d}"


def _norm_legacy(v: str) -> str:
    """The old markdown identifier of a rule. It is normalised but NOT parsed:
    it is a label from another world, and pretending it has our grammar would
    be the first step towards addressing a rule by it."""
    s = (v or "").strip().upper()
    if not s:
        return ""
    if not RE_LEGACY.match(s):
        raise RulesError(
            f"invalid legacy_id {v!r}: letters, digits, '.', '_', '-' and '/', max 32 "
            "characters. It is the OLD markdown identifier, recorded only so the "
            "citations can be mapped afterwards.")
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
        # A FAULT, and the only one in this file: the image was built without
        # the signature library. The caller asked for nothing wrong and can do
        # nothing about it, so this keeps its traceback instead of being filed
        # away as a refusal.
        raise RulesFault(f"signature verification unavailable: {e}")
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
                                        -- the why of the RULE: written at the
                                        -- proposal, and no event rewrites it
  event         TEXT,                   -- the last EVENT and its why: approved,
                                        -- denied, renewed... written by the
                                        -- lifecycle, never at the proposal
  proposed_by   TEXT,
  legacy_id     TEXT,                   -- the old markdown identifier. READ ONLY:
                                        -- never a second way to address a rule
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (project, id),
  UNIQUE (project, domain, seq)
);

-- Two rules cannot claim the same old identifier. It costs one line and it
-- catches the OPPOSITE error to the one feared: in a long seeding pass
-- forgetting a rule is possible, but entering one TWICE is likelier, and
-- without this nobody would notice. Partial, so the nulls of everything born
-- after the migration cost nothing.
CREATE UNIQUE INDEX IF NOT EXISTS ux_rules_legacy
    ON rules(project, legacy_id) WHERE legacy_id IS NOT NULL;

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
          NEW.updated_at, 'amended', NEW.event);
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
# Indexes the preflight has to see. Only the ones that carry a GUARANTEE, not
# the ones that carry speed: ux_rules_legacy is what stops two rules claiming
# the same old identifier, and a constraint nobody checks is a constraint that
# is not there.
INDEXES = ("ux_rules_legacy",)
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
        self._migrate()
        self.cx.executescript(SCHEMA)
        after = {r[0] for r in self.cx.execute("SELECT name FROM sqlite_master")}
        self.repaired = ([] if fresh else
                         sorted((after - before) - set(self._MIGRATION_OBJECTS)))
        self._fix_modes()

    # ---------- housekeeping ----------

    # Objects this version adds to a schema that already exists. They are
    # subtracted from `repaired`, because a repair means "a trigger vanished and
    # history stopped being written" — something to worry about. An upgrade
    # arriving is not that, and a warning that cries wolf on every first open
    # after a release is a warning nobody reads on the day it is right.
    _MIGRATION_OBJECTS = ("ux_rules_legacy",)

    def _migrate(self) -> None:
        """What has to change on an existing database: the new columns, and
        the one trigger whose subject moved with them. Nothing else.

        `CREATE TABLE IF NOT EXISTS` is a no-op on a table that is already
        there, so a new column never appears on a database in service. This runs
        BEFORE executescript because the index over legacy_id is in the schema
        and would fail on a table with no such column. On a fresh database there
        is nothing to migrate and the schema creates everything whole.

        AND IT DOES NOT CONVERT ANYTHING. An earlier version of this method
        widened the old two-digit IDs across every table and rewrote the bodies
        to match. It was deleted, and the reason is the same one that killed the
        bulk import: A MIGRATION IS NOT CODE, IT IS THE WORK. The rules go back
        in one at a time, by hand, each one read and decided; the only thing the
        engine owes that pass is somewhere to write down which old identifier a
        new rule replaces, which is `legacy_id` and nothing more. Converting
        prose by pattern invents citations that were never citations, and
        converting IDs behind the author's back moves the very pointers the pass
        exists to re-decide.

        So this method has one job, and when the seeding is finished and the
        mapping has been used, `legacy_id` goes too.

        Kept declarative on purpose: whatever happens here must also be in
        SCHEMA, or a fresh install and an upgraded one stop being the same
        thing."""
        self.migrated: list[str] = []
        have = {r[0] for r in self.cx.execute("SELECT name FROM sqlite_master "
                                              "WHERE type='table'")}
        if "rules" not in have:
            return
        cols = {r[1] for r in self.cx.execute("PRAGMA table_info(rules)")}
        for name, decl in (("legacy_id", "TEXT"), ("event", "TEXT")):
            if name not in cols:
                try:
                    self.cx.execute(f"ALTER TABLE rules ADD COLUMN {name} {decl}")
                except sqlite3.OperationalError as e:
                    # Two processes opening the database for the first time
                    # after an upgrade: the loser must not die at __init__.
                    if "duplicate column" not in str(e).lower():
                        raise
                else:
                    self.migrated.append(f"rules.{name}")
        if "rules.event" in self.migrated:
            # The history trigger changed subject with the column: it used to
            # copy NEW.reason into the version row, and from here on reason
            # never moves, so it copies NEW.event. CREATE TRIGGER IF NOT
            # EXISTS never replaces a body — the old trigger must go, and the
            # executescript right after this puts the new one in its place.
            # Declared, because a trigger swap on a database in service
            # happens once.
            self.cx.execute("DROP TRIGGER IF EXISTS trg_rules_upd")
            self.migrated.append("trg_rules_upd")

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

    def _dict(self, row, p: str, expand: bool = True, why: bool = False) -> dict:
        """The MAINTENANCE shape of a rule — the consumer reading is not built
        here: list_rules, get_rules and search strip their answers down to the
        ID and the body themselves. `expand` is TRUE by default because a chat
        is never offered a choice it can get wrong: what reaches a consumer
        always carries the gloss. `why` adds `reason` and the last `event`: it
        is on only where a person decides — the batch and the export."""
        body = row["body"]
        d = {"id": row["id"], "type": row["type"], "title": row["title"],
             "body": self._expand(p, body) if expand else body,
             "status": row["status"], "permanence": row["permanence"],
             "expires_at": row["expires_at"], "scopes": self._scopes_of(p, row["id"]),
             "version": self._version(p, row["id"]), "changelog": row["changelog"],
             "source": row["source"], "updated_at": row["updated_at"]}
        if row["superseded_by"]:
            d["superseded_by"] = row["superseded_by"]
        if row["denied_reason"]:
            d["denied_reason"] = row["denied_reason"]
        if row["legacy_id"]:
            d["legacy_id"] = row["legacy_id"]
        if why:
            d["reason"] = row["reason"]
            if row["event"]:
                d["event"] = row["event"]
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

    def _rules_for(self, p: str, c: str, expand: bool = True) -> dict:
        """The full rows in force for one consumer, ordered by the breadth of
        the scope they arrive through. The shared engine under TWO readings:
        list_rules strips it down to what a consumer gets, export keeps it
        whole because a person maintaining the corpus reads it."""
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
            d = self._dict(row, p, expand, why=True)
            d["via"] = scopes
            d["breadth"] = breadth
            rules.append(d)
        rules.sort(key=lambda d: (-d["breadth"], d["id"][:2], int(d["id"].split("-")[1])))
        total = self.cx.execute(
            "SELECT COUNT(*) FROM rules WHERE project=:p AND " + self._IN_FORCE,
            {"p": p, "now": now}).fetchone()[0]
        return {"rules": rules, "outside": total - len(rules)}

    def list_rules(self, code: str, consumer: str, expand: bool = True) -> dict:
        """Every rule in force for one consumer, in ONE call, ordered from the
        most widespread to the most specific. The order IS the breadth of the
        scope: it stays right on its own when a consumer is added.

        THE CONSUMER READING: each rule arrives as its ID and its body — the
        citations expanded with the current title of what they point at — and
        nothing else. The title, the dates, the perimeter and the why are
        administration, and they cost context in every chat that works under
        the rules: they live in the maintenance reading (rules_batch,
        rules_export). The ORDER is still the breadth; only the fields that
        said so went out."""
        p = self._project(code)
        c = self._consumer(p, consumer)
        data = self._rules_for(p, c, expand)
        rules = [{"id": d["id"], "body": d["body"]} for d in data["rules"]]
        return {"project": p, "consumer": c, "rules": rules, "count": len(rules),
                "outside_your_scope": data["outside"],
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
                # The consumer reading: the ID and the body. One exception,
                # and it is a verdict rather than a field: a rule NOT in force
                # says so, because handing back a retired body as if it bound
                # anybody would be the reading lying by omission.
                d = {"id": rid, "body": self._expand(p, row["body"])}
                if row["status"] != "active":
                    d["status"] = row["status"]
                elif row["permanence"] != "permanent" and row["expires_at"] \
                        and row["expires_at"] <= _now():
                    d["status"] = "expired"
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
                hits.append({"id": row["id"], "body": self._expand(p, row["body"])})
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
                "note": "a denied proposal is kept on purpose, with its reason. The registry "
                        "no longer refuses a re-proposal — the number is assigned by the "
                        "counter, so the same text filed again simply takes a new one. "
                        "Reading this list before proposing is now a habit, not a guard rail"}

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
            "migrated_at_open": self.migrated,
        }

    def check(self, code: str) -> dict:
        """Audit: broken pointers, citations of retired rules, rules with no
        perimeter, redundancy candidates.

        NUMBERING GAPS ARE GONE, and deliberately. The number is assigned by the
        database now, so a gap is impossible: the counter does not skip, and
        retiring leaves none because the row stays. A check that at steady state
        cannot tell a fault from a choice is not a check — it is a line you
        learn to skip, and the day it reports something true you have already
        stopped reading it."""
        p = self._project(code)
        now = _now()
        known = {r[0] for r in self.cx.execute("SELECT id FROM rules WHERE project=?", (p,))}
        retired = {r[0] for r in self.cx.execute(
            "SELECT id FROM rules WHERE project=? AND status='retired'", (p,))}
        # A citation is only a problem when the rule MAKING it is in force. The
        # door refuses a denied target and a target that does not resolve, but
        # it cannot refuse a target that changes state afterwards: a rule is
        # filed citing a proposal, the proposal is denied or the target is
        # retired, and nothing ever says so. Hence the three buckets — and hence
        # the filter on the SOURCE, without which a batch citing itself would
        # report the project as incoherent every single time.
        by_status = {r[0]: r[1] for r in self.cx.execute(
            "SELECT id, status FROM rules WHERE project=?", (p,))}
        in_force = {r[0] for r in self.cx.execute(
            "SELECT id FROM rules WHERE project=:p AND " + self._IN_FORCE,
            {"p": p, "now": now})}
        broken, to_retired, to_denied, to_proposed = [], [], [], []
        for r in self.cx.execute("SELECT src, dst FROM rule_refs WHERE project=? "
                                 "ORDER BY src, dst", (p,)):
            if r[1] not in known:
                broken.append({"from": r[0], "cites": r[1]})
                continue
            if r[0] not in in_force:
                continue
            bucket = {"retired": to_retired, "denied": to_denied,
                      "proposed": to_proposed}.get(by_status.get(r[1]))
            if bucket is not None:
                bucket.append({"from": r[0], "cites": r[1]})
        no_scope = [r[0] for r in self.cx.execute(
            "SELECT id FROM rules r WHERE r.project=? AND r.status='active' AND NOT EXISTS "
            "(SELECT 1 FROM rule_scopes s WHERE s.project=r.project AND s.rule_id=r.id)", (p,))]
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
        clean = not (broken or to_retired or to_denied or to_proposed or no_scope)
        return {"project": p, "coherent": clean,
                "broken_pointers": broken, "citations_to_retired": to_retired,
                "citations_to_denied": to_denied,
                "citations_to_proposed": to_proposed,
                "rules_without_perimeter": no_scope,
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
        out = {"project": p, "id": rid, "versions": [dict(r) for r in rows], "count": len(rows)}
        # legacy_id is immutable, so it belongs to the rule and not to any one
        # version: putting it in every row of the history would be noise.
        cur = self._row(p, rid)
        if cur is not None and cur["legacy_id"]:
            out["legacy_id"] = cur["legacy_id"]
        return out

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

    # ---------- citations ----------

    def _cites(self, p: str, body: str, self_id: str = "") -> list[str]:
        """Parse a body and VALIDATE its citations. Raises, so this is the door.

        Four refusals:
          · a bare ID left OUTSIDE the brackets, which is a forgotten bracket
            and would otherwise become a mute citation. Case does not matter:
            `va-0001` and `Va-0001` are the same mistake;
          · a citation that does not RESOLVE — a chat cannot hallucinate a
            pointer, because the proposal does not go in;
          · a citation towards a rule that is NOT YET APPROVED;
          · a gloss of your own inside the brackets, because what is between
            them is not stored and dropping it silently would be worse.

        THE THIRD ONE IS THE LOAD-BEARING ONE, and it is a decision about how
        the corpus is built, not a technicality. You may only cite a rule that
        has already been through approval. So the order of work is forced: file
        the cited rule, get it approved, then file the one that cites it. A
        proposal that needs a rule which does not exist yet simply waits.

        The alternative — citing something still in the batch — looks convenient
        and is a trap: the number of a proposal is not final until it is in, so
        a batch whose members cite each other is a batch that can be approved
        into an inconsistent state. Nobody is writing twelve thousand rules
        here; waiting one round is cheaper than a registry whose pointers were
        right only at the moment they were written.

        There is NO escape hatch on the bare-ID check, and the reason is worth
        keeping: an exception was proposed — IDs inside backticks do not count —
        so that a rule ABOUT the format of IDs could be written. A rule about
        how rules are written must not exist: that matter belongs to the manual,
        which a chat reads before writing. If a body ever trips over the check,
        the cure is to rewrite the sentence, never to add an exception.

        It cannot live in a trigger: SQLite has no regular expressions, and a
        trigger calling a REGEXP the application registers would fail the moment
        somebody opened the file with sqlite3 by hand."""
        body = body or ""
        out: list[str] = []
        glossed: list[tuple[str, str]] = []
        for m in RE_CITE.finditer(body):
            # ONE normalising door, the same one rules_get uses. Two doors with
            # two ideas of what an ID looks like is how a tolerance documented
            # in one place becomes a refusal in another. The pattern already
            # guarantees the shape, so this cannot raise.
            dst = _norm_id(m.group(1))
            if m.group(2) is not None:
                glossed.append((dst, m.group(2).strip()))
            if dst == self_id:
                continue
            if dst not in out:
                out.append(dst)
        # Anything shaped like an ID and NOT inside brackets of its own: a
        # forgotten bracket. An ordinary parenthesis is ordinary prose — what
        # makes a token a citation is the shape XX-NNNN, not the bracket.
        #
        # Only the DECLARED domains of this project are hunted. Refusing every
        # two-letter-and-digits token caught a URL path, a locale, a ticket
        # number — things no rewriting of the sentence can fix — while catching
        # nothing extra: a forgotten bracket is always a forgotten bracket
        # around a domain that exists.
        doms = set(self._domains(p))
        outside = RE_CITE.sub(" ", body)
        stray = sorted({f"{m.group(1).upper()}-{m.group(2)}"
                        for m in RE_BARE.finditer(outside)
                        if m.group(1).upper() in doms})
        if stray:
            example = stray[0]
            try:
                example = _norm_id(example)
            except RulesError:
                pass
            raise RulesError(
                f"bare ID in the body: {', '.join(stray)}. A citation is the ID ALONE "
                f"inside round brackets — ({example}) — so 'see {stray[0]}' and "
                f"'(see {stray[0]})' are both refused: in the second the brackets hold a "
                "sentence, not a pointer. Outside a bracket of its own an ID is a typo, and "
                "a typo must not be able to turn into a citation nobody sees. If you did "
                "not mean a rule at all, rewrite the token so it does not read as one of "
                "this project's IDs. There is no exception, on purpose.")
        missing = [d for d in out if self._row(p, d) is None]
        if missing:
            raise RulesError(
                f"citation that does not resolve: {', '.join(missing)} "
                f"{'were' if len(missing) > 1 else 'was'} never defined in this project.")
        unborn = sorted(d for d in out
                        if self._row(p, d)["status"] in ("proposed", "denied"))
        if unborn:
            raise RulesError(
                f"citation towards a rule that is not in force yet: {', '.join(unborn)}. "
                "You may only cite a rule that has ALREADY been approved. File the cited "
                "rule first, have it approved, then file this one — a batch whose members "
                "cite each other can be approved into a state where the pointers were only "
                "ever right at the moment they were written. If it was refused, "
                "rules_pending says why.")
        # THE GLOSS IS CHECKED, NOT SWALLOWED. Reading hands back
        # `(VA-0002 — its title)` and pasting that straight back must work — but
        # anything else inside those brackets is the author's own words, and
        # dropping them on the way to storage would be a registry losing text in
        # silence. So the only gloss accepted is the one the registry itself
        # would have written.
        for dst, gloss in glossed:
            row = self._row(p, dst)
            wanted = self._gloss(row)
            if gloss == wanted or gloss.startswith(wanted + " ·"):
                continue
            raise RulesError(
                f"the text inside ({dst} — …) is not that rule's title. A citation is the "
                f"ID alone; the title is added when you READ, so the only thing that may "
                f"sit there is what came back — right now that is {wanted!r}. If you have "
                "something of your own to say about the rule, say it outside the brackets: "
                "what goes in them is stored, and a note stored there could not be dropped "
                "without losing your words.")
        return out

    @staticmethod
    def _legacy_cites(body: str) -> list[str]:
        """The citations a body HAPPENS to carry, without judging it: marked
        ones AND bare IDs, because that is what the old parser counted.

        Only import_rules uses this, and it uses the OLD engine's pattern on
        purpose — uppercase, two or three digits — not the wider one the new
        check hunts with. Its bodies come from a world where the acronyms were
        bare, so reading only the marked ones would silently drop the whole
        reference graph and the audit in its wake would call a broken project
        clean. But reading MORE than the old parser did is just as wrong: it
        would invent pointers that were never pointers, and a seeding pass would
        go chasing them."""
        out: list[str] = []
        for raw in ([m.group(1) for m in RE_CITE.finditer(body or "")]
                    + [m.group(0) for m in RE_BARE_LEGACY.finditer(
                        RE_CITE.sub(" ", body or ""))]):
            try:
                dst = _norm_id(_strip_gloss(raw))
            except RulesError:
                continue
            if dst not in out:
                out.append(dst)
        return out

    @staticmethod
    def _compact(body: str) -> str:
        """Put a body back into its stored form: every citation reduced to the
        bare pointer.

        Reading expands, and a maintainer is told to paste back what they read —
        so without this the gloss WOULD be stored, and a title changed the next
        day would leave a stale copy of itself inside somebody else's rule. That
        is the staleness of a materialised export, except inside the
        authoritative source instead of a derivative. Only the pointer is
        stored, and that is what makes the gloss unable to rot."""
        def one(m):
            try:
                return f"({_norm_id(m.group(1))})"
            except RulesError:
                return m.group(0)
        return RE_CITE.sub(one, body or "")

    def _write_refs(self, p: str, rid: str, cites: list[str]) -> int:
        self.cx.execute("DELETE FROM rule_refs WHERE project=? AND src=?", (p, rid))
        for dst in cites:
            if dst == rid:
                continue
            self.cx.execute("INSERT OR IGNORE INTO rule_refs (project, src, dst) VALUES (?,?,?)",
                            (p, rid, dst))
        return len(cites)

    def _expand(self, p: str, body: str) -> str:
        """Expand every citation with the CURRENT title of the rule it points at.

        The gloss is NOT written, it is GENERATED — so it cannot go stale, which
        is the same defect as a materialised export but inside the authoritative
        source instead of a derivative. And the expansion knows the STATE of the
        rule it points at, so a citation towards a retired one arrives already
        marked as such, in the text, while the chat is reading.

        It never raises: what is in the database has already passed the door,
        and a reading path that can fail is a reading path that will."""
        now = _now()

        def one(m):
            try:
                rid = _norm_id(m.group(1))
            except RulesError:
                return m.group(0)
            row = self._row(p, rid)
            if row is None:
                return f"({rid}{GLOSS_SEP}⚠ never defined)"
            mark = ""
            if row["status"] != "active":
                mark = f" · {row['status']}"
            elif (row["permanence"] != "permanent" and row["expires_at"]
                  and row["expires_at"] <= now):
                mark = " · expired"
            # The title gives up its own round brackets before it goes inside
            # a round bracket. Without this a title holding a ')' closes the
            # citation early, and pasting the body back into rules_fix is
            # refused for text the registry itself generated — blaming the
            # author for the writer's mistake.
            return f"({rid}{GLOSS_SEP}{self._gloss(row)}{mark})"

        return RE_CITE.sub(one, body or "")

    @staticmethod
    def _gloss(row) -> str:
        """The title as it appears inside a citation. It gives up its own round
        brackets first: a title holding a ')' would close the citation early, and
        pasting the body back into rules_fix would then be refused for text the
        registry itself generated — blaming the author for the writer's
        mistake."""
        return (row["title"] or "").replace("(", "[").replace(")", "]")

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

    def _check_domain(self, p: str, domain: str) -> str:
        d = (domain or "").strip().upper()
        if not d:
            raise RulesError("the rule needs a DOMAIN: the number is not yours to pick, "
                             "the registry assigns it")
        if d not in self._domains(p):
            raise RulesError(f"domain {d!r} is not declared by this project "
                             f"(declared: {', '.join(self._domains(p)) or 'none'})")
        return d

    def _next_seq(self, p: str, dom: str) -> int:
        """The next number in that domain, counting from the LAST ever used —
        retired and denied rows included, because an ID is never reused."""
        last = self.cx.execute("SELECT IFNULL(MAX(seq), 0) FROM rules "
                               "WHERE project=? AND domain=?", (p, dom)).fetchone()[0]
        n = int(last) + 1
        if n > MAX_SEQ:
            raise RulesError(f"domain {dom} has burned all {MAX_SEQ} numbers: it needs a "
                             "new domain, because IDs are never reused")
        return n

    def propose(self, code: str, domain: str, rtype: str, title: str, body: str,
                scopes, reason: str, proposed_by: str = "",
                changelog: str = "", source: str = "", legacy_id: str = "") -> dict:
        """File a proposal. It reaches NOBODY until the batch is approved — which
        is why this needs only the project code: an unapproved proposal cannot
        do harm, and a chat that deposits one stops keeping a note about it.

        THE NUMBER IS NOT A PARAMETER. You give the DOMAIN and the registry
        assigns the next number in it, four digits. A number is not a choice, it
        is a position in a sequence: whoever does not pass it cannot pick it,
        which is a structural guarantee and not a rule anybody has to remember.

        `legacy_id` is the old markdown identifier, optional. Since the number is
        no longer yours, recording the old one alongside the new one builds the
        mapping WHILE the work happens, instead of reconstructing it afterwards:
        the second pass over the citations becomes a substitution, not an
        investigation.

        The ID assigned comes back in the verdict — without it you could not
        write the citations that point at this rule."""
        p = self._project(code)
        dom = self._check_domain(p, domain)
        rtype = (rtype or "").strip().upper()
        if rtype not in TYPES:
            raise RulesError(f"type {rtype!r}: R binding, M method, F technical fact. "
                             "Retirement is a STATE, not a type.")
        if not (title or "").strip():
            raise RulesError("the rule needs a title")
        if not (body or "").strip():
            raise RulesError("the rule needs a body")
        if not (reason or "").strip():
            raise RulesError("reason is mandatory: without the why a rule cannot be "
                             "defended, and at the first opportunity it gets reopened")
        legacy = _norm_legacy(legacy_id)
        if legacy:
            twin = self.cx.execute("SELECT id FROM rules WHERE project=? AND legacy_id=?",
                                   (p, legacy)).fetchone()
            if twin:
                raise RulesError(
                    f"{legacy} was already filed, as {twin['id']}. Two rules cannot claim "
                    "the same old identifier: in a long pass entering one twice is likelier "
                    "than forgetting one, and this is what notices.")
        # Citations are validated BEFORE anything is written: a chat cannot
        # hallucinate a pointer, because the proposal does not go in.
        # VALIDATED ON WHAT ARRIVED, then compacted, then measured. The order is
        # the whole safety of it: compacting first would drop a gloss before the
        # bare-ID check could look at it, so a body could lose a pointer and a
        # sentence without anybody being told. Measured last because the ceiling
        # has to be about what actually goes into the database.
        cites = self._cites(p, body)
        body = self._compact(body)
        if len(body.encode()) > MAX_BODY_BYTES:
            raise RulesError(f"body over {MAX_BODY_BYTES} bytes once stored: split the rule")
        scopes = self._check_scopes(p, _norm_scope_list(scopes))
        by = _norm_name(proposed_by, "consumer") if proposed_by else None
        if by:
            self._consumer(p, by)
        # IMMEDIATE, not the default deferred: the write lock is taken BEFORE
        # the counter is read, so nobody can read the same MAX(seq) in between.
        # A plain BEGIN would upgrade from read to write halfway through, and in
        # WAL that upgrade cannot wait — the loser dies with "database is
        # locked" no matter how long the busy timeout is.
        self.cx.execute("BEGIN IMMEDIATE")
        try:
            # The number is read and taken in one go, and
            # UNIQUE(project, domain, seq) is the net underneath.
            seq = self._next_seq(p, dom)
            rid = f"{dom}-{seq:0{ID_DIGITS}d}"
            for s in scopes:
                self.cx.execute("INSERT INTO rule_scopes (project, rule_id, scope) VALUES (?,?,?)",
                                (p, rid, s))
            self.cx.execute(
                "INSERT INTO rules (project, id, domain, seq, type, title, body, status, "
                "permanence, changelog, source, reason, proposed_by, legacy_id, updated_at) "
                "VALUES (?,?,?,?,?,?,?,'proposed','provisional',?,?,?,?,?,?)",
                (p, rid, dom, seq, rtype, title.strip(), body, changelog or None,
                 source or None, reason.strip(), by, legacy or None, _now()))
            self._write_refs(p, rid, cites)
            self.cx.execute("COMMIT")
        except sqlite3.IntegrityError as e:
            self.cx.execute("ROLLBACK")
            # ONE integrity error here is a refusal, and it is the race on the
            # counter: two writers took the same number, nothing was written,
            # filing it again works. Everything else that the integrity layer
            # can raise — a foreign key, a NOT NULL, a CHECK — means the schema
            # and the code disagree, which no caller can fix and which will
            # fail again for ever. Telling THEM to retry is the worst answer
            # available. The discrimination is on the constraint, not on prose:
            # this branch used to say "if it names the unique constraint …" and
            # classify unconditionally, which is a comment doing a condition's
            # job.
            if "rules.project, rules.domain, rules.seq" not in str(e).replace(
                    "UNIQUE constraint failed: ", ""):
                raise RulesFault(
                    f"the database refused the proposal for a reason that is not the "
                    f"counter race: {e}. Schema and code disagree — retrying will not "
                    f"help.")
            raise RulesError(
                f"the proposal for domain {dom} was refused by the database: {e}. Two "
                "writers took the same number — nothing was written, so filing it again "
                "is safe.")
        except Exception:
            self.cx.execute("ROLLBACK")
            raise
        out = {"project": p, "id": rid, "domain": dom, "seq": seq,
               "status": "proposed", "scopes": scopes, "cites": cites,
               "reaches_now": [],
               "note": "the ID above was ASSIGNED by the registry: write it down, it is what "
                       "other rules must cite. It reaches nobody until the batch is "
                       "approved — check back with pending instead of keeping a note."}
        if legacy:
            out["legacy_id"] = legacy
        return out

    def batch(self, code: str) -> dict:
        """The pending batch plus its DIGEST — sha256 over the ordered list of
        IDs and bodies. You sign the batch, not the single rule: that is what
        makes the signature worth reading, and it is where three proposals that
        say the same thing become visible next to each other.

        Each proposal carries its `reason`: the why being let in is on the
        table where the decision happens, not a history call away."""
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
                "proposals": [self._dict(r, p, why=True) for r in rows],
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
                "event=?, updated_at=? WHERE project=? AND id=?",
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
            self.cx.execute("UPDATE rules SET status='denied', denied_reason=?, event=?, "
                            "updated_at=? WHERE project=? AND id=?",
                            (reason.strip(), "denied", _now(), p, rid))
            out.append(rid)
        return {"project": p, "denied": out, "reason": reason.strip(),
                "note": "the row stays and the ID is burnt. It no longer BLOCKS the same "
                        "idea coming back — with the counter a re-proposal takes a new "
                        "number — but rules_pending shows the refusal and its reason to "
                        "whoever filed it"}

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
            self.cx.execute("UPDATE rules SET expires_at=?, event=?, updated_at=? "
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
                            "event=?, updated_at=? WHERE project=? AND id=?",
                            ("promoted to permanent", _now(), p, rid))
        return {"project": p, "promoted": ids, "signed": signed, "digest": message}

    def amend(self, code: str, rid: str, expected_version: int, reason: str,
              title: str = None, body: str = None, rtype: str = None,
              changelog: str = None) -> dict:
        """Fix a DEFECT in place: a wrong number, a broken pointer, a sentence
        that says something false. Same ID, the rule stays in force.
        A superseded DECISION is not fixed this way: propose the new one and
        retire the old pointing at it.

        A new body goes through the SAME citation check as a proposal: this is
        the door the second seeding pass uses, so it cannot be the door that
        lets an unresolved pointer in. The body you read back is expanded — you
        can paste it here as it came, the gloss is dropped.

        The `reason` asked for here is the why of the FIX: it lands in the
        event column and in the history. The rule's own `reason` — the why it
        exists — is never rewritten by any event."""
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
        if body is None:
            # NO NEW BODY, so nothing is re-validated. Otherwise a rule written
            # before the citation format existed could never be renamed,
            # retyped or given a changelog again: the check would refuse a
            # sentence nobody touched today, and rules_fix is exactly the tool
            # the conversion pass needs. What is already in the database gets
            # audited by rules_check, which is the right place — a report, not a
            # door slammed on unrelated work.
            new_body, cites = row["body"], None
        else:
            # A body that ARRIVED is always checked, before it is compacted.
            cites = self._cites(p, body, self_id=rid)
            new_body = self._compact(body)
            if len(new_body.encode()) > MAX_BODY_BYTES:
                raise RulesError(f"body over {MAX_BODY_BYTES} bytes once stored: split the rule")
            if new_body == row["body"]:
                # Pasting back what you read is not an edit.
                cites = None
        self.cx.execute(
            "UPDATE rules SET type=?, title=?, body=?, changelog=?, event=?, updated_at=? "
            "WHERE project=? AND id=?",
            (row["type"] if rtype is None else rtype.strip().upper(),
             row["title"] if title is None else title.strip(),
             new_body,
             row["changelog"] if changelog is None else changelog,
             reason.strip(), _now(), p, rid))
        if cites is not None:
            self._write_refs(p, rid, cites)
        return {"project": p, "id": rid, "version": self._version(p, rid),
                "amended": True, "cites": cites if cites is not None else "unchanged"}

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
            target = self._row(p, sb)
            if target is None:
                raise RulesError(f"{sb} does not exist: create the new rule first")
            if sb == rid:
                raise RulesError(f"{rid} cannot supersede itself")
            # The same rule as a citation, and for the same reason: the number
            # of a proposal is not final until it is in, and a successor that is
            # never approved leaves the retired rule pointing at nothing for
            # good. superseded_by is not written to rule_refs, so no audit would
            # ever come back to it — the check has to be here or nowhere.
            if target["status"] in ("proposed", "denied"):
                raise RulesError(
                    f"{sb} has not been approved yet, so it cannot supersede anything. "
                    "Have the successor approved first, then retire the rule it replaces.")
        self.cx.execute("UPDATE rules SET status='retired', superseded_by=?, changelog=?, "
                        "event=?, updated_at=? WHERE project=? AND id=?",
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
        approval lock needs no global off switch.

        It is the ONE path that still takes an explicit ID, and the one that does
        not validate citations: its bodies come from another world, where the
        acronyms are bare and point at a numbering that is about to change.
        Recording what is there without judging it is the honest behaviour for a
        door that is scheduled to be removed — but it does have to RECORD it, so
        the bare IDs are read as references and the ID given becomes the
        rule's legacy_id. Otherwise the audit that runs in its wake reports a
        clean project that is not, which is worse than not auditing."""
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
                # Checked HERE, before the transaction: rule_scopes is written
                # first (the deferred FK), so without this a duplicate is
                # rejected by the perimeter's primary key and the message talks
                # about a table the caller never mentioned.
                if self._row(p, rid):
                    raise RulesError(f"{rid} is already in this batch: two entries "
                                     "normalise onto the same ID")
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
                    "permanence, expires_at, changelog, source, reason, legacy_id, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,'active',?,?,?,?,?,?,?)",
                    (p, rid, dom, seq, rtype, (item.get("title") or rid).strip(), body,
                     "permanent" if permanent else "provisional",
                     None if permanent else _plus_days(self.provisional_days),
                     item.get("changelog"), item.get("source"), reason.strip(),
                     _norm_legacy(item.get("id", "")) or None, _now()))
                self._write_refs(p, rid, self._legacy_cites(body))
                self.cx.execute("COMMIT")
                taken.append(rid)
            except RulesFault:
                # A fault is not a rejected item. Swallowing it here would put
                # a full disk, a locked database or a half-written page into
                # the `rejected` list, return 200, and let the audit below
                # declare the project clean — which is the defect RulesFault
                # exists to close, on the door that writes the most. The ORDER
                # matters for the same reason it matters in server.py: it
                # subclasses RulesError.
                try:
                    self.cx.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            except RulesError as e:
                # A rejected ITEM: this one is malformed, the others go on.
                try:
                    self.cx.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                rejected.append({"id": item.get("id"), "why": str(e)})
            except sqlite3.IntegrityError as e:
                # The caller's data collided with a constraint — same shape as
                # a malformed item, and it must not stop the rest.
                try:
                    self.cx.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                rejected.append({"id": item.get("id"), "why": str(e)})
            except Exception:
                # Anything else is the machine: roll back so the connection is
                # left usable, then let it RISE. It keeps its traceback, the
                # import stops, and nobody is told that a half-written database
                # imported cleanly.
                try:
                    self.cx.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        audit = self.check(code)
        return {"project": p, "imported": len(taken), "ids": taken,
                "rejected": rejected, "audit": audit,
                "note": "the broken pointers listed by the audit were already in the "
                        "Markdown files: they were just not visible."}

    # ---------- derivatives ----------

    def export(self, code: str, consumer: str = "", expand: bool = False) -> dict:
        """A Markdown snapshot. It is a DERIVATIVE: the truth stays in the
        database and this regenerates. With a consumer it is the block to paste
        into that chat's memory; without one, the whole project.

        This is the ONLY reader that gets a choice about the citations, because
        it is read by a person: compact by default, `expand` to have every
        pointer carry the current title of what it points at.

        Every rule carries its `reason`, and the whole-project export the last
        `event` too: this is a MAINTENANCE reading, and the why is what a
        person maintaining the corpus decides on."""
        p = self._project(code)
        lines = [f"# {p} — rules", "",
                 f"> Generated {_now()} by codifier-mcp {VERSION}. This file is a "
                 f"DERIVATIVE: the truth is the registry, and this regenerates.", ""]
        if consumer:
            c = self._consumer(p, consumer)
            data = self._rules_for(p, c, expand)
            lines[0] = f"# {p} — rules for {c}"
            lines += [f"{len(data['rules'])} rules in force, widest first. "
                      f"{data['outside']} are outside your perimeter.", ""]
            groups: dict[int, list] = {}
            for r in data["rules"]:
                groups.setdefault(r["breadth"], []).append(r)
            for breadth in sorted(groups, reverse=True):
                block = groups[breadth]
                via = sorted({v for r in block for v in r["via"]})
                lines += [f"## Reaching {breadth} consumer(s) — via {', '.join(via)}", ""]
                for r in block:
                    lines += [f"### {r['id']} · {r['title']}  `{r['type']}`", "",
                              f"*why: {r['reason']}*", "", r["body"], ""]
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
                              + (f" · was {r['legacy_id']}" if r["legacy_id"] else "")
                              + "*", "",
                              f"*why: {r['reason']}*"
                              + (f" — *last event: {r['event']}*" if r["event"] else ""),
                              "",
                              self._expand(p, r["body"]) if expand else r["body"], ""]
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
