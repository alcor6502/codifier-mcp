"""
rules.py — a rules registry on SQLite. ONE database PER PROJECT.

The model
---------
- A project is a FILE: `/db/<Name>/<slug>.db`, and there is no `project_id`
  anywhere in the schema. Spillover between projects is not forbidden, it is
  impossible. Which files are served is declared in `/db/projects.txt`, a text
  file edited from Unraid — the registry stopped being a table in v4.0.0,
  along with the tools that could create and rekey a project. What is
  catastrophic has no tool.
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
- a new rule reaches nobody until it is approved. Approval covers a BATCH and
  demands the batch's DIGEST back: you approve the batch you READ, and a
  proposal arriving in between moves the digest and voids the approval. The
  ed25519 signature that used to ride on top left in v2.0.0 — it was the
  clumsy way of letting a person in instead of a chat, and the admin UI solves
  that at the root. The digest was never the signature's: it stays;
- an approved rule is PROVISIONAL and expires. Staying costs a decision, going
  is free — which is the asymmetry that stops rules from piling up.

The database is owned by root and its files are 0644: whoever mounts the share
READS it and does not touch it. Writing by hand would bypass the triggers and
break history in silence.
"""
from __future__ import annotations

import difflib
import functools
import hashlib
import logging
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

# A CHILD of the service's logger, so it inherits LOG_LEVEL and is caught by
# the ring buffer the administration page reads. The engine logs exactly two
# kinds of thing — a database created empty, and a schema object rebuilt —
# and both are alarms about the world outside this process.
log = logging.getLogger("codifier-mcp.registry")

VERSION = "4.0.0"

# The GENERATION of the schema, and it is a number the database carries in
# `PRAGMA user_version`. It exists because of a thing that was seen live at
# the v3.1.0 Apply: a database that does not know what generation it is makes
# ordinary growth — the new tables of a new release — indistinguishable from
# REPAIR, and the preflight duly reported "somebody had removed these
# objects" about tables that had simply never existed. With this number the
# router knows before it opens: match and it connects, mismatch and it
# refuses naming the file. There is NO migration in 4.0.0 — an old database
# is refused, not upgraded.
SCHEMA_GENERATION = 4

TYPES = ("R", "M", "F")                 # R binding · M method · F technical fact
KINDS = ("chat", "skill", "human")      # a human calls no tool, but owns tasks
STATUSES = ("proposed", "active", "retired", "denied")
PERMANENCE = ("provisional", "permanent")
REACH = ("all", "targeted")             # the audience is MIXED: groups ∪ exceptions
VERDICTS = ("approved", "denied")
TASK_STATUSES = ("pending", "completed", "dropped")

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
# ANYTHING SHAPED LIKE AN ID, whatever the digit count — one, two, three,
# five. RE_BARE starts at two because that is what the old two-digit era could
# legitimately write; this one starts at ONE because the sanitisation is not
# looking for valid IDs, it is looking for everything that is NOT the canonical
# form. `VE-5` and `VE-12345` used to walk straight through: the first was
# below RE_BARE's floor, the second above what RE_CITE will match, so neither
# was ever seen. There is exactly one accepted way to point at a rule —
# `(XX-NNNN)`, four digits, inside round brackets — and every other digit count
# is an identifier of the old Markdown corpus by construction.
RE_ID_SHAPED = re.compile(r"\b([A-Za-z]{2})-(\d+)\b")
# Reading expands a citation with the current title; this lets that expanded
# form come back in through rules_fix without the gloss having to be stripped
# by hand. Only the pointer is ever stored, so the gloss cannot go stale.
RE_CITE_GLOSSED = re.compile(
    r"^([A-Za-z]{2}-\d{2,4}(?:-[RMFrmf])?)\s*(?:—|--|·|\|).*$", re.S)
# The separator the gloss is generated with. It is written once, here, because
# the parser has to be able to take back exactly what the writer put out.
GLOSS_SEP = " — "

RE_CODE = re.compile(r"^[A-Za-z0-9]{8,32}$")
# Spelling is DATA: a name is stored exactly as it was first given and comes
# back the same, byte for byte. What is unique is the CASEFOLDED form — see
# ux_consumers_fold / ux_scopes_fold — so `Architect` and `architect` are one
# identity with one spelling, never two rows. The old pattern forced
# lowercase, which silently rewrote what the owner typed: that rewriting is
# extinct, not configurable.
RE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,40}$")
# What a slug may hold, one character at a time — the pattern is per character
# and not per string so a refusal can name WHICH characters were the trouble.
RE_SLUG_CHAR = re.compile(r"[a-z0-9-]")

FILE_MODE = 0o644                       # root writes, everyone else reads
DIR_MODE = 0o755
DEFAULT_PROVISIONAL_DAYS = 90
# The owner reads and decides in batches of 3-4: that rhythm is the
# deliberate bottleneck of the whole approval flow, and this is the number
# that imposes it. It used to be a rule (the dead AM domain) and died at gate
# two, rightly: a machine-checkable constraint lives in the tool, not in the
# corpus. Deliberately generous; a deployment can lower it.
DEFAULT_PENDING_CAP = 5
MAX_BODY_BYTES = 64_000
MAX_GET_IDS = 50

# ---------------------------------------------------------------------
# The task log
# ---------------------------------------------------------------------
# `TK` is the task log's own prefix, and it is RESERVED: a project that
# declared it as a domain of rules would mint rule IDs indistinguishable from
# task IDs, and a citation would stop meaning one thing. Refused where a
# domain is DECLARED, and again where one is used — the second is not
# belt-and-braces for its own sake: a row put there by hand with sqlite3 never
# passed the first door, and the guarantee has to hold whichever door the
# write came through.
TASK_PREFIX = "TK"
RESERVED_DOMAINS = (TASK_PREFIX,)

# The ceilings of the task log, NAMED, because the spec asks for names and
# because a literal repeated in four queries is a number written four times.
#
# TASKS_LIST_CAP is the ceiling of every LIST (list, search, range). Fifty is
# generous on purpose: the point of the cap is that a runaway answer cannot
# eat a chat's context, not that it disciplines anybody.
TASKS_LIST_CAP = 50
# TASKS_GET_IDS is the batch of bodies. Ten, and the arithmetic behind it is
# the one the spec asked to be done in delivery rather than estimated: a body
# is capped at MAX_BODY_BYTES, so ten of them is 640,000 characters — far over
# any client's result ceiling. The count alone therefore does NOT bound the
# answer, and the real limit is the byte one below.
TASKS_GET_IDS = 10
# The byte ceiling of that same batch, and the number that actually bounds it.
# 60,000 leaves a single full-size body whole — the case where truncating
# would be useless, since there is nothing smaller to fall back to — and stops
# the batch at the first body that would cross it, declaring the truncation.
TASKS_GET_BYTES = 60_000
# How far back a closed task keeps showing up in `tasks_list`. Past it the
# task is not gone, it is asked for by date with tasks_range.
TASKS_RECENT_DAYS = 30
# When a task still open starts coming back MARKED. It is a label on a
# reading, never a lifecycle: a task does not expire, because an automatic
# expiry would be a `dropped` with no reason, written by the clock. Thirty
# days chosen with Alfredo on 2026-08-11: a month open is stopped for real,
# and on most rounds nobody carries the mark — which is what makes it worth
# seeing when somebody does.
TASKS_STALE_DAYS = 30

# Identical answer for a missing code and a wrong one: a message that told them
# apart would be an oracle.
ERR_PROJECT = ("project not specified: this needs the project CODE, the one at the top "
               "of its instructions. Without it the registry does not answer — and there "
               "is no way to list projects: either you have it, or you ask for it.")

# One message for every way maintenance can fail to open — wrong code, wrong
# key, missing either. Which half was wrong is not said, on purpose: telling
# them apart would confirm a valid code to whoever holds only that.
ERR_MAINT = ("maintenance refused: the project code or the architect key is missing "
             "or wrong — and which one is not said, on purpose. Maintenance is done "
             "by the chat that maintains this project, with the key Alfredo gives "
             "it. Do not guess it: ask.")

# What the registry GENERATES. The code is the door and lives at the top of
# the project instructions; the architect key is the maintenance privilege
# and lives in a password manager. Both are generated HERE — `rekey` included
# — because a credential a person invents is the only kind that ends up weak,
# and one generator means one format. The alphabet drops the four lookalike
# characters (I/l, O/0): these get read aloud and retyped once, at the
# receipt.
CODE_LEN = 16
KEY_LEN = 24
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"


def _gen(n: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def _key_hash(key: str) -> str:
    """sha256 and nothing slower, deliberately: the key is generated at high
    entropy by _gen, never chosen by a person, so a KDF would buy nothing
    against the only attack that exists — reading the file and brute-forcing
    a 24-character random string, which sha256 already makes absurd."""
    return hashlib.sha256((key or "").strip().encode()).hexdigest()


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
    engine almost everything is the caller: of the eighty-odd refusals, the one
    fault left is the schema-and-code disagreement behind a proposal the
    database refused for a reason that is not the counter race — which no
    caller can fix and which will fail again for ever. One neighbour that
    deliberately stays an ordinary refusal, because the reasoning is not
    obvious: the UNIQUE collision on (project, domain, seq) when two writers
    take the same number. It comes from the database, but nothing was written
    and the message says filing it again is safe — the twin decided the same
    for its CAS conflicts, which are the same shape.

    Anything sqlite raises on its own — a locked file, a half-written page, a
    full disk — never becomes a RulesError at all: this engine lets it rise
    untouched, so it already keeps its traceback."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _plus_days(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day_bound(s: str, what: str, end: bool) -> str:
    """A date the way a person writes one — `2026-07-01` — turned into the
    instant that makes the comparison mean what they said. A bare date used as
    an upper bound would silently exclude everything that happened that day,
    which is the class of off-by-one nobody notices until a month is missing
    from a changelog. A full stamp is taken as given."""
    t = (s or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", t):
        return t + ("T23:59:59Z" if end else "T00:00:00Z")
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", t):
        return t
    raise RulesError(f"{what} {s!r}: a date, YYYY-MM-DD (or a whole stamp, "
                     "YYYY-MM-DDTHH:MM:SSZ)")


def _day_start(s: str, what: str) -> str:
    return _day_bound(s, what, end=False)


def _day_end(s: str, what: str) -> str:
    return _day_bound(s, what, end=True)


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


def _valid_name(name: str, what: str) -> str:
    """Validate a name and hand it back UNTOUCHED. There is no normalisation
    here on purpose: the spelling is the author's, and identity is decided by
    the casefolded unique index, not by rewriting the input."""
    s = (name or "").strip()
    if not RE_NAME.match(s):
        raise RulesError(
            f"invalid {what} name {name!r}: letters, digits, space, '-' and '_', "
            "max 41 characters, and it cannot start with a separator")
    return s


def _fold(name: str) -> str:
    """The comparison form of a name. ONE definition, because two ideas of
    what makes names equal is how `Architect` and `architect` become two
    consumers. It matches SQLite's lower() — ASCII — which is what the unique
    indexes use: the two sides of the boundary must agree."""
    return (name or "").strip().lower()


def _slug(name: str) -> str:
    """The file name of a project, DERIVED from its name: lowercase, runs of
    whitespace to a single dash, and nothing else allowed.

    Derived and never stored, because the path is a RULE and not a datum:
    folder = the name as it is spelled, file = the slug, always. Two places
    that both claim to know where a project lives is how a rename half
    happens.

    Anything the slug cannot carry is refused NAMING the characters. It would
    be easy to drop them quietly — and then two projects a month apart would
    land on the same file and nobody would have been told."""
    s = re.sub(r"\s+", "-", (name or "").strip().lower())
    if not s:
        raise RulesError("a project needs a name: the folder under /db is the name, "
                         "and the file inside it is that name in lowercase with "
                         "dashes for spaces")
    bad = "".join(sorted({c for c in s if not RE_SLUG_CHAR.match(c)}))
    if bad:
        raise RulesError(
            f"{name!r} cannot become a file name because of {bad!r}: a project name "
            "carries letters, digits and spaces, and nothing else — the file is that "
            "name in lowercase with dashes for spaces")
    return s


def _valid_domain(d: str) -> str:
    """The ONE door a domain letter-pair goes through, wherever it is declared.
    It used to be a regex written twice — once in create_project, once in
    add_domains — and a reservation added to one copy is a reservation that
    holds on one door."""
    d = (d or "").strip()
    if not re.match(r"^[A-Z]{2}$", d):
        raise RulesError(f"domain {d!r}: exactly two uppercase letters")
    if d in RESERVED_DOMAINS:
        raise RulesError(
            f"domain {d!r} is RESERVED: it is the prefix of the task log, and a rule "
            f"numbered {d}-0001 could not be told apart from a task. Pick another pair.")
    return d


def _norm_scope_list(scopes) -> list[str]:
    if isinstance(scopes, str):
        scopes = [scopes]
    out: list[str] = []
    for s in scopes or []:
        t = (s or "").strip()
        if t.lower() in ALL_ALIASES:
            t = ALL
        elif t != ALL:
            t = _valid_name(t, "scope")
        if _fold(t) not in [_fold(x) for x in out]:
            out.append(t)
    return out


# =====================================================================
# Schema
# =====================================================================

# The perimeter of a rule, in two shapes, both written into history.
#   scopes    what was DECLARED  (e.g. 'deliberativi')
#   consumers who was REACHED    (resolved and expanded)
# A version is a photograph: if only the scope name were stored, changing the
# membership of that scope tomorrow would rewrite what was true yesterday.
def _f(sql: str, row: str) -> str:
    """Bind a SQL fragment to the row alias it must read (NEW, OLD, r…)."""
    return sql.replace("{R}", row)


# The next version number of an entity, computed rather than counted anywhere:
# a stored counter is a second copy of a fact, and the version tables never
# lose a row, so MAX+1 can be trusted here in a way it could not be trusted
# for task IDs (see `seq` on `task`).
_NEXT_RULE_V = ("(SELECT IFNULL(MAX(version), 0) + 1 FROM rule_version "
                "WHERE rule_id = {R}.rule_id)")
_NEXT_CONSUMER_V = ("(SELECT IFNULL(MAX(version), 0) + 1 FROM consumer_version "
                    "WHERE consumer_id = {R}.consumer_id)")
_NEXT_DOMAIN_V = ("(SELECT IFNULL(MAX(version), 0) + 1 FROM domain_version "
                  "WHERE domain_id = {R}.domain_id)")
_NEXT_GROUP_V = ("(SELECT IFNULL(MAX(version), 0) + 1 FROM consumer_group_version "
                 "WHERE group_id = {R}.group_id)")
_NEXT_TASK_V = ("(SELECT IFNULL(MAX(version), 0) + 1 FROM task_version "
                "WHERE task_id = {R}.task_id)")
_NEXT_PROFILE_V = "(SELECT IFNULL(MAX(version), 0) + 1 FROM project_profile_version)"

_NOW = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"


SCHEMA = f"""
-- =====================================================================
-- The project IS the file
-- =====================================================================
-- There is no `project_id` anywhere below, and no `projects` table: one
-- project is one SQLite file under /db/<Name>/<slug>.db, and the registry
-- that says which files are served is `projects.txt`. Spillover between
-- projects is not forbidden here — it is impossible.

-- ---------------------------------------------------------------------
-- The profile: one row, and it is the project talking about itself
-- ---------------------------------------------------------------------
-- `brief` is identity — owner, style, doctrine — and changes rarely, behind
-- the admin code. `specs` are the living facts (what used to be called the
-- Perimeter): true today, false tomorrow without anyone having DECIDED
-- anything, so they change on the reference code. They are two columns and
-- not one because they are two gates.
CREATE TABLE IF NOT EXISTS project_profile (
  profile_id INTEGER PRIMARY KEY CHECK (profile_id = 1),
  brief      TEXT,
  specs      TEXT,
  queue_cap  INTEGER,        -- NULL = unlimited, 0 = queue closed, N = N
  updated_at TEXT NOT NULL,
  actor      TEXT            -- who wrote last; the version trigger reads it
);

CREATE TABLE IF NOT EXISTS project_profile_version (
  version    INTEGER PRIMARY KEY,
  brief      TEXT,
  specs      TEXT,
  queue_cap  INTEGER,
  timestamp  TEXT NOT NULL,
  action     TEXT NOT NULL,
  actor      TEXT
);

-- ---------------------------------------------------------------------
-- Domains
-- ---------------------------------------------------------------------
-- `code` is printed inside every display ID this registry ever hands out, so
-- it is written ONCE (a trigger says so): renaming it would relabel history.
-- `description` is a gloss and is amendable. `reason` is why the domain
-- exists at all, and it is required — a domain nobody can justify is a
-- drawer, and drawers fill up.
CREATE TABLE IF NOT EXISTS domain (
  domain_id      INTEGER PRIMARY KEY,
  code           TEXT NOT NULL CHECK (upper(code) <> 'TK'),
  description    TEXT,
  reason         TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  retired_at     TEXT,
  retired_reason TEXT,
  actor          TEXT,
  CHECK (retired_at IS NULL OR TRIM(IFNULL(retired_reason,'')) <> '')
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_domain_fold ON domain(lower(code));

CREATE TABLE IF NOT EXISTS domain_version (
  domain_id      INTEGER NOT NULL REFERENCES domain(domain_id),
  version        INTEGER NOT NULL,
  code           TEXT,
  description    TEXT,
  retired_at     TEXT,
  retired_reason TEXT,
  timestamp      TEXT NOT NULL,
  action         TEXT NOT NULL,
  actor          TEXT,
  PRIMARY KEY (domain_id, version)
);

-- ---------------------------------------------------------------------
-- Consumers: whoever is under the rules
-- ---------------------------------------------------------------------
-- Chats, skills and — since rev. 5 — HUMANS. A human calls no tool, but is a
-- valid owner of a task: the owner's mail is read from the overview and from
-- the web UI, and that is exactly why opening a task for a human does not
-- notify anybody.
--
-- `brief` is the mandate — the boundaries — and moves on the admin code, so a
-- chat holding only the reference code cannot rewrite its own remit. `specs`
-- are operational data and move on the reference code. `secret` is NULL
-- until somebody decides that this consumer's gestures must be signed: set
-- it, and every gesture in its name carries `consumer_key`. It is switched on
-- one consumer at a time, without rewiring anything.
CREATE TABLE IF NOT EXISTS consumer (
  consumer_id    INTEGER PRIMARY KEY,
  name           TEXT NOT NULL,   -- spelling is DATA; identity is the surrogate
  kind           TEXT NOT NULL CHECK (kind IN ('chat','skill','human')),
  brief          TEXT,
  specs          TEXT,
  secret         TEXT,
  created_at     TEXT NOT NULL,
  retired_at     TEXT,
  retired_reason TEXT,
  actor          TEXT,
  CHECK (retired_at IS NULL OR TRIM(IFNULL(retired_reason,'')) <> '')
);

-- Identity is the casefolded name: `Architect` and `architect` are one
-- consumer, and the spelling stays the author's.
CREATE UNIQUE INDEX IF NOT EXISTS ux_consumer_fold ON consumer(lower(name));

CREATE TABLE IF NOT EXISTS consumer_version (
  consumer_id INTEGER NOT NULL REFERENCES consumer(consumer_id),
  version     INTEGER NOT NULL,
  name        TEXT,              -- what it was called THAT DAY
  kind        TEXT,
  brief       TEXT,
  specs       TEXT,
  retired_at  TEXT,
  timestamp   TEXT NOT NULL,
  action      TEXT NOT NULL,     -- created/amended/renamed/retired/revived
  actor       TEXT,
  PRIMARY KEY (consumer_id, version)
);

-- ---------------------------------------------------------------------
-- Groups: real ones only
-- ---------------------------------------------------------------------
-- The generated singletons, the `managed` column, the `_ALL_` row and their
-- four triggers are gone: `_ALL_` was routing dressed up as data, and a
-- singleton was a group invented so that the code would have something to
-- join against. What is left is what a person would call a group.
CREATE TABLE IF NOT EXISTS consumer_group (
  group_id       INTEGER PRIMARY KEY,
  name           TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  retired_at     TEXT,
  retired_reason TEXT,
  actor          TEXT,
  CHECK (retired_at IS NULL OR TRIM(IFNULL(retired_reason,'')) <> '')
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_group_fold ON consumer_group(lower(name));

CREATE TABLE IF NOT EXISTS consumer_group_version (
  group_id       INTEGER NOT NULL REFERENCES consumer_group(group_id),
  version        INTEGER NOT NULL,
  name           TEXT,
  retired_at     TEXT,
  retired_reason TEXT,
  timestamp      TEXT NOT NULL,
  action         TEXT NOT NULL,
  actor          TEXT,
  PRIMARY KEY (group_id, version)
);

CREATE TABLE IF NOT EXISTS consumer_group_member (
  group_id    INTEGER NOT NULL REFERENCES consumer_group(group_id),
  consumer_id INTEGER NOT NULL REFERENCES consumer(consumer_id),
  PRIMARY KEY (group_id, consumer_id)
);

CREATE INDEX IF NOT EXISTS ix_group_member ON consumer_group_member(consumer_id);

-- ---------------------------------------------------------------------
-- The corpus
-- ---------------------------------------------------------------------
-- 'VA-0001' is not stored anywhere: UNIQUE(domain_id, seq) is the truth and
-- the display ID is computed by the view. That is difficulty #2 of the nine,
-- and it is why a domain's code can be printed everywhere without ever being
-- copied.
CREATE TABLE IF NOT EXISTS rule (
  rule_id       INTEGER PRIMARY KEY,
  domain_id     INTEGER NOT NULL REFERENCES domain(domain_id),
  seq           INTEGER NOT NULL,
  type          TEXT NOT NULL CHECK (type IN ('R','M','F')),
  title         TEXT NOT NULL,
  body          TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'proposed'
                CHECK (status IN ('proposed','active','retired','denied')),
  permanence    TEXT NOT NULL DEFAULT 'provisional'
                CHECK (permanence IN ('provisional','permanent')),
  expires_at    TEXT,
  reach         TEXT NOT NULL CHECK (reach IN ('all','targeted')),
  supersedes_rule_id    INTEGER REFERENCES rule(rule_id),  -- on the PROPOSAL
  superseded_by_rule_id INTEGER REFERENCES rule(rule_id),  -- on the RETIRED one
  source        TEXT,
  reason        TEXT NOT NULL,   -- the why of the RULE, immutable
  event         TEXT,            -- the last event of the lifecycle
  proposed_by   TEXT,            -- the AUTHOR, on the rule, for good
  actor         TEXT,            -- the hand on the LAST gesture; 'web ui' from
                                 -- the batch page. Same column `task` has had
                                 -- since 3.1.0, and the version trigger reads
                                 -- it: without it `rule_version.actor` would
                                 -- be a column nothing could ever fill
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  UNIQUE (domain_id, seq)
);

-- Two pending proposals cannot claim the same victim: whoever approves would
-- be retiring one rule towards two heirs, and which one won would be batch
-- order. Partial on `proposed`, so approval and denial free the slot by
-- themselves.
CREATE UNIQUE INDEX IF NOT EXISTS ux_rule_supersedes ON rule(supersedes_rule_id)
    WHERE supersedes_rule_id IS NOT NULL AND status = 'proposed';

CREATE INDEX IF NOT EXISTS ix_rule_status ON rule(status);

-- The audience, MIXED (rev. 6). `reach='all'` means NO rows in either table;
-- `targeted` means at least one, and the audience is the UNION of the two.
-- Groups are the normal case; the singles are called EXCEPTIONS because they
-- stand next to the groups and can only ever ADD.
--
-- The two references to `rule` are DEFERRED on purpose, and it is the same
-- reason the 3.x perimeter was deferred: the engine writes the audience
-- BEFORE the rule, inside one transaction, so that the AFTER INSERT trigger
-- on `rule` already has a complete perimeter to photograph. Written the
-- obvious way round — rule first, audience after — version 1 of every
-- targeted rule photographs NOBODY, and nothing complains.
CREATE TABLE IF NOT EXISTS rule_audience_group (
  rule_id  INTEGER NOT NULL REFERENCES rule(rule_id)
      DEFERRABLE INITIALLY DEFERRED,
  group_id INTEGER NOT NULL REFERENCES consumer_group(group_id),
  PRIMARY KEY (rule_id, group_id)
);

CREATE TABLE IF NOT EXISTS rule_audience_exception (
  rule_id     INTEGER NOT NULL REFERENCES rule(rule_id)
      DEFERRABLE INITIALLY DEFERRED,
  consumer_id INTEGER NOT NULL REFERENCES consumer(consumer_id),
  PRIMARY KEY (rule_id, consumer_id)
);

CREATE INDEX IF NOT EXISTS ix_audience_group_g ON rule_audience_group(group_id);
CREATE INDEX IF NOT EXISTS ix_audience_exc_c ON rule_audience_exception(consumer_id);

-- Citation between rules, and it is a FOREIGN KEY. In 3.x this was text and
-- an audit went looking for pointers that pointed nowhere; now the database
-- refuses to write one. An audit that hunts for something the schema can
-- forbid is a guarantee living in the wrong place.
CREATE TABLE IF NOT EXISTS rule_ref (
  src_rule_id INTEGER NOT NULL REFERENCES rule(rule_id),
  dst_rule_id INTEGER NOT NULL REFERENCES rule(rule_id),
  PRIMARY KEY (src_rule_id, dst_rule_id)
);

CREATE INDEX IF NOT EXISTS ix_rule_ref_dst ON rule_ref(dst_rule_id);

-- ---------------------------------------------------------------------
-- The history: whole snapshots written, diffs computed on read
-- ---------------------------------------------------------------------
-- No deltas with NULLs — "unchanged" and "cleared" would become
-- indistinguishable, and they would become indistinguishable precisely on
-- `expires_at`. What a reader actually wants ("show me the history with the
-- date and only what changed") is a DISPLAY requirement, and it is met by
-- computing N against N-1 at read time.
CREATE TABLE IF NOT EXISTS rule_version (
  rule_id               INTEGER NOT NULL REFERENCES rule(rule_id),
  version               INTEGER NOT NULL,
  type                  TEXT,
  title                 TEXT,
  body                  TEXT,
  status                TEXT,
  permanence            TEXT,
  expires_at            TEXT,
  reach                 TEXT,
  superseded_by_rule_id INTEGER,
  timestamp             TEXT NOT NULL,
  action                TEXT NOT NULL,
  reason                TEXT,
  actor                 TEXT,
  PRIMARY KEY (rule_id, version)
);

-- The audience photograph, relational. It has no date of its own on purpose:
-- the date lives once, on the parent row.
CREATE TABLE IF NOT EXISTS rule_version_audience (
  rule_id      INTEGER NOT NULL,
  version      INTEGER NOT NULL,
  consumer_id  INTEGER NOT NULL REFERENCES consumer(consumer_id),
  via_group_id INTEGER REFERENCES consumer_group(group_id),
  PRIMARY KEY (rule_id, version, consumer_id),
  FOREIGN KEY (rule_id, version) REFERENCES rule_version(rule_id, version)
);

-- ---------------------------------------------------------------------
-- Decisions: one turn of the batch page
-- ---------------------------------------------------------------------
-- On that page approving and denying are the SAME gesture — ticked is
-- approved, unticked is denied, against the digest — so what gets recorded is
-- a DECISION with two verdicts, not an "approval" that forgets the noes.
-- Denying costs a sentence and the CHECK is the guarantee; approving does
-- not, because the yes is the tick and the rule's own `reason` is already
-- written.
CREATE TABLE IF NOT EXISTS decision (
  decision_id INTEGER PRIMARY KEY,
  digest      TEXT NOT NULL,      -- sha256 of what was LOOKED AT
  decided_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decision_rule (
  decision_id INTEGER NOT NULL REFERENCES decision(decision_id),
  rule_id     INTEGER NOT NULL REFERENCES rule(rule_id),
  verdict     TEXT NOT NULL CHECK (verdict IN ('approved','denied')),
  reason      TEXT,
  PRIMARY KEY (decision_id, rule_id),
  CHECK ((verdict = 'approved' AND reason IS NULL)
      OR (verdict = 'denied' AND TRIM(IFNULL(reason,'')) <> ''))
);

-- ---------------------------------------------------------------------
-- The task log: work, not law
-- ---------------------------------------------------------------------
-- `seq` is UNIQUE and never comes back, and it no longer needs a counter row
-- to protect it: the prune ARCHIVES (`archived_at`) instead of deleting, so
-- MAX(seq) reads every number ever handed out. The cure and the disease are
-- the same story — in 3.1.0 the prune deleted, MAX(seq) went backwards, and
-- TK-0004 came back after TK-0007.
CREATE TABLE IF NOT EXISTS task (
  task_id        INTEGER PRIMARY KEY,
  seq            INTEGER NOT NULL UNIQUE,
  title          TEXT NOT NULL,
  body           TEXT NOT NULL,
  consumer_id    INTEGER NOT NULL REFERENCES consumer(consumer_id),  -- the OWNER
  created_by     TEXT NOT NULL,   -- a SIGNATURE, not a pointer: 'Alfredo' is valid
  urgent         INTEGER NOT NULL DEFAULT 0 CHECK (urgent IN (0,1)),
  status         TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','completed','dropped')),
  outcome        TEXT,
  reason_dropped TEXT,
  actor          TEXT,
  idem_key       TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  closed_at      TEXT,
  archived_at    TEXT,
  -- The three states and what each one COSTS, in the schema and not only at
  -- the door: this file is readable from the share.
  CHECK ((status = 'pending'
            AND outcome IS NULL AND reason_dropped IS NULL AND closed_at IS NULL)
      OR (status = 'completed'
            AND TRIM(IFNULL(outcome,'')) <> '' AND closed_at IS NOT NULL)
      OR (status = 'dropped'
            AND TRIM(IFNULL(reason_dropped,'')) <> '' AND closed_at IS NOT NULL))
);

-- Partial on `pending`, which is the whole semantics: the same key after the
-- task is closed opens a NEW task, because the recurring audit that finds the
-- same discrepancy again is reporting it again, not repeating itself.
CREATE UNIQUE INDEX IF NOT EXISTS ux_task_idem
    ON task(consumer_id, idem_key)
 WHERE idem_key IS NOT NULL AND status = 'pending';

CREATE INDEX IF NOT EXISTS ix_task_owner  ON task(consumer_id, status);
CREATE INDEX IF NOT EXISTS ix_task_closed ON task(closed_at);

CREATE TABLE IF NOT EXISTS task_version (
  task_id        INTEGER NOT NULL REFERENCES task(task_id),
  version        INTEGER NOT NULL,
  title          TEXT,
  body           TEXT,
  consumer_id    INTEGER,          -- the owner of THAT day
  created_by     TEXT,
  urgent         INTEGER,
  status         TEXT,
  outcome        TEXT,
  reason_dropped TEXT,
  timestamp      TEXT NOT NULL,
  action         TEXT NOT NULL,    -- created/amended/reassigned/completed/
                                   -- dropped/archived
  actor          TEXT,
  PRIMARY KEY (task_id, version)
);

-- ---------------------------------------------------------------------
-- The one-time admin auth codes  (rev. 7 — RECONCILE THIS TABLE)
-- ---------------------------------------------------------------------
-- The web UI mints a row behind the web ui password; a structural gesture
-- burns it in the SAME transaction, and only when the gesture SUCCEEDS — a
-- refusal rolls back and does not consume it, so a typo does not cost a trip
-- to the UI. Spent or expired it is nothing, and alone it elevates nobody.
-- It lives in the project's own file, so a code minted for one project is
-- not a code for another.
CREATE TABLE IF NOT EXISTS auth_code (
  code_id      INTEGER PRIMARY KEY,
  code_hash    TEXT NOT NULL,       -- the code itself was shown once, on the page
  minted_at    TEXT NOT NULL,
  expires_at   TEXT NOT NULL,
  spent_at     TEXT,
  spent_action TEXT,                -- the gesture that burned it
  CHECK (spent_at IS NULL OR TRIM(IFNULL(spent_action,'')) <> '')
);

CREATE INDEX IF NOT EXISTS ix_auth_code_live ON auth_code(expires_at) WHERE spent_at IS NULL;

-- ---------------------------------------------------------------------
-- The IDs everyone reads
-- ---------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_rule AS
SELECT r.*, d.code || '-' || printf('%04d', r.seq) AS display_id
  FROM rule r JOIN domain d ON d.domain_id = r.domain_id;

CREATE VIEW IF NOT EXISTS v_task AS
SELECT t.*, 'TK-' || printf('%04d', t.seq) AS display_id FROM task t;

-- =====================================================================
-- The history is written by the DATABASE, not by tool code
-- =====================================================================
-- Same doctrine as every version of this schema: a row written by hand with
-- sqlite3 is photographed too.

CREATE TRIGGER IF NOT EXISTS trg_profile_ins AFTER INSERT ON project_profile BEGIN
  INSERT INTO project_profile_version (version, brief, specs, queue_cap,
                                       timestamp, action, actor)
  VALUES ({_NEXT_PROFILE_V}, NEW.brief, NEW.specs, NEW.queue_cap,
          NEW.updated_at, 'created', NEW.actor);
END;

CREATE TRIGGER IF NOT EXISTS trg_profile_upd AFTER UPDATE ON project_profile BEGIN
  INSERT INTO project_profile_version (version, brief, specs, queue_cap,
                                       timestamp, action, actor)
  VALUES ({_NEXT_PROFILE_V}, NEW.brief, NEW.specs, NEW.queue_cap,
          NEW.updated_at, 'amended', NEW.actor);
END;

CREATE TRIGGER IF NOT EXISTS trg_domain_ins AFTER INSERT ON domain BEGIN
  INSERT INTO domain_version (domain_id, version, code, description,
                              retired_at, retired_reason, timestamp, action, actor)
  VALUES (NEW.domain_id, {_f(_NEXT_DOMAIN_V, 'NEW')}, NEW.code, NEW.description,
          NEW.retired_at, NEW.retired_reason,
          NEW.created_at, 'created', NEW.actor);
END;

-- The action NAMES the retirement and the revival instead of calling both
-- 'amended': a domain ending is the change this history exists to record, and
-- a word that covers everything covers nothing.
CREATE TRIGGER IF NOT EXISTS trg_domain_upd AFTER UPDATE ON domain BEGIN
  INSERT INTO domain_version (domain_id, version, code, description,
                              retired_at, retired_reason, timestamp, action, actor)
  VALUES (NEW.domain_id, {_f(_NEXT_DOMAIN_V, 'NEW')}, NEW.code, NEW.description,
          NEW.retired_at, NEW.retired_reason, {_NOW},
          CASE
            WHEN NEW.retired_at IS NOT NULL AND OLD.retired_at IS NULL THEN 'retired'
            WHEN NEW.retired_at IS NULL AND OLD.retired_at IS NOT NULL THEN 'revived'
            ELSE 'amended'
          END, NEW.actor);
END;

CREATE TRIGGER IF NOT EXISTS trg_consumer_ins AFTER INSERT ON consumer BEGIN
  INSERT INTO consumer_version (consumer_id, version, name, kind, brief, specs,
                                retired_at, timestamp, action, actor)
  VALUES (NEW.consumer_id, {_f(_NEXT_CONSUMER_V, 'NEW')}, NEW.name, NEW.kind,
          NEW.brief, NEW.specs, NEW.retired_at, NEW.created_at, 'created', NEW.actor);
END;

CREATE TRIGGER IF NOT EXISTS trg_consumer_upd AFTER UPDATE ON consumer BEGIN
  INSERT INTO consumer_version (consumer_id, version, name, kind, brief, specs,
                                retired_at, timestamp, action, actor)
  VALUES (NEW.consumer_id, {_f(_NEXT_CONSUMER_V, 'NEW')}, NEW.name, NEW.kind,
          NEW.brief, NEW.specs, NEW.retired_at, {_NOW},
          CASE
            WHEN NEW.retired_at IS NOT NULL AND OLD.retired_at IS NULL THEN 'retired'
            WHEN NEW.retired_at IS NULL AND OLD.retired_at IS NOT NULL THEN 'revived'
            WHEN NEW.name <> OLD.name THEN 'renamed'
            ELSE 'amended'
          END, NEW.actor);
END;

CREATE TRIGGER IF NOT EXISTS trg_group_ins AFTER INSERT ON consumer_group BEGIN
  INSERT INTO consumer_group_version (group_id, version, name, retired_at,
                                      retired_reason, timestamp, action, actor)
  VALUES (NEW.group_id, {_f(_NEXT_GROUP_V, 'NEW')}, NEW.name, NEW.retired_at,
          NEW.retired_reason, NEW.created_at, 'created', NEW.actor);
END;

CREATE TRIGGER IF NOT EXISTS trg_group_upd AFTER UPDATE ON consumer_group BEGIN
  INSERT INTO consumer_group_version (group_id, version, name, retired_at,
                                      retired_reason, timestamp, action, actor)
  VALUES (NEW.group_id, {_f(_NEXT_GROUP_V, 'NEW')}, NEW.name, NEW.retired_at,
          NEW.retired_reason, {_NOW},
          CASE
            WHEN NEW.retired_at IS NOT NULL AND OLD.retired_at IS NULL THEN 'retired'
            WHEN NEW.retired_at IS NULL AND OLD.retired_at IS NOT NULL THEN 'revived'
            WHEN NEW.name <> OLD.name THEN 'renamed'
            ELSE 'amended'
          END, NEW.actor);
END;

CREATE TRIGGER IF NOT EXISTS trg_rule_ins AFTER INSERT ON rule BEGIN
  INSERT INTO rule_version (rule_id, version, type, title, body, status,
                            permanence, expires_at, reach, superseded_by_rule_id,
                            timestamp, action, reason, actor)
  VALUES (NEW.rule_id, {_f(_NEXT_RULE_V, 'NEW')}, NEW.type, NEW.title, NEW.body,
          NEW.status, NEW.permanence, NEW.expires_at, NEW.reach,
          NEW.superseded_by_rule_id, NEW.created_at, 'created', NEW.reason,
          NEW.actor);
END;

CREATE TRIGGER IF NOT EXISTS trg_rule_upd AFTER UPDATE ON rule BEGIN
  INSERT INTO rule_version (rule_id, version, type, title, body, status,
                            permanence, expires_at, reach, superseded_by_rule_id,
                            timestamp, action, reason, actor)
  VALUES (NEW.rule_id, {_f(_NEXT_RULE_V, 'NEW')}, NEW.type, NEW.title, NEW.body,
          NEW.status, NEW.permanence, NEW.expires_at, NEW.reach,
          NEW.superseded_by_rule_id, NEW.updated_at,
          -- The action is the VERB, and the verbs worth having are the ones
          -- the database can DERIVE: a transition of `status` is visible from
          -- here, so approval, denial and retirement name themselves instead
          -- of hiding inside a generic 'amended'. A perimeter change is not
          -- derivable — narrowing a targeted rule to fewer groups leaves
          -- `reach` exactly where it was — so it stays 'amended' and says what
          -- it was in `reason`, next to a photograph that shows the audience
          -- shrink. A verb that is right half the time is worse than one that
          -- is always honest.
          CASE
            WHEN NEW.status = 'active'  AND OLD.status = 'proposed'  THEN 'approved'
            WHEN NEW.status = 'denied'  AND OLD.status <> 'denied'   THEN 'denied'
            WHEN NEW.status = 'retired' AND OLD.status <> 'retired'  THEN 'retired'
            ELSE 'amended'
          END,
          NEW.event, NEW.actor);
END;

CREATE TRIGGER IF NOT EXISTS trg_task_ins AFTER INSERT ON task BEGIN
  INSERT INTO task_version (task_id, version, title, body, consumer_id,
                            created_by, urgent, status, outcome, reason_dropped,
                            timestamp, action, actor)
  VALUES (NEW.task_id, {_f(_NEXT_TASK_V, 'NEW')}, NEW.title, NEW.body,
          NEW.consumer_id, NEW.created_by, NEW.urgent, NEW.status, NEW.outcome,
          NEW.reason_dropped, NEW.created_at, 'created', NEW.created_by);
END;

CREATE TRIGGER IF NOT EXISTS trg_task_upd AFTER UPDATE ON task BEGIN
  INSERT INTO task_version (task_id, version, title, body, consumer_id,
                            created_by, urgent, status, outcome, reason_dropped,
                            timestamp, action, actor)
  VALUES (NEW.task_id, {_f(_NEXT_TASK_V, 'NEW')}, NEW.title, NEW.body,
          NEW.consumer_id, NEW.created_by, NEW.urgent, NEW.status, NEW.outcome,
          NEW.reason_dropped, NEW.updated_at,
          CASE
            WHEN NEW.archived_at IS NOT NULL AND OLD.archived_at IS NULL
              THEN 'archived'
            WHEN NEW.consumer_id <> OLD.consumer_id THEN 'reassigned'
            WHEN NEW.status = 'pending' THEN 'amended'
            ELSE NEW.status
          END, NEW.actor);
END;

-- THE PHOTOGRAPH OF THE AUDIENCE, and it hangs off the version row rather
-- than off the rule: whatever door wrote the version — a tool, the batch
-- page, sqlite3 by hand — the picture of who this rule reached that day gets
-- taken with it.
--
-- Two things in here are not obvious and both are load-bearing:
--
--   * a consumer reached by TWO groups of the same rule gets ONE row, and it
--     carries the LOWEST group_id. Group-with-group overlap is allowed on
--     purpose (the structure changes on its own, and forbidding it would mean
--     revalidating every rule at each tweak), so the picture has to survive
--     it. What the snapshot answers is WHO WAS REACHED; the door is a detail
--     that can legitimately be plural, and `via_group_id` here means "one of
--     the groups that reached it", not "the only one".
--   * an exception WINS over a group. An exception already covered by this
--     rule's groups is refused at write time — but an overlap that forms
--     LATER (the consumer joins the group afterwards) is deliberately NOT
--     blocked, so it can and will be sitting here at photograph time. The
--     exception was declared by hand, so it keeps the row and the group
--     branch skips that consumer. Without this, a legal write would ABORT on
--     a primary key.
CREATE TRIGGER IF NOT EXISTS trg_rule_version_audience
AFTER INSERT ON rule_version
BEGIN
  INSERT INTO rule_version_audience (rule_id, version, consumer_id, via_group_id)
  SELECT NEW.rule_id, NEW.version, c.consumer_id, NULL
    FROM consumer c
   WHERE NEW.reach = 'all' AND c.retired_at IS NULL;

  INSERT INTO rule_version_audience (rule_id, version, consumer_id, via_group_id)
  SELECT NEW.rule_id, NEW.version, e.consumer_id, NULL
    FROM rule_audience_exception e
    JOIN consumer c ON c.consumer_id = e.consumer_id
   WHERE NEW.reach = 'targeted' AND e.rule_id = NEW.rule_id
     AND c.retired_at IS NULL;

  INSERT INTO rule_version_audience (rule_id, version, consumer_id, via_group_id)
  SELECT NEW.rule_id, NEW.version, m.consumer_id, MIN(m.group_id)
    FROM rule_audience_group g
    JOIN consumer_group_member m ON m.group_id = g.group_id
    JOIN consumer c ON c.consumer_id = m.consumer_id
   WHERE NEW.reach = 'targeted' AND g.rule_id = NEW.rule_id
     AND c.retired_at IS NULL
     AND m.consumer_id NOT IN (SELECT consumer_id FROM rule_audience_exception
                                WHERE rule_id = NEW.rule_id)
   GROUP BY m.consumer_id;
END;

-- =====================================================================
-- The guarantees that live in the schema
-- =====================================================================

-- A domain's code is printed inside every ID it ever handed out.
CREATE TRIGGER IF NOT EXISTS trg_domain_code_frozen
BEFORE UPDATE OF code ON domain
WHEN NEW.code <> OLD.code
BEGIN
  SELECT RAISE(ABORT, 'frozen field: a domain code is written once — it is printed inside every ID it ever handed out, and changing it would relabel history');
END;

-- Retiring a domain that still has rules in force would leave those rules
-- with a dead label. The rules go first.
CREATE TRIGGER IF NOT EXISTS trg_domain_retire_active
BEFORE UPDATE ON domain
WHEN NEW.retired_at IS NOT NULL AND OLD.retired_at IS NULL
 AND EXISTS (SELECT 1 FROM rule
              WHERE domain_id = OLD.domain_id AND status = 'active')
BEGIN
  SELECT RAISE(ABORT, 'domain still in force: it has active rules — retire or supersede them first');
END;

-- THE EXCLUSIVE ARC, and it is an INVARIANT: `all` means no audience rows,
-- `targeted` means at least one. It is checked after every write to the rule
-- because that is when both sides exist — the audience arrives first, the
-- rule last — and because an invariant that only holds at creation is an
-- invariant somebody will break with an amendment.
--
-- `reach` DECLARED and never deduced is the whole point: a targeted rule
-- whose rows never arrived must FAIL, not quietly become universal.
CREATE TRIGGER IF NOT EXISTS trg_rule_arc_ins
AFTER INSERT ON rule
WHEN (NEW.reach = 'all'
        AND (EXISTS (SELECT 1 FROM rule_audience_group WHERE rule_id = NEW.rule_id)
          OR EXISTS (SELECT 1 FROM rule_audience_exception WHERE rule_id = NEW.rule_id)))
   OR (NEW.reach = 'targeted'
        AND NOT EXISTS (SELECT 1 FROM rule_audience_group WHERE rule_id = NEW.rule_id)
        AND NOT EXISTS (SELECT 1 FROM rule_audience_exception WHERE rule_id = NEW.rule_id))
BEGIN
  SELECT RAISE(ABORT, 'reach does not match the audience: all takes no group and no exception, targeted takes at least one — reach is declared, never deduced');
END;

CREATE TRIGGER IF NOT EXISTS trg_rule_arc_upd
AFTER UPDATE ON rule
WHEN (NEW.reach = 'all'
        AND (EXISTS (SELECT 1 FROM rule_audience_group WHERE rule_id = NEW.rule_id)
          OR EXISTS (SELECT 1 FROM rule_audience_exception WHERE rule_id = NEW.rule_id)))
   OR (NEW.reach = 'targeted'
        AND NOT EXISTS (SELECT 1 FROM rule_audience_group WHERE rule_id = NEW.rule_id)
        AND NOT EXISTS (SELECT 1 FROM rule_audience_exception WHERE rule_id = NEW.rule_id))
BEGIN
  SELECT RAISE(ABORT, 'reach does not match the audience: all takes no group and no exception, targeted takes at least one — reach is declared, never deduced');
END;

-- And the same arc watched from the other side: an audience row added to a
-- rule that is already universal. The two above fire on writes to `rule`, so
-- without these a row could be slipped next to a live universal rule and
-- nothing would notice until the rule was next touched.
--
-- The subquery returns NULL while the rule does not exist yet, and NULL is
-- not `<> 'targeted'`, so the creation path — audience first, rule last —
-- passes here and is caught by trg_rule_arc_ins instead. That is deliberate,
-- and it is why the arc is enforced in two places rather than one.
CREATE TRIGGER IF NOT EXISTS trg_audience_group_arc
BEFORE INSERT ON rule_audience_group
WHEN (SELECT reach FROM rule WHERE rule_id = NEW.rule_id) <> 'targeted'
BEGIN
  SELECT RAISE(ABORT, 'reach is not targeted: a universal rule takes no audience row — declare reach=targeted first');
END;

CREATE TRIGGER IF NOT EXISTS trg_audience_exception_arc
BEFORE INSERT ON rule_audience_exception
WHEN (SELECT reach FROM rule WHERE rule_id = NEW.rule_id) <> 'targeted'
BEGIN
  SELECT RAISE(ABORT, 'reach is not targeted: a universal rule takes no audience row — declare reach=targeted first');
END;

-- CLOSED IS CLOSED. The outcome and the reason are the two sentences the
-- whole log is read for, and a log whose past sentences can change is a log
-- nobody can quote. The one exception is the prune, which only ever sets
-- `archived_at` — see the next trigger for what it may not touch.
CREATE TRIGGER IF NOT EXISTS trg_task_closed_is_closed
BEFORE UPDATE ON task
WHEN OLD.status <> 'pending'
 AND NOT (NEW.archived_at IS NOT NULL AND OLD.archived_at IS NULL
          AND NEW.status = OLD.status AND NEW.title = OLD.title
          AND NEW.body = OLD.body AND IFNULL(NEW.outcome,'') = IFNULL(OLD.outcome,'')
          AND IFNULL(NEW.reason_dropped,'') = IFNULL(OLD.reason_dropped,''))
BEGIN
  SELECT RAISE(ABORT, 'closed task: a completed or dropped task is not amended and not reopened — open a new one');
END;

-- The prune archives what is finished. An open task is not clutter, it is
-- work, and archiving it would hide it from the desk that owes it.
CREATE TRIGGER IF NOT EXISTS trg_task_archive_closed_only
BEFORE UPDATE ON task
WHEN NEW.archived_at IS NOT NULL AND NEW.status = 'pending'
BEGIN
  SELECT RAISE(ABORT, 'open task: the prune archives what is closed — this one is still pending');
END;

-- What never changes while it is open. `urgent` is in here because of WHO
-- sets it: the creator knows the condition that made the work urgent, and
-- letting the receiver clear the flag would put the lever in the hand of
-- whoever has an interest in postponing.
CREATE TRIGGER IF NOT EXISTS trg_task_frozen
BEFORE UPDATE ON task
WHEN NEW.task_id <> OLD.task_id OR NEW.seq <> OLD.seq OR NEW.urgent <> OLD.urgent
  OR NEW.created_by <> OLD.created_by OR NEW.created_at <> OLD.created_at
BEGIN
  SELECT RAISE(ABORT, 'frozen field: the ID, the number, the author, the date and the urgent flag are written once — urgency is the creator''s and is not cleared by whoever receives it');
END;

-- A one-time code is one-time in the DATABASE, not only in the function that
-- checks it: a second burn is refused even if the check is bypassed.
CREATE TRIGGER IF NOT EXISTS trg_auth_code_spent_once
BEFORE UPDATE ON auth_code
WHEN OLD.spent_at IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'auth code already spent: a one-time code is spent once — mint another on the maintenance page');
END;
"""

# Counted from the code, never written twice. The preflight counts these by
# itself and compares against what the database actually holds.
TABLES = ("project_profile", "project_profile_version",
          "domain", "domain_version",
          "consumer", "consumer_version",
          "consumer_group", "consumer_group_version", "consumer_group_member",
          "rule", "rule_audience_group", "rule_audience_exception", "rule_ref",
          "rule_version", "rule_version_audience",
          "decision", "decision_rule",
          "task", "task_version",
          "auth_code")

VIEWS = ("v_rule", "v_task")

# Only the indexes that carry a GUARANTEE, never the ones that carry speed.
INDEXES = ("ux_domain_fold", "ux_consumer_fold", "ux_group_fold",
           "ux_rule_supersedes", "ux_task_idem")

TRIGGERS = ("trg_profile_ins", "trg_profile_upd",
            "trg_domain_ins", "trg_domain_upd",
            "trg_consumer_ins", "trg_consumer_upd",
            "trg_group_ins", "trg_group_upd",
            "trg_rule_ins", "trg_rule_upd",
            "trg_task_ins", "trg_task_upd",
            "trg_rule_version_audience",
            "trg_domain_code_frozen", "trg_domain_retire_active",
            "trg_rule_arc_ins", "trg_rule_arc_upd",
            "trg_audience_group_arc", "trg_audience_exception_arc",
            "trg_task_closed_is_closed", "trg_task_archive_closed_only",
            "trg_task_frozen",
            "trg_auth_code_spent_once")



# =====================================================================
# The registry: a FILE, not a table
# =====================================================================
# Until v3.1.0 the list of projects was a TABLE inside the one database that
# held them all, reachable through tools that could create and rekey. It is a
# text file now, and the reasons are three:
#
# - one project is one SQLite file, so spillover between projects is not
#   forbidden, it is impossible;
# - the file IS the truth. There is no state to reconcile it against: a line
#   without a database creates one, a database without a line is not served;
# - what is catastrophic has no tool. Deleting a project is deleting a folder
#   from Unraid, and no call from a chat can reach it.
#
# The container sees /db and nothing above it; the mapping from the host is
# the Unraid template's business, exactly as before.

DB_ROOT = "/db"
REGISTRY_FILE = "projects.txt"
# Root only. The file holds the reference and admin codes in clear, which is
# the decision: the file is the safe, and root is the process.
REGISTRY_MODE = 0o600
REGISTRY_FIELDS = 3

# Written into the file the first time the server finds none, so that whoever
# opens it from Unraid finds the instructions already inside. It is in English
# like everything else in this repository, and it carries NO example row: a
# specimen line with plausible codes is a line somebody uncomments.
REGISTRY_TEMPLATE = """\
# projects.txt — the project registry of codifier-mcp
#
# One line per project, three fields, separated by |
#     name | reference code | admin code
#
# - This file IS the truth: a line without a database creates it
#   (empty, current schema — the log says so out loud); a database
#   without a line is not served.
# - The NAME is a folder NEXT TO this file (original spelling); its slug
#   (lowercase, spaces to dashes) is the .db filename inside it.
#   Renaming a project = edit this line AND rename the folder.
# - The reference code is what every chat of the project carries; the
#   admin code is what elevates one. Both are 8 to 32 letters and
#   digits, and no code may appear twice in this file.
# - Lines starting with # and blank lines are ignored.
# - Root only (600). Edited from Unraid; the server re-reads on mtime.
# - Deletion is never done from tools: retire here, remove from Unraid.
"""


def _registry_lines(text: str, where: str) -> list[tuple]:
    """Parse the registry. Every refusal names the LINE, and the line number
    is the one an editor shows — comments and blanks counted in.

    Nothing here is repaired quietly. A registry read half-right is worse than
    one that will not read at all, because the half that got through looks
    like the whole."""
    out: list[tuple] = []
    slug_at: dict[str, int] = {}
    code_at: dict[str, int] = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != REGISTRY_FIELDS:
            raise RulesFault(
                f"{where} line {n}: {len(parts)} field(s) where {REGISTRY_FIELDS} are "
                f"expected. A project line is `name | reference code | admin code` "
                f"and nothing else is served — comment it out with # while you fix "
                f"it: {line!r}")
        name, ref, adm = parts
        if not RE_NAME.match(name):
            raise RulesFault(
                f"{where} line {n}: {name!r} is not a usable project name — letters, "
                "digits, spaces, dashes and underscores, up to 41 characters, and it "
                "starts with a letter or a digit")
        try:
            slug = _slug(name)
        except RulesError as exc:
            raise RulesFault(f"{where} line {n}: {exc}") from None
        for label, code in (("reference code", ref), ("admin code", adm)):
            if not RE_CODE.match(code):
                raise RulesFault(
                    f"{where} line {n}: the {label} of {name!r} is not 8 to 32 letters "
                    "and digits. Codes are generated, never invented — and a field left "
                    "empty is a door left open")
            if code in code_at:
                # This catches the two mistakes that matter with one sentence:
                # the same code on two projects (which project would answer?)
                # and the reference code equal to the admin code on ONE project,
                # which is elevation handed to every chat that can read.
                raise RulesFault(
                    f"{where} line {n}: the {label} of {name!r} already appears on line "
                    f"{code_at[code]}. Every code in this file is its own: one that is "
                    "written twice either points at two projects or turns a reference "
                    "code into an admin code")
            code_at[code] = n
        if slug in slug_at:
            raise RulesFault(
                f"{where} line {n}: {name!r} and the project on line {slug_at[slug]} "
                f"would share the file {slug}.db — two projects, one database. Rename "
                "one of them")
        slug_at[slug] = n
        out.append((n, name, ref, adm))
    return out


def _registry_text(path: str) -> str:
    """UTF-8, strictly, and a decoding failure names the line it broke on.

    Strict because the alternative is `errors='replace'`, and a project name
    silently altered by a replacement character is a folder that will never be
    found again."""
    with open(path, "rb") as fh:
        data = fh.read()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        n = data[:exc.start].count(b"\n") + 1
        raise RulesFault(
            f"{path} line {n}: not UTF-8 (byte {exc.object[exc.start]:#04x} at "
            f"offset {exc.start}). The registry is a plain UTF-8 text file — an "
            "editor that saved it in another encoding has to save it again") from None


class Registry:
    """The router: which files are served, and which code opens which.

    It is not a connection pool and not a cache — it is the reading of a file
    turned into objects, redone whenever the file changes. `project(code)` is
    the only door in: from a code to one database, or to the same refusal a
    missing code gets.

    A malformed registry stops EVERYTHING, at boot and at re-read alike, and
    the previous reading is kept untouched rather than half-replaced: the
    service refuses with the offending line in the message until the file
    parses again. The alternative — carrying on with the last good reading —
    means serving a truth the file no longer states, which is the one failure
    this registry exists to make impossible."""

    def __init__(self, root: str = DB_ROOT, *,
                 provisional_days: int = DEFAULT_PROVISIONAL_DAYS,
                 pending_cap: int = DEFAULT_PENDING_CAP) -> None:
        self.root = root
        self.file = os.path.join(root, REGISTRY_FILE)
        self.provisional_days = int(provisional_days or DEFAULT_PROVISIONAL_DAYS)
        self.pending_cap = int(pending_cap or DEFAULT_PENDING_CAP)
        self._lock = threading.RLock()
        self._open: dict[str, Project] = {}      # by slug
        self._by_code: dict[str, Project] = {}   # by REFERENCE code
        self._mtime: int | None = None
        self.template_written = False
        os.makedirs(root, exist_ok=True)
        self._ensure_file()
        self.reload(force=True)

    # ---------- the file ----------

    def _ensure_file(self) -> None:
        """Create-if-missing, with the template inside. Said out loud, because
        an empty registry serves nothing and the reason must not have to be
        guessed from silence."""
        if os.path.exists(self.file):
            return
        with open(self.file, "w", encoding="utf-8") as fh:
            fh.write(REGISTRY_TEMPLATE)
        self.template_written = True
        log.warning("created %s from the template: no project is served until a line "
                    "is added to it", self.file)

    def _enforce_mode(self) -> None:
        """0600, re-imposed after every re-read.

        Not only at creation: the file is edited from Unraid, over a share,
        and an editor that writes a new file and renames it over the old one
        brings its own mode with it. The codes are in clear in there — that is
        the decision — so the mode is the whole of the protection, and a
        protection restored only on the day the file is born is not one."""
        try:
            if os.stat(self.file).st_mode & 0o777 != REGISTRY_MODE:
                os.chmod(self.file, REGISTRY_MODE)
        except OSError:
            pass

    def reload(self, force: bool = False) -> bool:
        """Re-read if the file moved. Adding a project does not need a restart.

        mtime and not a checksum: the file is edited by a person from Unraid,
        never by this process, and a person who saves changes the mtime. The
        stamp is stored only on SUCCESS, so a file that will not parse is
        retried at the next call instead of being taken as read."""
        with self._lock:
            self._ensure_file()
            mtime = os.stat(self.file).st_mtime_ns
            if not force and mtime == self._mtime:
                return False
            lines = _registry_lines(_registry_text(self.file), self.file)
            keep: dict[str, Project] = {}
            born: list[Project] = []
            try:
                for _n, name, ref, adm in lines:
                    slug = _slug(name)
                    p = self._open.get(slug)
                    if p is None or p.name != name:
                        # A different spelling is a different FOLDER, so it is a
                        # different project on disk even when the slug matches.
                        p = Project(name, self.root,
                                    provisional_days=self.provisional_days,
                                    pending_cap=self.pending_cap)
                        born.append(p)
                    p.reference_code, p.admin_code = ref, adm
                    keep[slug] = p
            except Exception:
                for p in born:
                    p.close()
                raise
            for slug, p in self._open.items():
                if keep.get(slug) is not p:
                    p.close()
            self._open = keep
            self._by_code = {p.reference_code: p for p in keep.values()}
            self._mtime = mtime
            self._enforce_mode()
            return True

    # ---------- the door ----------

    def project(self, code: str) -> Project:
        """From the reference CODE to one database. Never from the name: the
        name is not a credential. Identical refusal for a missing code and a
        wrong one — telling them apart would confirm a valid code to whoever
        holds only half of one."""
        self.reload()
        c = (code or "").strip()
        if not c:
            raise RulesError(ERR_PROJECT)
        with self._lock:
            p = self._by_code.get(c)
        if p is None:
            raise RulesError(ERR_PROJECT)
        return p

    def by_name(self, name: str) -> Project:
        """From the NAME to one database, and it is the administration page's
        door, not a chat's: the page addresses projects by name because the
        codes must not leave the process to sit in a URL."""
        self.reload()
        n = _fold(name)
        with self._lock:
            for p in self._open.values():
                if _fold(p.name) == n:
                    return p
            known = ", ".join(sorted(p.name for p in self._open.values())) or "(none)"
        raise RulesError(f"unknown project {name!r}. Served right now: {known}")

    def projects(self) -> dict:
        """What is served, by name. NO CODES: they live in the file and in the
        instructions of whoever holds them, and a listing that carried them
        would be the oracle this registry has never had."""
        self.reload()
        with self._lock:
            out = [{"name": p.name, "slug": p.slug, "path": p.path,
                    "schema": p.generation, "born_empty": p.born_empty}
                   for p in sorted(self._open.values(), key=lambda x: _fold(x.name))]
        return {"projects": out, "count": len(out), "registry": self.file}

    def repaired(self) -> dict[str, list[str]]:
        """Per project, the schema objects that had to be rebuilt at open."""
        with self._lock:
            return {p.name: p.repaired for p in self._open.values() if p.repaired}

    def born_empty(self) -> list[str]:
        """The projects whose database this boot CREATED. On a first
        installation that is the whole registry; on any other boot it is the
        signature of a folder that was not renamed with its line."""
        with self._lock:
            return sorted(p.name for p in self._open.values() if p.born_empty)

    def close(self) -> None:
        with self._lock:
            for p in self._open.values():
                p.close()
            self._open, self._by_code, self._mtime = {}, {}, None


# =====================================================================
# One project, one file
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
class Project:
    """One project: one folder, one file, one connection, one lock.

    The path is not configuration, it is arithmetic: `<root>/<name>/<slug>.db`,
    folder spelled as the name is spelled, file spelled as the slug. Nothing
    reads a path from anywhere — which is why a rename that moves only half of
    the pair shows up as an empty database and a shouting log line, and not as
    a project quietly serving the wrong file."""

    def __init__(self, name: str, root: str = DB_ROOT, *,
                 reference_code: str = "", admin_code: str = "",
                 provisional_days: int = DEFAULT_PROVISIONAL_DAYS,
                 pending_cap: int = DEFAULT_PENDING_CAP) -> None:
        self.name = (name or "").strip()
        self.slug = _slug(self.name)
        self.reference_code = reference_code
        self.admin_code = admin_code
        self.dir = os.path.join(root, self.name)
        self.path = os.path.join(self.dir, self.slug + ".db")
        self.provisional_days = int(provisional_days or DEFAULT_PROVISIONAL_DAYS)
        self.pending_cap = int(pending_cap or DEFAULT_PENDING_CAP)
        # Re-entrant, and it must exist before anything else: every public
        # method acquires it (see _serialised).
        self._lock = threading.RLock()
        os.makedirs(self.dir, exist_ok=True)
        try:
            os.chmod(self.dir, DIR_MODE)
        except OSError:
            pass
        self.born_empty = not os.path.exists(self.path)
        # check_same_thread=False because the server calls tools from a thread
        # pool. It is safe ONLY together with the lock above.
        self.cx = sqlite3.connect(self.path, timeout=10, isolation_level=None,
                                  check_same_thread=False)
        self.cx.row_factory = sqlite3.Row
        self.cx.execute("PRAGMA journal_mode=WAL")
        self.cx.execute("PRAGMA synchronous=FULL")
        self.cx.execute("PRAGMA foreign_keys=ON")
        self.cx.execute("PRAGMA busy_timeout=10000")
        generation = self.cx.execute("PRAGMA user_version").fetchone()[0]
        if self.born_empty:
            self.cx.executescript(SCHEMA)
            # Written LAST, after the schema is in: a file that carries the
            # generation carries the schema too, or the number would be a
            # promise instead of a fact.
            self.cx.execute(f"PRAGMA user_version = {SCHEMA_GENERATION}")
            self.generation = SCHEMA_GENERATION
            self.repaired: list[str] = []
            log.warning("created empty database for %s at %s — schema generation %d, "
                        "no anagrafica and no rules. If this project already existed, "
                        "its folder was not renamed along with its registry line",
                        self.name, self.path, SCHEMA_GENERATION)
        elif generation != SCHEMA_GENERATION:
            # THE refusal that used to be a migration. A database that does not
            # know its generation reads as 0, which is exactly what every
            # pre-4.0.0 file says, so the sentence fits both cases without a
            # second test.
            self.cx.close()
            raise RulesFault(
                f"{self.path} carries schema generation {generation} and this server "
                f"speaks {SCHEMA_GENERATION}: the file is NOT served. There is no "
                "migration, by decision — the corpus goes back in by hand. Move the "
                "folder out of the way from Unraid and let the registry line create "
                "the project again, or put back the image that speaks it.")
        else:
            # The schema is re-applied at every open: a missing object —
            # typically a trigger dropped by hand — is rebuilt. But the repair
            # is DECLARED: a trigger that vanishes raises no error, it just
            # stops writing history.
            before = {r[0] for r in self.cx.execute("SELECT name FROM sqlite_master")}
            self.cx.executescript(SCHEMA)
            after = {r[0] for r in self.cx.execute("SELECT name FROM sqlite_master")}
            # Read from the FILE, not copied from the constant: they are equal
            # by construction, which is exactly the kind of equality that stops
            # being true without anybody noticing.
            self.generation = generation
            self.repaired = sorted(after - before)
        self._fix_modes()

    def __repr__(self) -> str:            # what a log line and a traceback show
        return f"<Project {self.name!r} at {self.path}>"

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

    def _record_approval(self, project: str, digest: str, ids: list[str]) -> None:
        """One row per approval, written by the lifecycle. What it records is
        WHAT was let in and WHEN — the who is the OAuth gate's business, a
        layer up, and the signature that used to sit here left in v2.0.0."""
        self.cx.execute(
            "INSERT INTO approvals (project, digest, n_rules, rule_ids, approved_at) "
            "VALUES (?,?,?,?,?)",
            (project, digest, len(ids), ",".join(ids), _now()))

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
        """Resolve a consumer by its CASEFOLDED name and hand back the STORED
        spelling. The caller may type `architect` and the registry answers
        with `Architect`: identity is folded, spelling is data, and every
        answer carries the spelling the owner chose."""
        n = _fold(name)
        allowed, retired = [], {}
        for r in self.cx.execute(
                "SELECT name, retired_at FROM consumers WHERE project=? ORDER BY name",
                (project,)):
            if r["retired_at"]:
                retired[_fold(r["name"])] = (r["name"], r["retired_at"])
            else:
                allowed.append(r["name"])
        if not n:
            raise RulesError(f"consumer not specified. This project has: {', '.join(allowed)}")
        for stored in allowed:
            if _fold(stored) == n:
                return stored
        # A RETIRED ONE IS NOT AN UNKNOWN ONE, and saying so is the difference
        # between "you typed it wrong" and "that role ended". This is the door
        # every other one goes through — list, get, propose, the task log — so
        # the sentence is written once and every caller gets it.
        if n in retired:
            stored, when = retired[n]
            raise RulesError(
                f"{stored} was RETIRED on {when}: it reaches nothing, it holds nothing, "
                "and no rule or task can name it. If that role is back, revive it with "
                "rules_consumers_add and an item carrying revive:true — which is a "
                "decision, not a typo, so it has to be said out loud. Live consumers: "
                f"{', '.join(allowed) or '(none)'}")
        raise RulesError(
            f"unknown consumer {name!r}. This project has: {', '.join(allowed) or '(none)'}")

    def _domains(self, project: str) -> list[str]:
        return [r[0] for r in self.cx.execute(
            "SELECT domain FROM project_domains WHERE project=? ORDER BY domain", (project,))]

    def _legend(self, project: str, ids) -> dict:
        """The domains PRESENT in a list of IDs, each with its gloss, read
        from the project's own declarations. Two letters age badly in human
        memory — even the owner reads these lists, and in six months nobody
        remembers what LQ was. Surfaced, not new state, and limited to what
        the list actually contains so it never becomes a second registry."""
        doms = {str(i).split("-", 1)[0] for i in ids}
        if not doms:
            return {}
        return {r["domain"]: r["description"] or "" for r in self.cx.execute(
            "SELECT domain, description FROM project_domains WHERE project=? "
            "ORDER BY domain", (project,)) if r["domain"] in doms}

    @staticmethod
    def _legend_line(legend: dict) -> str:
        return " · ".join(f"{d} — {g}" if g else d for d, g in legend.items())

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
        cons = self.cx.execute("SELECT name, kind FROM consumers WHERE project=? "
                               "AND retired_at IS NULL ORDER BY kind, name", (p,)).fetchall()
        gone = self.cx.execute("SELECT name, kind, retired_at, retired_reason FROM consumers "
                               "WHERE project=? AND retired_at IS NOT NULL ORDER BY name",
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
            # Kept APART, never merged into the list above: a retired consumer
            # is not one of the project's consumers any more, and a caller
            # picking a name out of that list must not be able to pick a dead
            # one. Shown at all because the history keeps resolving, so a name
            # met in an old version has to be explainable.
            "retired_consumers": [{"name": c["name"], "kind": c["kind"],
                                   "retired_at": c["retired_at"],
                                   "reason": c["retired_reason"]} for c in gone],
            "scopes": scopes,
            "domains": {d["domain"]: d["description"] for d in doms},
            "registry_version": VERSION,
            "approval": {"provisional_days": self.provisional_days},
        }

    def create_project(self, name: str, consumers=None, domains=None,
                       description: str = "") -> dict:
        """Create a project. The CODE and the ARCHITECT KEY are GENERATED
        here and returned ONCE, on this receipt: from then on the key exists
        only as a hash. Consumers and domains are OPTIONAL — a project may be
        born empty, because seeding it (domains, then consumers, then the
        rules one by one) is the Architect's first job, done with the very
        key this call hands out."""
        name = (name or "").strip()
        if not name:
            raise RulesError("the project needs a name")
        if self.cx.execute("SELECT 1 FROM projects WHERE name=?", (name,)).fetchone():
            raise RulesError(f"a project named {name!r} already exists")
        if isinstance(domains, (list, tuple)):
            domains = {d: "" for d in domains}
        domains = domains or {}
        for d in domains:
            _valid_domain(d)
        cons = self._normalise_consumers(consumers or [])
        # The sanitisation runs here too, and at this door it can only do half
        # its job: the project does not exist yet, so no domain is declared and
        # no rule can be resolved — what fires is the SHORT-FORM relic, which
        # needs neither. Said out loud because a half-check nobody declares is
        # how a guarantee becomes a habit. Everything written afterwards goes
        # through the full door.
        for _n, _k, _b, _r in cons:
            self._relics(name, f"brief of {_n!r}", _b)
        for _d, _desc in domains.items():
            self._relics(name, f"gloss of {_d}", _desc)
        self._relics(name, "description", description)
        while True:
            code = _gen(CODE_LEN)
            if not self.cx.execute("SELECT 1 FROM projects WHERE code=?",
                                   (code,)).fetchone():
                break
        key = _gen(KEY_LEN)
        try:
            self.cx.execute("BEGIN")
            self.cx.execute("INSERT INTO projects (name, code, architect_key_hash, "
                            "description, created) VALUES (?,?,?,?,?)",
                            (name, code, _key_hash(key), description or None, _now()))
            for d, desc in domains.items():
                self.cx.execute("INSERT INTO project_domains (project, domain, description) "
                                "VALUES (?,?,?)", (name, d, desc or None))
            self.cx.execute("INSERT INTO scopes (project, name, managed) VALUES (?,?,1)",
                            (name, ALL))
            for cname, kind, brief, _ in cons:
                self.cx.execute("INSERT INTO consumers (project, name, kind, brief, "
                                "created) VALUES (?,?,?,?,?)",
                                (name, cname, kind or "chat", brief or None, _now()))
            self.cx.execute("COMMIT")
        except Exception:
            self.cx.execute("ROLLBACK")
            raise
        return {"created": name, "code": code, "architect_key": key,
                "consumers": [c for c, _, _, _ in cons], "domains": sorted(domains),
                "note": "TWO secrets, TWO destinations, shown ONCE: the code goes at "
                        "the top of the project instructions, the architect key in "
                        "the password manager. Neither can be read back — losing "
                        "either costs a rekey, which regenerates the pair."}

    @staticmethod
    def _normalise_consumers(consumers) -> list[tuple[str, str, str]]:
        """Each item may carry its BRIEF and its KIND — creating a consumer and
        giving it its identity is one gesture, not three calls.

        `kind` COMES BACK EMPTY WHEN IT WAS NOT GIVEN, and that distinction is
        the whole point of this shape. It used to default to 'chat' right here,
        which threw away the difference between "this is a chat" and "the
        caller said nothing" — so a later call naming an existing SKILL as a
        bare string would have silently demoted it. The default is applied
        where a row is CREATED, which is the only place it means anything."""
        out: list[tuple[str, str, str, bool]] = []
        for item in consumers or []:
            brief, revive = "", False
            if isinstance(item, str):
                cname, kind = item, ""
            elif isinstance(item, dict):
                cname, kind = item.get("name", ""), item.get("kind", "")
                brief = item.get("brief") or ""
                revive = bool(item.get("revive"))
            else:
                vals = list(item)
                cname = vals[0] if vals else ""
                kind = vals[1] if len(vals) > 1 else ""
                brief = vals[2] if len(vals) > 2 else ""
            cname = _valid_name(cname, "consumer")
            # A NAME keeps its spelling; a KIND does not have one. The rule is
            # worth stating because it looks like an inconsistency and is the
            # opposite: `Architect` is somebody's choice and is data, while
            # `SKILL`, `Skill` and `skill` are one value written three ways —
            # a closed set of two, where a second spelling buys nothing and
            # costs a comparison that can disagree with itself.
            kind = (kind or "").strip().lower()
            if kind and kind not in KINDS:
                raise RulesError(f"kind {kind!r}: it must be one of {', '.join(KINDS)}")
            if _fold(cname) in ALL_ALIASES or cname == ALL:
                raise RulesError(f"{ALL} is reserved and is not a consumer name")
            brief = (brief or "").strip()
            if len(brief.encode()) > MAX_BODY_BYTES:
                raise RulesError(
                    f"the brief of {cname!r} is over {MAX_BODY_BYTES} bytes: split it — "
                    "same discipline as a rule's body")
            if _fold(cname) not in [_fold(c) for c, _, _, _ in out]:
                out.append((cname, kind, brief, revive))
        return out

    def rekey_project(self, code: str) -> dict:
        """Regenerate the PAIR — code and architect key, always together, and
        GENERATED here rather than passed in: rekey used to take the new code
        from the caller, which made it the one place left where a credential
        was a person's invention. One generator, one format, one receipt —
        `create` and `rekey` hand out the same two lines. The cost, accepted:
        the two secrets break for different reasons and the pair pays for
        both — but they live in a password manager, where "I lost one" does
        not happen."""
        p = self._project(code)
        while True:
            new_code = _gen(CODE_LEN)
            if not self.cx.execute("SELECT 1 FROM projects WHERE code=?",
                                   (new_code,)).fetchone():
                break
        new_key = _gen(KEY_LEN)
        self.cx.execute("UPDATE projects SET code=?, architect_key_hash=? WHERE name=?",
                        (new_code, _key_hash(new_key), p))
        return {"project": p, "code": new_code, "architect_key": new_key,
                "rekeyed": True,
                "note": "TWO secrets, TWO destinations, shown ONCE: code to the "
                        "project instructions, key to the password manager — and "
                        "do both BEFORE closing this page: the old pair no longer "
                        "reaches anything."}

    def check_architect(self, code: str, key: str) -> str:
        """The maintenance gate, per project. It resolves the project from
        its code FIRST, then compares the key's hash on that row — in that
        order, and with ONE message for every failure, because an answer
        that told a wrong key apart from a wrong code would confirm a valid
        code to whoever holds only that. It is not session state: the pair
        travels on every call."""
        try:
            p = self._project(code)
        except RulesError:
            raise RulesError(ERR_MAINT) from None
        row = self.cx.execute("SELECT architect_key_hash FROM projects WHERE name=?",
                              (p,)).fetchone()
        if not row or not row[0] or not secrets.compare_digest(
                row[0], _key_hash(key)):
            raise RulesError(ERR_MAINT)
        return p

    def add_consumers(self, code: str, consumers) -> dict:
        """Adds consumers — and writes BRIEFS. On a consumer that already
        exists, an item carrying a brief updates it: the brief is written
        through the door that already exists, not through a new one. Removing
        a consumer stays impossible — it would orphan the rules aimed at it."""
        p = self._project(code)
        # A BRIEF IS PROSE, and prose is sanitised. It is read at the head of
        # every rules_list that consumer makes, so a relic sitting there is the
        # first thing a role reads at the start of every session — which is the
        # most effective place in the whole registry for an old identifier to
        # keep itself alive.
        cons = [(n, k, self._prose(p, f"brief of {n!r}", b), r)
                for n, k, b, r in self._normalise_consumers(consumers)]
        added, added_kinds, brief_set, kind_set, already = [], {}, [], [], []
        revived = []
        for cname, kind, brief, revive in cons:
            row = self.cx.execute(
                "SELECT id, name, kind, retired_at FROM consumers WHERE project=? "
                "AND lower(name)=?", (p, _fold(cname))).fetchone()
            if row and row["retired_at"]:
                # A RETIRED NAME IS NOT FREE AND IS NOT LIVE. Creating it again
                # would give one name two identities and two histories under the
                # same key; writing to it as if nothing happened would undo a
                # decision by accident. So the only way back is SAID: revive.
                if not revive:
                    raise RulesError(
                        f"{row['name']} was RETIRED on {row['retired_at']}. The name is "
                        "not free — the history still uses it — and it is not live "
                        "either. If that role is back, say so: pass "
                        "{'name': '" + row["name"] + "', 'revive': true}. Bringing a "
                        "consumer back is a decision, and a decision is never the "
                        "silent effect of a list of names.")
                # ONE COLUMN, and nothing to rebuild: retirement destroyed no
                # membership and no perimeter, so clearing the flag puts the
                # consumer back exactly as it was — its singleton, its groups,
                # and every rule that reached it. That is what marking instead
                # of deleting buys, and it is why reviving needs no repair pass
                # that could get it wrong.
                self.cx.execute("UPDATE consumers SET retired_at=NULL, retired_reason=NULL, "
                                "kind=?, brief=? WHERE id=?",
                                (kind or row["kind"], brief or None, row["id"]))
                revived.append(row["name"])
                continue
            if row:
                # The consumer EXISTS — under the stored spelling, whatever
                # spelling the call used. Only the brief is written; the name
                # is never touched, because spelling is data. And the no-op
                # is DECLARED, stored spelling included: an `added: []` that
                # said nothing was how a gloss went missing in silence once.
                # THE KIND IS WRITTEN HERE TOO, and it is the same defect the
                # gloss had — the field next door, found the same way. A skill
                # entered as a chat could only be repaired by creating a new
                # consumer and abandoning the old, which orphans every rule
                # aimed at it: a typo would have cost a piece of the corpus.
                #
                # Only an EXPLICIT kind moves it. A bare string says nothing
                # about the kind, so naming an existing skill in a plain list
                # leaves it a skill instead of quietly demoting it — the
                # silent write, inverted, which is what made the gloss defect
                # worth fixing rather than tolerating.
                touched = False
                if kind and kind != row["kind"]:
                    self.cx.execute("UPDATE consumers SET kind=? WHERE id=?",
                                    (kind, row["id"]))
                    kind_set.append(f"{row['name']}: {row['kind']} -> {kind}")
                    touched = True
                if brief:
                    self.cx.execute("UPDATE consumers SET brief=? WHERE id=?",
                                    (brief, row["id"]))
                    brief_set.append(row["name"])
                    touched = True
                if not touched:
                    already.append(row["name"])
                continue
            if self.cx.execute("SELECT 1 FROM scopes WHERE project=? AND lower(name)=?",
                               (p, _fold(cname))).fetchone():
                raise RulesError(
                    f"a scope named {cname!r} already exists: a consumer and a scope share "
                    "one namespace, because every consumer gets a scope with its own name")
            # The default lands HERE, on creation, which is the only place it
            # means anything: an item that said nothing about the kind is a
            # chat, and one that said nothing about an EXISTING consumer's
            # kind leaves it alone.
            self.cx.execute("INSERT INTO consumers (project, name, kind, brief, created) "
                            "VALUES (?,?,?,?,?)",
                            (p, cname, kind or "chat", brief or None, _now()))
            added.append(cname)
            added_kinds[cname] = kind or "chat"
        return {"project": p, "added": added, "added_kinds": added_kinds,
                "brief_set": brief_set, "kind_set": kind_set, "revived": revived,
                "already_there": already,
                "note": "each new one also got a scope of its own, made by the database. "
                        "A bare name is a CHAT: to declare a skill pass an object — "
                        "{'name': 'FP-Update-Tax', 'kind': 'skill'} — and the same object "
                        "on a consumer that already exists CORRECTS its kind, which is "
                        "reported in kind_set and versioned by trigger."}

    def retire_consumer(self, code: str, name: str, reason: str) -> dict:
        """END A CONSUMER. The row stays; every POINTER goes.

        This is the door the manual promised for a long time and did not have,
        and its absence made the model rigid in the one place a model must not
        be: roles end, skills get rewritten, things happen. Until v3.2.0 the
        nearest thing was to narrow every rule off a consumer by hand and
        leave it there — where it still showed in the project, and where
        `_ALL_` still reached it, so it went on being bound by every universal
        rule. That is not retired, that is invisible bookkeeping.

        IT MARKS. IT DELETES NOTHING. One column moves — `retired_at` — and
        every read excludes it from then on. Not one row of `scope_members`,
        not one row of `rule_scopes`: the relations stay exactly as they were.

        THE FIRST VERSION OF THIS METHOD DID DELETE THEM, and it was wrong in
        a way worth keeping written down, because it looked tidy. Referential
        integrity was not the problem — both are junction tables and nothing
        points at their rows. The problem is what a junction row MEANS.
        Deleting from `rule_scopes` changed the perimeter of LIVE RULES as a
        side effect of a gesture aimed at somebody else: nobody decided those
        rules should reach less, and the trigger dutifully recorded a
        perimeter decision no human had taken. And deleting from
        `scope_members` threw away the answer to "which rules used to reach
        it", leaving it reconstructable only out of the text snapshots — the
        truth migrating from a relational structure into a column of prose.

        So: mark, and filter on every read. The discipline that costs is that
        every query joining `consumers` has to exclude the retired ones —
        `_CONSUMERS_OF`, `_breadth`, `_members`, `_reaching`, `_holders`, and
        every enumeration — which is why a static check pins it rather than
        leaving it to memory.

        WHAT FOLLOWS FROM THAT:

          · the row stays and so does everything pointing at it. A rule goes
            on DECLARING the same scopes; what changed is who is on the other
            end. `rules_check` reports the ones that now reach nobody live;
          · `_ALL_` stops reaching it — one clause, because that branch
            enumerates consumers directly instead of going through membership;
          · every door that names it refuses, with a sentence that says
            RETIRED and not 'unknown' — the difference between a typo and a
            role that ended;
          · reviving is the same column back to NULL, and everything returns
            as it was: no rebuild, no repair pass, nothing to get wrong.

        OPEN TASKS BLOCK IT, and that is not caution. A task is somebody's
        work waiting: retiring its owner would make it unreachable by every
        reading, which is a `dropped` with no reason performed by
        housekeeping. Close them or hand them to somebody, then retire."""
        p = self._project(code)
        stored = self._consumer(p, name)          # refuses an already retired one
        if not (reason or "").strip():
            raise RulesError(
                "reason is mandatory: ending a role is a decision, and one with no "
                "reason gets re-taken from scratch the day somebody asks why.")
        reason = self._prose(p, "reason", reason)
        cid = self.cx.execute("SELECT id FROM consumers WHERE project=? AND name=?",
                              (p, stored)).fetchone()[0]
        open_tasks = [r[0] for r in self.cx.execute(
            "SELECT id FROM tasks WHERE project=? AND consumer_id=? AND status='pending' "
            "ORDER BY seq", (p, cid))]
        if open_tasks:
            raise RulesError(
                f"{stored} still owns {len(open_tasks)} open task(s): "
                f"{', '.join(open_tasks)}. Retiring it now would leave that work "
                "unreachable by every reading — a drop with no reason, performed by "
                "housekeeping. Close them with an outcome, drop them with a reason, or "
                "hand them to somebody else with tasks_amend.")
        # The rules aimed AT IT, read BEFORE the pointers go, so the verdict can
        # name them.
        aimed = [r[0] for r in self.cx.execute(
            "SELECT DISTINCT rs.rule_id FROM rule_scopes rs JOIN scopes s ON s.id=rs.scope_id "
            "WHERE rs.project=? AND s.managed=1 AND lower(s.name)=lower(?) ORDER BY rs.rule_id",
            (p, stored))]
        groups = [r[0] for r in self.cx.execute(
            "SELECT s.name FROM scope_members m JOIN scopes s ON s.id=m.scope_id "
            "WHERE m.consumer_id=? AND s.managed=0 ORDER BY s.name", (cid,))]
        # Read BEFORE the flag moves, because afterwards the reads exclude it —
        # which is the whole design and is also why the verdict has to be taken
        # now if it is going to name anything.
        now = _now()
        # ONE COLUMN MOVES, AND NOTHING ELSE. Not one row of scope_members and
        # not one row of rule_scopes is touched — see the docstring for why the
        # first version of this method, which deleted both, was wrong.
        self.cx.execute("UPDATE consumers SET retired_at=?, retired_reason=? WHERE id=?",
                        (now, reason, cid))
        orphaned = [rid for rid in aimed if not self._holders(p, rid)]
        return {"project": p, "retired": stored, "at": now, "reason": reason,
                "was_in_groups": groups, "losing_a_reader": aimed,
                "now_reaching_nobody_live": orphaned,
                "note": "MARKED, never deleted, and no membership and no perimeter was "
                        "touched: the relations stay whole and every read excludes it "
                        "instead. So `which rules used to reach it` is still a query, "
                        "reviving puts everything back exactly as it was, and no rule's "
                        "perimeter was re-decided by a gesture aimed at somebody else. "
                        "The rules above still DECLARE the same scopes; what changed is "
                        "who is on the other end. Those reaching nobody live are listed "
                        "and rules_check keeps listing them."}

    def add_domains(self, code: str, domains) -> dict:
        """Adds domains — and UPDATES the gloss of one that already exists,
        declaring it. The 'only adding' rule covers the LETTERS (removing a
        domain would orphan its IDs), not the glosses, which are for humans
        and correcting one orphans nothing. What a tool does not do, it says:
        the old shape returned `added: []` on an existing domain and silently
        dropped the new gloss — a verdict that read like success."""
        p = self._project(code)
        if isinstance(domains, (list, tuple)):
            domains = {d: "" for d in domains}
        added, updated = [], []
        for d, desc in (domains or {}).items():
            _valid_domain(d)
            desc = self._prose(p, f"gloss of {d}", desc)
            row = self.cx.execute("SELECT description FROM project_domains "
                                  "WHERE project=? AND domain=?", (p, d)).fetchone()
            if row:
                if desc and desc != (row["description"] or ""):
                    self.cx.execute("UPDATE project_domains SET description=? "
                                    "WHERE project=? AND domain=?", (desc, p, d))
                    updated.append(d)
                continue
            self.cx.execute("INSERT INTO project_domains (project, domain, description) "
                            "VALUES (?,?,?)", (p, d, desc or None))
            added.append(d)
        return {"project": p, "added": added, "updated": updated}

    # ---------- scopes ----------

    def _scope_id(self, project: str, scope: str):
        """Casefolded name to surrogate id, or None. The one lookup every
        scope reference goes through, so there is one idea of equality."""
        row = self.cx.execute(
            "SELECT id FROM scopes WHERE project=? AND lower(name)=?",
            (project, _fold(scope))).fetchone()
        return row[0] if row else None

    def _breadth(self, project: str, scope: str) -> int:
        """How many consumers a scope reaches. _ALL_ is not a listed set: it must
        reach consumers that do not exist yet, so its breadth is computed."""
        if scope == ALL:
            return self.cx.execute("SELECT COUNT(*) FROM consumers WHERE project=? "
                                   "AND retired_at IS NULL", (project,)).fetchone()[0]
        return self.cx.execute(
            "SELECT COUNT(*) FROM scope_members m JOIN scopes s ON s.id=m.scope_id "
            "  JOIN consumers c ON c.id=m.consumer_id "
            "WHERE s.project=? AND lower(s.name)=? AND c.retired_at IS NULL",
            (project, _fold(scope))).fetchone()[0]

    def _members(self, project: str, scope: str) -> list[str]:
        if scope == ALL:
            return [r[0] for r in self.cx.execute(
                "SELECT name FROM consumers WHERE project=? AND retired_at IS NULL "
                "ORDER BY name", (project,))]
        return [r[0] for r in self.cx.execute(
            "SELECT c.name FROM scope_members m "
            "  JOIN scopes s ON s.id=m.scope_id JOIN consumers c ON c.id=m.consumer_id "
            " WHERE s.project=? AND lower(s.name)=? AND c.retired_at IS NULL "
            " ORDER BY c.name", (project, _fold(scope)))]

    def create_scope(self, code: str, name: str, members) -> dict:
        p = self._project(code)
        name = _valid_name(name, "scope")
        if self.cx.execute("SELECT 1 FROM scopes WHERE project=? AND lower(name)=?",
                           (p, _fold(name))).fetchone():
            raise RulesError(f"a scope named {name!r} already exists")
        if self.cx.execute("SELECT 1 FROM consumers WHERE project=? AND lower(name)=?",
                           (p, _fold(name))).fetchone():
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
            sid = self.cx.execute("SELECT id FROM scopes WHERE project=? AND lower(name)=?",
                                  (p, _fold(name))).fetchone()[0]
            for m in members:
                self.cx.execute(
                    "INSERT INTO scope_members (scope_id, consumer_id) "
                    "SELECT ?, id FROM consumers WHERE project=? AND lower(name)=?",
                    (sid, p, _fold(m)))
            self.cx.execute("COMMIT")
        except Exception:
            self.cx.execute("ROLLBACK")
            raise
        return {"project": p, "scope": name, "members": members, "breadth": len(members)}

    def edit_scope(self, code: str, name: str, add=None, remove=None) -> dict:
        """Careful: this changes the perimeter of EVERY rule pointing at this
        scope. To widen a single rule use widen_rule instead."""
        p = self._project(code)
        name = _valid_name(name, "scope")
        row = self.cx.execute("SELECT id, name, managed FROM scopes "
                              "WHERE project=? AND lower(name)=?",
                              (p, _fold(name))).fetchone()
        if row is None:
            raise RulesError(f"no scope named {name!r} in this project")
        if row["managed"]:
            raise RulesError(f"{name!r} is a managed scope (a consumer singleton, or {ALL}): "
                             "its membership is fixed by construction")
        sid, name = row["id"], row["name"]
        for m in (add or []):
            self._consumer(p, m)
            self.cx.execute(
                "INSERT OR IGNORE INTO scope_members (scope_id, consumer_id) "
                "SELECT ?, id FROM consumers WHERE project=? AND lower(name)=?",
                (sid, p, _fold(m)))
        for m in (remove or []):
            self.cx.execute(
                "DELETE FROM scope_members WHERE scope_id=? AND consumer_id IN "
                "(SELECT id FROM consumers WHERE project=? AND lower(name)=?)",
                (sid, p, _fold(m)))
        n = self.cx.execute("SELECT COUNT(*) FROM rule_scopes WHERE scope_id=?",
                            (sid,)).fetchone()[0]
        return {"project": p, "scope": name, "members": self._members(p, name),
                "rules_affected": n}

    # ---------- reading rules ----------

    def _row(self, p: str, rid: str):
        return self.cx.execute("SELECT * FROM rules WHERE project=? AND id=?", (p, rid)).fetchone()

    def _scopes_of(self, p: str, rid: str) -> list[str]:
        return [r[0] for r in self.cx.execute(
            "SELECT s.name FROM rule_scopes rs JOIN scopes s ON s.id=rs.scope_id "
            " WHERE rs.project=? AND rs.rule_id=? ORDER BY s.name", (p, rid))]

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
        if row["supersedes"]:
            # On a PROPOSAL: whoever approves must see that letting this in
            # also retires that — a supersede invisible in the batch would be
            # worse than the defect it cures.
            d["supersedes"] = row["supersedes"]
        if row["denied_reason"]:
            d["denied_reason"] = row["denied_reason"]
        if why:
            d["reason"] = row["reason"]
            if row["event"]:
                d["event"] = row["event"]
        return d

    _IN_FORCE = ("status = 'active' AND (permanence = 'permanent' "
                 "OR expires_at IS NULL OR expires_at > :now)")

    @staticmethod
    def _in_force(row, now: str = "") -> bool:
        """The same predicate as _IN_FORCE, for a row already in hand: one
        definition each side of the SQL boundary, held together by the suite."""
        return (row["status"] == "active"
                and (row["permanence"] == "permanent" or not row["expires_at"]
                     or row["expires_at"] > (now or _now())))

    def _reaching(self, p: str, consumer: str) -> dict[str, tuple[int, list[str]]]:
        """rule_id -> (breadth of the widest scope it arrives through, scopes)."""
        rows = self.cx.execute(
            "SELECT rs.rule_id, sc.name AS scope FROM rule_scopes rs "
            "  JOIN scopes sc ON sc.id = rs.scope_id "
            " WHERE rs.project = :p AND (sc.name = :all OR EXISTS ("
            # No `retired_at` clause here, and its absence is deliberate: this
            # asks whether ONE NAMED consumer is a member, and a read for a
            # retired consumer never gets this far — `_consumer()` refuses the
            # name first. A filter nobody can ever see fail is not a filter,
            # and this file does not keep those.
            "   SELECT 1 FROM scope_members m JOIN consumers k ON k.id = m.consumer_id "
            "    WHERE m.scope_id = rs.scope_id AND lower(k.name) = :c))",
            {"p": p, "c": _fold(consumer), "all": ALL}).fetchall()
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
        # The BRIEF leads: "you are so-and-so, and these are your rules" is one
        # round trip, which is the reason the field exists. Empty is not an
        # error — a consumer without a mandate written down is still a
        # consumer, and skills leave it empty on purpose.
        brief = self.cx.execute("SELECT brief FROM consumers WHERE project=? AND name=?",
                                (p, c)).fetchone()[0]
        return {"project": p, "consumer": c, "brief": brief or "",
                "domains": self._legend(p, [d["id"] for d in rules]),
                "rules": rules, "count": len(rules),
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
                    if row["status"] == "retired" and row["superseded_by"]:
                        d["superseded_by"] = row["superseded_by"]
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
            "SELECT DISTINCT c.name FROM rule_scopes rs "
            "  JOIN scope_members m ON m.scope_id=rs.scope_id "
            "  JOIN consumers c ON c.id=m.consumer_id "
            " WHERE rs.project=? AND rs.rule_id=? AND c.retired_at IS NULL "
            " ORDER BY c.name", (p, rid)).fetchall()
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
                    # The renewals queue carries the WHY, and only this queue
                    # does: it is the one list read to decide, and the
                    # decision is undecidable without the reason in front of
                    # you. The waiting and denied lists keep their shape.
                    expiring.append(self._dict(row, p, why=True))
        return {"project": p, "consumer": c or "(all)",
                "waiting": waiting, "denied": denied, "expiring_within_30_days": expiring,
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
                "SELECT name FROM consumers WHERE project=? AND retired_at IS NULL "
                "ORDER BY name", (p,))]:
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
            "approval": {"provisional_days": self.provisional_days,
                         "batches_approved": q(
                             "SELECT COUNT(*) FROM approvals WHERE project=:p")},
            "registry_version": VERSION,
            "repaired_at_open": self.repaired,
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

    def expiry(self, code: str, rid: str) -> dict:
        """The expiry of ONE rule, chosen at will. Born in C5: the UI's
        detail page used to DECLARE that no method handed this out — the
        date was published only where it is decided, the expiring queue —
        and saying so out loud was the honest form of the gap. Now the
        method exists, and it hands out exactly the lifecycle fields:
        status, permanence, the date, and whether the rule is in force."""
        p = self._project(code)
        rid = _norm_id(rid)
        row = self._row(p, rid)
        if row is None:
            raise RulesError(f"{rid}: never defined in this project")
        return {"project": p, "id": rid, "status": row["status"],
                "permanence": row["permanence"], "expires_at": row["expires_at"],
                "in_force": self._in_force(row)}

    def history(self, code: str, rid: str) -> dict:
        p = self._project(code)
        rid = _norm_id(rid)
        rows = self.cx.execute(
            "SELECT version, ts, action, reason, status, permanence, scopes, consumers "
            "  FROM rule_versions WHERE project=? AND rule_id=? ORDER BY version", (p, rid)).fetchall()
        if not rows:
            raise RulesError(f"{rid}: no history — this ID was never defined in this project")
        return {"project": p, "id": rid, "versions": [dict(r) for r in rows],
                "count": len(rows)}

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

    # ---------- the reference sanitisation ----------

    SANITISED = "reference sanitisation failed"

    def _relics(self, p: str, field: str, text: str) -> None:
        """THE SANITISATION, and it runs on EVERY piece of prose an author
        writes — every field, every door, the task log included.

        It exists because of one observed behaviour: the identifiers of the old
        Markdown corpus get dragged along. Not out of carelessness — out of
        memory. Somebody who spent two years reading `VE-05` writes `VE-05`,
        and the registry this is migrating away from grows back one relic at a
        time. The manual promises those identifiers do not enter the registry
        AT ALL; before this, they entered through every field that was not a
        body, and the worst of them is `reason`, which is IMMUTABLE — a relic
        landing there could never be removed by anything, not even rules_fix.

        Two shapes are refused, and the second is the one that used to slip
        through in silence:

          · a BARE ID outside brackets of its own, in a domain this project
            declares. A forgotten bracket, or a relic; either way it must not
            be able to become a citation nobody sees;
          · an ID written with FEWER THAN FOUR DIGITS, anywhere, brackets
            included. In this registry every number is four digits, so a
            two- or three-digit one is a relic BY CONSTRUCTION. It used to be
            padded — `(VE-05)` became VE-0005 — which was a kindness when the
            two-digit era's own bodies had to keep resolving, and is now the
            most effective way to smuggle an old reference in: it does not
            fail, it silently points at a DIFFERENT rule.

        Reading still forgives a short ID, and that is not an exception: there
        it identifies a row that exists, and a person quoting from memory is
        not writing a relic into the corpus. PROSE never forgives, because
        prose is what gets stored."""
        text = text or ""
        wrong = sorted({m.group(0) for m in RE_ID_SHAPED.finditer(text)
                        if len(m.group(2)) != ID_DIGITS})
        if wrong:
            raise RulesError(
                f"{self.SANITISED} in `{field}`: {', '.join(wrong)}. There is exactly ONE "
                f"way to point at a rule here — (XX-{'N' * ID_DIGITS}), {ID_DIGITS} digits, "
                "inside round brackets — and any other number of digits is an identifier "
                "of the OLD Markdown corpus by construction. A short one used to be padded "
                "in silence, which is worse than a refusal: it did not fail, it pointed at "
                "a DIFFERENT rule and nobody was told. Nothing is deleted here and nothing "
                "is rewritten for you: say it in words — 'the old rule about mergers' — or "
                "cite by its real ID the rule that replaced it.")
        doms = set(self._domains(p))
        stray = sorted({f"{m.group(1).upper()}-{m.group(2)}"
                        for m in RE_BARE.finditer(RE_CITE.sub(" ", text))
                        if m.group(1).upper() in doms})
        if stray:
            example = stray[0]
            try:
                example = _norm_id(example)
            except RulesError:
                pass
            raise RulesError(
                f"{self.SANITISED} in `{field}`: bare ID {', '.join(stray)}. A citation "
                f"is the ID ALONE inside round brackets — ({example}) — so 'see "
                f"{stray[0]}' and '(see {stray[0]})' are both refused: in the second the "
                "brackets hold a sentence, not a pointer. Outside a bracket of its own "
                "an ID is a typo, and a typo must not be able to turn into a citation "
                "nobody sees. If you did not mean a rule at all, rewrite the token so it "
                "does not read as one of this project's IDs. There is no exception, on "
                "purpose.")

    def _prose(self, p: str, field: str, text: str) -> str:
        """A prose field of a RULE, validated the way a body is and handed back
        in its stored form. One door, no exceptions: a `reason` that could
        carry what a `body` cannot would be the hole with a different name —
        and `reason` is the field that can never be repaired."""
        if not (text or "").strip():
            return text or ""
        self._cites(p, text, field=field)
        return self._compact(text)

    def _cites(self, p: str, body: str, self_id: str = "", field: str = "body") -> list[str]:
        """Parse a body and VALIDATE its citations. Raises, so this is the door.

        It opens with `_relics`, the sanitisation every field of every door
        runs — the bare ID and the short-form relic. What is left here is the
        part that is about the CORPUS rather than about the old one:

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
        # THE SANITISATION FIRST, and before any padding happens: `_norm_id`
        # below would turn a two-digit relic into a valid-looking pointer, so
        # a check that ran after it would be looking at the cure instead of
        # the disease.
        self._relics(p, field, body)
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
        # The bare-ID hunt moved into `_relics`, which runs for every field of
        # every door instead of only for a body. Only the DECLARED domains of
        # this project are hunted there: refusing every two-letter-and-digits
        # token caught a URL path, a locale, a ticket number — things no
        # rewriting of the sentence can fix — while catching nothing extra.
        missing = [d for d in out if self._row(p, d) is None]
        if missing:
            raise RulesError(
                f"citation in `{field}` that does not resolve: {', '.join(missing)} "
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
            if row["status"] == "retired" and row["superseded_by"]:
                # The retired rule points forward, in the text, while the
                # reader is reading: the heir is one ID away.
                mark = f" · retired → superseded by {row['superseded_by']}"
            elif row["status"] != "active":
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
        """Resolve every scope reference and hand back the STORED spellings:
        what goes into a verdict is the name the owner chose, whatever
        spelling the call arrived with."""
        if not scopes:
            raise RulesError("a rule with no perimeter reaches nobody: give at least one "
                             f"scope, or {ALL} if it binds everyone")
        out = []
        for s in scopes:
            row = self.cx.execute("SELECT name FROM scopes WHERE project=? AND lower(name)=?",
                                  (p, _fold(s))).fetchone()
            if row is None:
                raise RulesError(
                    f"{s!r} is neither a consumer nor a scope of this project. "
                    "Every consumer has a scope with its own name; groups are made "
                    "with create_scope.")
            # A RETIRED CONSUMER'S SINGLETON SURVIVES ITS OWNER — the scope row
            # is managed and carries that spelling, so dropping it would mean
            # dropping a name the history still uses. It must not be a TARGET
            # though: a rule aimed there would reach nobody, quietly, which is
            # the one thing a perimeter must never do.
            dead = self.cx.execute(
                "SELECT retired_at FROM consumers WHERE project=? AND lower(name)=? "
                "AND retired_at IS NOT NULL", (p, _fold(s))).fetchone()
            if dead is not None:
                raise RulesError(
                    f"{row['name']} was RETIRED on {dead[0]}: a rule aimed at it would "
                    "reach nobody. Aim it at whoever took the work over, or leave it out.")
            out.append(row["name"])
        return out

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
        # Checked at USE as well as at declaration: a row written by hand with
        # sqlite3 never passed the declaring door, and a guarantee that only
        # holds on one door is not one.
        if d in RESERVED_DOMAINS:
            raise RulesError(
                f"domain {d!r} is RESERVED: it is the prefix of the task log, and a "
                f"rule numbered {d}-0001 could not be told apart from a task.")
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
                changelog: str = "", source: str = "", supersedes: str = "") -> dict:
        """File a proposal. It reaches NOBODY until the batch is approved — which
        is why this needs only the project code: an unapproved proposal cannot
        do harm, and a chat that deposits one stops keeping a note about it.

        THE NUMBER IS NOT A PARAMETER. You give the DOMAIN and the registry
        assigns the next number in it, four digits. A number is not a choice, it
        is a position in a sequence: whoever does not pass it cannot pick it,
        which is a structural guarantee and not a rule anybody has to remember.

        `supersedes` names the rule this proposal REPLACES — a dedicated field,
        never a citation in the body, so the registry can impose the atomicity:
        at approval, in the same transaction, the heir goes active and the
        named rule is retired pointing at it. The target must be IN FORCE, and
        only one pending proposal may claim it (a partial unique index, so it
        holds no matter which door the write came through). The heir DECLARES
        its own scopes: the supersede is the moment the perimeter gets
        re-decided, not inherited.

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
        sup = None
        if (supersedes or "").strip():
            sup = _norm_id(supersedes)
            target = self._row(p, sup)
            if target is None:
                raise RulesError(
                    f"{sup}: never defined in this project. `supersedes` must name a "
                    "rule in force — the one this proposal replaces.")
            if not self._in_force(target):
                raise RulesError(
                    f"{sup} is {target['status']} and not in force: only a rule in "
                    "force can be superseded. A defect in a living rule is rules_fix; "
                    "a rule already retired needs no heir declared after the fact.")
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
        # EVERY prose field this call carries, through the same door. The
        # `reason` is the one that made this necessary and the one that can
        # never be repaired: it is written once and no event rewrites it, so a
        # relic landing there outlives the rule itself — it survives in
        # rule_versions, in an export already taken and in a backup already
        # carried off site. Only `body` records refs; the others are validated
        # and stored, because a citation graph built out of prose would be a
        # second registry nobody asked for.
        title = self._prose(p, "title", title)
        reason = self._prose(p, "reason", reason)
        changelog = self._prose(p, "changelog", changelog)
        source = self._prose(p, "source", source)
        scopes = self._check_scopes(p, _norm_scope_list(scopes))
        if not (proposed_by or "").strip():
            raise RulesError(
                "proposed_by is mandatory: it is your own consumer name, and it is what "
                "makes the proposal YOURS. Omitted, the proposal would be an orphan — "
                "rules_pending could never show it to whoever filed it — and a silent "
                "orphan is exactly the class of error this registry refuses at the door.")
        by = self._consumer(p, proposed_by)
        # IMMEDIATE, not the default deferred: the write lock is taken BEFORE
        # the counter is read, so nobody can read the same MAX(seq) in between.
        # A plain BEGIN would upgrade from read to write halfway through, and in
        # WAL that upgrade cannot wait — the loser dies with "database is
        # locked" no matter how long the busy timeout is.
        self.cx.execute("BEGIN IMMEDIATE")
        try:
            # The ceiling on the pending queue, counted under the SAME write
            # lock as the counter: two writers racing past a Python check
            # would both see room where there is one slot. The owner reads
            # and decides in batches of 3-4 — the ceiling is that rhythm as a
            # number that refuses, and the refusal is the rhythm's whole
            # enforcement: no override, because an override would be the
            # extra proposal with extra steps. Approval and denial free the
            # slots by themselves.
            pend = self.cx.execute(
                "SELECT title FROM rules WHERE project=? AND status='proposed' "
                "ORDER BY id", (p,)).fetchall()
            if len(pend) >= self.pending_cap:
                queue = " · ".join(r[0] for r in pend)
                raise RulesError(
                    f"there are already {len(pend)} pending proposals in this project "
                    f"and the ceiling is {self.pending_cap}: wait for them to be "
                    f"approved or denied before filing more. In the queue: {queue}")
            # The number is read and taken in one go, and
            # UNIQUE(project, domain, seq) is the net underneath.
            seq = self._next_seq(p, dom)
            rid = f"{dom}-{seq:0{ID_DIGITS}d}"
            for s in scopes:
                self.cx.execute(
                    "INSERT INTO rule_scopes (project, rule_id, scope_id) "
                    "SELECT ?, ?, id FROM scopes WHERE project=? AND lower(name)=?",
                    (p, rid, p, _fold(s)))
            self.cx.execute(
                "INSERT INTO rules (project, id, domain, seq, type, title, body, status, "
                "permanence, changelog, source, reason, proposed_by, supersedes, "
                "updated_at) "
                "VALUES (?,?,?,?,?,?,?,'proposed','provisional',?,?,?,?,?,?)",
                (p, rid, dom, seq, rtype, title.strip(), body, changelog or None,
                 source or None, reason.strip(), by, sup, _now()))
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
            if "rules.project, rules.supersedes" in str(e):
                raise RulesError(
                    f"a pending proposal already supersedes {sup}: one victim, one "
                    "heir. Have that batch approved or denied first — approval and "
                    "denial free the slot by themselves.")
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
        if sup:
            out["supersedes"] = sup
            out["note"] += (f" At approval {sup} is retired in the same "
                            "transaction, pointing at this rule.")
        return out

    def batch(self, code: str) -> dict:
        """The pending batch plus its DIGEST — sha256 over the ordered list of
        IDs and bodies. You approve the batch, not the single rule: seen side
        by side, three proposals that say the same thing become visible as
        what they are.

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
        proposals = [self._dict(r, p, why=True) for r in rows]
        # The supersede arrives EXPANDED, like a citation in reading: the
        # batch is where the approver decides, and deciding to retire a rule
        # requires reading WHICH rule, not going to look an ID up. The state
        # mark matters most when it is bad news: a victim that vanished while
        # the proposal was pending is announced here, before the approval —
        # the no-op verdict after it is the receipt, not the warning.
        # `approve` does NOT read this field: it takes its (heir, victim)
        # pairs from the table, so the display can serve the person without
        # the machine parsing its own prose back.
        now = _now()
        for d in proposals:
            sup = d.get("supersedes")
            if not sup:
                continue
            victim = self._row(p, sup)
            mark = ""
            if victim["status"] == "retired" and victim["superseded_by"]:
                mark = f" · retired → superseded by {victim['superseded_by']}"
            elif victim["status"] != "active":
                mark = f" · {victim['status']}"
            elif (victim["permanence"] != "permanent" and victim["expires_at"]
                  and victim["expires_at"] <= now):
                mark = " · expired"
            d["supersedes"] = f"{sup}{GLOSS_SEP}{self._gloss(victim)}{mark}"
        return {"project": p, "count": len(ids), "ids": ids,
                "proposals": proposals,
                "digest": digest,
                # THIS NOTE USED TO DESCRIBE A CALL THAT NO LONGER EXISTS —
                # "pass this digest to approve" was 2.x text left standing when
                # rules_approve went to the UI in 3.0.0, and it sent a dry run
                # looking for a tool it could never find. Nothing caught it:
                # the check that refuses a docstring naming a tool that is gone
                # looks for the NAME, and this sentence named no tool. A
                # runtime note is spoken surface too, and it goes stale exactly
                # like a docstring.
                "note": "the digest is what the LOT PAGE of the administration UI asks "
                        "back: approval is not a tool since 3.0.0 — redacting and "
                        "promulgating stopped being the same power — so this call READS "
                        "the batch and a person approves it in the browser, against this "
                        "same digest. If a proposal arrives in between the digest changes "
                        "and the stale one is refused, which is the proof that what was "
                        "approved is the batch that was read. Denying IS still a tool: "
                        "rules_deny, which frees the slots this queue's ceiling counts."}

    def approve(self, code: str, digest: str) -> dict:
        """Approve the whole pending batch. The DIGEST is the one check left on
        this door, and it is not ceremony: it proves the approval covers the
        batch that was read, not the batch that exists now.

        A proposal that carries `supersedes` does BOTH ITS MOVES here, in the
        same transaction: the heir goes active and the named rule is retired
        pointing at it. There is no window in which both are in force, and no
        third step anybody can forget. A victim that somebody else retired
        while the proposal was pending is a DECLARED no-op: the approval goes
        through, the verdict says which supersede was skipped, and the other
        maintainer's retirement is not rewritten behind their back."""
        p = self._project(code)
        current = self.batch(code)
        if (digest or "").strip() != current["digest"]:
            raise RulesError(
                "that digest is not the current one: the batch changed after you read it "
                "(someone proposed or denied something). Ask for the batch again and "
                "re-read it. You cannot read one batch and have another approved.")
        if not current["ids"]:
            raise RulesError("nothing to approve: the batch is empty")
        expires = _plus_days(self.provisional_days)
        superseded, skipped = [], []
        self.cx.execute("BEGIN IMMEDIATE")
        try:
            # (heir, victim) from the TABLE, before the status flips: the
            # batch decorates its `supersedes` for the person reading it, and
            # a machine that parsed that prose back would break the day the
            # gloss changes shape.
            sup_pairs = [(r["id"], r["supersedes"]) for r in self.cx.execute(
                "SELECT id, supersedes FROM rules WHERE project=? AND "
                "status='proposed' AND supersedes IS NOT NULL ORDER BY id",
                (p,))]
            self._record_approval(p, current["digest"], current["ids"])
            for rid in current["ids"]:
                self.cx.execute(
                    "UPDATE rules SET status='active', permanence='provisional', "
                    "expires_at=?, event=?, updated_at=? WHERE project=? AND id=?",
                    (expires, "approved", _now(), p, rid))
            for heir, sup in sup_pairs:
                target = self._row(p, sup)
                if target is not None and self._in_force(target):
                    self.cx.execute(
                        "UPDATE rules SET status='retired', superseded_by=?, event=?, "
                        "updated_at=? WHERE project=? AND id=?",
                        (heir, f"superseded by {heir}", _now(), p, sup))
                    superseded.append({"retired": sup, "by": heir})
                else:
                    skipped.append({"id": heir, "target": sup,
                                    "why": "no longer in force"})
            self.cx.execute("COMMIT")
        except Exception:
            self.cx.execute("ROLLBACK")
            raise
        out = {"project": p, "approved": current["ids"], "count": len(current["ids"]),
               "expires_at": expires, "superseded": superseded,
               "supersede_skipped": skipped,
               "note": ("they are PROVISIONAL: unless renewed they leave the lists by "
                        "themselves. Staying costs a decision, going is free.")}
        return out

    def deny(self, code: str, ids, reason: str) -> dict:
        """No digest: denying cannot do harm. And an explicit denial turns
        silence into an answer — the chat learns instead of guessing."""
        p = self._project(code)
        if not (reason or "").strip():
            raise RulesError("a denial without a reason teaches nothing: say why")
        reason = self._prose(p, "reason", reason)
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

    def renew(self, code: str, ids, days: int = 0) -> dict:
        """Keeping a rule alive is letting it in again — which is why the
        renewal is where the corpus is governed, and why it goes behind the
        admin code in the server."""
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
        # The ORIGINAL reason, next to each rule: renewal is where the corpus
        # is governed, and "would I file this today, for the reason it was
        # filed for?" is undecidable without the reason in front of you. The
        # manual used to patch this with a habit; the tool does it now.
        reasons = {rid: self._row(p, rid)["reason"] for rid in ids}
        expires = _plus_days(int(days) or self.provisional_days)
        for rid in ids:
            self.cx.execute("UPDATE rules SET expires_at=?, event=?, updated_at=? "
                            "WHERE project=? AND id=?",
                            (expires, "renewed", _now(), p, rid))
        return {"project": p, "renewed": ids, "expires_at": expires,
                "reasons": reasons}

    def promote(self, code: str, ids) -> dict:
        """From provisional to permanent. Rare and deliberate: a permanent rule
        is one you promise to notice when it goes stale."""
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
        for rid in ids:
            self.cx.execute("UPDATE rules SET permanence='permanent', expires_at=NULL, "
                            "event=?, updated_at=? WHERE project=? AND id=?",
                            ("promoted to permanent", _now(), p, rid))
        return {"project": p, "promoted": ids}

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
        # The three prose fields this call may carry go through the same door
        # as a body. A `title` left out is left alone — same reasoning as the
        # body below: what did not arrive today is not re-judged today.
        reason = self._prose(p, "reason", reason)
        if title is not None:
            title = self._prose(p, "title", title)
        if changelog is not None:
            changelog = self._prose(p, "changelog", changelog)
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
        reason = self._prose(p, "reason", reason)
        scopes = self._check_scopes(p, _norm_scope_list(scopes))
        added = []
        for s in scopes:
            sid = self._scope_id(p, s)
            if self.cx.execute("SELECT 1 FROM rule_scopes WHERE project=? AND rule_id=? "
                               "AND scope_id=?", (p, rid, sid)).fetchone():
                continue
            self.cx.execute("INSERT INTO rule_scopes (project, rule_id, scope_id) "
                            "VALUES (?,?,?)", (p, rid, sid))
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
            n = self.cx.execute(
                "DELETE FROM rule_scopes WHERE project=? AND rule_id=? AND scope_id IN "
                "(SELECT id FROM scopes WHERE project=? AND lower(name)=?)",
                (p, rid, p, _fold(s))).rowcount
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
        reason = self._prose(p, "reason", reason)
        changelog = self._prose(p, "changelog", changelog)
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

    # ---------- the task log ----------
    #
    # Work, not law. The task log replaces both the per-role changelog and the
    # "pending" sections the role memories used to carry, and its whole point
    # is that "what is open for me?" becomes ONE query — and, because closing
    # costs an outcome, "what did I do lately?" is the same query with another
    # filter.
    #
    # It writes no file. The project's own Storia stays the long story, told
    # by whoever completes the work at the moment they complete it: the task
    # carries the short, queryable outcome, the Storia the why. Two gestures,
    # one moment.

    def _task_prose(self, p: str, field: str, text: str) -> str:
        """A task's prose: SANITISED like everything else, and not validated
        like a rule's.

        The two halves are different guarantees and it is worth keeping them
        apart. The sanitisation — no relic of the old Markdown, no bare ID —
        applies here exactly as it does to a rule, because "no old identifiers
        anywhere" means anywhere. What does NOT apply is the requirement that
        a pointer RESOLVE: a task legitimately says "propose a rule about X"
        and names something that does not exist yet, and refusing that would
        make the work log answerable to the corpus instead of the other way
        round. An unresolved pointer is reported in the text when the body is
        read.

        Compacted like a body, so a citation pasted back with its gloss is
        stored as the bare pointer and cannot carry a stale title."""
        if not (text or "").strip():
            return text or ""
        self._relics(p, field, text)
        return self._compact(text)

    def _consumer_id(self, project: str, name: str) -> tuple[int, str]:
        """The surrogate and the stored spelling, together. Every task lookup
        goes through the same casefolded resolution the rules use, so
        `architect` and `Architect` are one owner and the answer carries the
        spelling its owner chose."""
        stored = self._consumer(project, name)
        rid = self.cx.execute(
            "SELECT id FROM consumers WHERE project=? AND lower(name)=lower(?)",
            (project, stored)).fetchone()[0]
        return int(rid), stored

    @staticmethod
    def _norm_task_id(tid: str) -> str:
        """A task ID goes through the same door as a rule ID — brackets and a
        short number tolerated — and then has to BE one. `VA-0002` handed to a
        task reader is not a task that is missing, it is a rule: saying so is
        the difference between a broken citation and a wrong tool."""
        norm = _norm_id(tid)
        if not norm.startswith(TASK_PREFIX + "-"):
            raise RulesError(
                f"{norm} is not a task ID: tasks are {TASK_PREFIX}-NNNN. That looks like "
                "a RULE — read it with rules_get.")
        return norm

    def _next_task_seq(self, p: str) -> int:
        """The LAST NUMBER EVER MINTED, plus one — read from the high-water
        row, not from the rows that survive. Both are consulted and the larger
        wins: the counter is maintained by a trigger, and if somebody drops
        that trigger the surviving rows are still a floor. What must never
        happen is a number coming back."""
        alive = int(self.cx.execute(
            "SELECT IFNULL(MAX(seq), 0) FROM tasks WHERE project=?", (p,)).fetchone()[0])
        row = self.cx.execute("SELECT last FROM task_counter WHERE project=?",
                              (p,)).fetchone()
        n = max(alive, int(row[0]) if row else 0) + 1
        if n > MAX_SEQ:
            raise RulesError(f"the task log has burned all {MAX_SEQ} numbers, and IDs are "
                             "never reused: this needs a decision, not a retry")
        return n

    def _task_row(self, p: str, tid: str):
        return self.cx.execute("SELECT * FROM tasks WHERE project=? AND id=?",
                               (p, tid)).fetchone()

    @staticmethod
    def _age_days(stamp: str, now: str) -> int:
        try:
            a = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
            b = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError):
            return 0
        return max(0, (b - a).days)

    def _task_brief(self, row, now: str) -> dict:
        """THE SHORT FORM, and it is the default of every list: id, title,
        urgent, age, status. The body is read separately, by codes — a list
        that carried the bodies would make "what is open for me?" the most
        expensive question in the chat instead of the cheapest.

        `stale` appears only when it is true. It is a LABEL on a reading and
        never a lifecycle: a task does not expire, because an automatic expiry
        would be a `dropped` with no reason, written by the clock."""
        age = self._age_days(row["created_at"], now)
        out = {"id": row["id"], "title": row["title"],
               "urgent": bool(row["urgent"]),
               "consumer": row["consumer"] if "consumer" in row.keys() else None,
               "created_by": row["created_by"],
               "status": row["status"], "age_days": age}
        if out["consumer"] is None:
            out.pop("consumer")
        if row["status"] == "pending" and age >= TASKS_STALE_DAYS:
            out["stale"] = True
        if row["closed_at"]:
            out["closed_at"] = row["closed_at"]
        return out

    _TASK_SELECT = ("SELECT t.*, (SELECT c.name FROM consumers c WHERE c.id = t.consumer_id) "
                    "AS consumer FROM tasks t")

    def _order_and_cap(self, rows, now: str) -> tuple[list, int, bool]:
        """ORDERED BY THE SERVER, then cut. When the cap bites, the ORDER is
        what decides which work is lost — so the cut has to fall on the fresh
        work, which is still in mind, and never on what has gone stale, which
        is the reason the list exists at all. The client may reorder what it
        already holds; it cannot get back what was never sent.

        The truncation is always DECLARED, with the real total: without it a
        chat reads fifty and concludes it has fifty when it has a hundred and
        thirty — the incident already lived through with the vault's search."""
        total = len(rows)
        cut = rows[:TASKS_LIST_CAP]
        return [self._task_brief(r, now) for r in cut], total, total > TASKS_LIST_CAP

    def task_add(self, code: str, consumer: str, title: str, body: str,
                 created_by: str, urgent: bool = False, idem_key: str = "") -> dict:
        """Open a task for a consumer. ANYBODY in the project may open one for
        ANYBODY: that is how a coherence audit hands each correction to the
        role that owns it. `created_by` is mandatory — the lesson of
        proposed_by: omitted, the task would be orphaned in silence."""
        p = self._project(code)
        cid, owner = self._consumer_id(p, consumer)
        if not (created_by or "").strip():
            raise RulesError(
                "created_by is mandatory: it is your own consumer name, and it is what "
                "makes the task attributable. A task nobody signed is a task nobody "
                "can be asked about.")
        _, author = self._consumer_id(p, created_by)
        title = (title or "").strip()
        body = (body or "").strip()
        if not title:
            raise RulesError("the task needs a title")
        if not body:
            raise RulesError("the task needs a body: what has to be done, and enough of "
                             "the why to act on it in three weeks")
        if len(body.encode()) > MAX_BODY_BYTES:
            raise RulesError(f"the body is over {MAX_BODY_BYTES} bytes: split the task — "
                             "same discipline as a rule's body")
        title = self._task_prose(p, "title", title)
        body = self._task_prose(p, "body", body)
        key = (idem_key or "").strip()
        if key:
            # The MECHANICAL cure for duplicates, chosen over discipline
            # because discipline depends on how each skill happens to be
            # written: a recurring audit that finds the same discrepancy three
            # times must not produce three tasks. Partial on `pending`, so
            # after the task closes the same key opens a new one — finding it
            # AGAIN is a new report, not a repetition.
            row = self.cx.execute(
                "SELECT id FROM tasks WHERE project=? AND consumer_id=? AND idem_key=? "
                "AND status='pending'", (p, cid, key)).fetchone()
            if row is not None:
                return {"project": p, "id": row[0], "consumer": owner, "created": False,
                        "note": f"idempotency key {key!r} already has an OPEN task on "
                                f"{owner}: this is that task, not a second one. Once it "
                                "closes, the same key opens a new one."}
        seq = self._next_task_seq(p)
        tid = f"{TASK_PREFIX}-{seq:0{ID_DIGITS}d}"
        now = _now()
        self.cx.execute(
            "INSERT INTO tasks (project,id,seq,title,body,consumer_id,created_by,urgent,"
            "status,idem_key,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?, 'pending', ?,?,?)",
            (p, tid, seq, title, body, cid, author, 1 if urgent else 0,
             key or None, now, now))
        return {"project": p, "id": tid, "consumer": owner, "created_by": author,
                "urgent": bool(urgent), "created": True,
                "note": "cite it in prose the way rules are cited, between round "
                        f"brackets: ({tid})"}

    def task_list(self, code: str, consumer: str) -> dict:
        """What is open for you, and what you closed lately — the two halves of
        the same question. The closed half is the CHANGELOG this log replaced:
        it is there because every completion cost an outcome."""
        p = self._project(code)
        cid, owner = self._consumer_id(p, consumer)
        now = _now()
        pend = self.cx.execute(
            self._TASK_SELECT + " WHERE t.project=? AND t.consumer_id=? AND t.status='pending' "
            "ORDER BY t.urgent DESC, t.created_at ASC, t.seq ASC", (p, cid)).fetchall()
        since = _plus_days(-TASKS_RECENT_DAYS)
        closed = self.cx.execute(
            self._TASK_SELECT + " WHERE t.project=? AND t.consumer_id=? AND t.status<>'pending' "
            "AND t.closed_at >= ? ORDER BY t.closed_at DESC", (p, cid, since)).fetchall()
        p_items, p_total, p_cut = self._order_and_cap(pend, now)
        c_items, c_total, c_cut = self._order_and_cap(closed, now)
        return {"project": p, "consumer": owner,
                "pending": p_items, "pending_total": p_total, "pending_truncated": p_cut,
                "recently_closed": c_items, "recently_closed_total": c_total,
                "recently_closed_truncated": c_cut,
                "window_days": TASKS_RECENT_DAYS, "stale_after_days": TASKS_STALE_DAYS,
                "cap": TASKS_LIST_CAP,
                "note": "short form: the bodies are read by code with tasks_get, up to "
                        f"{TASKS_GET_IDS} at a time. Older closed tasks are not gone — "
                        "ask for them by date with tasks_range."}

    def task_search(self, code: str, consumer: str, query: str) -> dict:
        """Search your tasks, every state included: finding what you already
        did is the same question as finding what is open.

        Each hit carries THE FRAGMENT THAT MATCHED, because a code with no
        fragment tells you that something matched and not why — and then the
        only way on is a second call for every hit. The fragment is cut from
        what is STORED, so it shows the text as it was written; citations are
        expanded when the body is read whole, not here, where expanding would
        move the very offsets the fragment was measured on."""
        p = self._project(code)
        cid, owner = self._consumer_id(p, consumer)
        q = (query or "").strip()
        if not q:
            raise RulesError("search for what? The query is empty.")
        now = _now()
        like = f"%{q}%"
        rows = self.cx.execute(
            self._TASK_SELECT + " WHERE t.project=? AND t.consumer_id=? "
            "AND (t.title LIKE ? OR t.body LIKE ? OR IFNULL(t.outcome,'') LIKE ? "
            "OR IFNULL(t.reason,'') LIKE ?) "
            "ORDER BY t.urgent DESC, t.created_at ASC, t.seq ASC",
            (p, cid, like, like, like, like)).fetchall()
        items, total, cut = self._order_and_cap(rows, now)
        for item, row in zip(items, rows):
            item["match"] = self._fragment(q, row)
        return {"project": p, "consumer": owner, "query": q,
                "hits": items, "total": total, "truncated": cut,
                "cap": TASKS_LIST_CAP}

    @staticmethod
    def _fragment(q: str, row, width: int = 60) -> str:
        """The window around the match, whitespace collapsed, with an ellipsis
        on whichever side was cut. Searched over the fields in the order a
        reader would want them: what it is, then what came of it, then the
        body."""
        for field in ("title", "outcome", "reason", "body"):
            text = row[field] or ""
            i = text.lower().find(q.lower())
            if i < 0:
                continue
            a, b = max(0, i - width), min(len(text), i + len(q) + width)
            frag = " ".join(text[a:b].split())
            return ("…" if a > 0 else "") + frag + ("…" if b < len(text) else "")
        return ""

    def task_range(self, code: str, consumer: str, since: str, until: str,
                   on: str) -> dict:
        """The tasks of a stretch of days. `on` says WHICH DATE it filters —
        `created_at` or `closed_at` — and it has NO DEFAULT, on purpose:
        "opened in July" and "closed in July" are two different questions, and
        the changelog wants the second one. A default would answer one of them
        while the caller believed the other."""
        p = self._project(code)
        cid, owner = self._consumer_id(p, consumer)
        col = (on or "").strip().lower()
        if col not in ("created_at", "closed_at"):
            raise RulesError(
                "`on` must say which date to filter: 'created_at' (opened in that "
                "stretch) or 'closed_at' (closed in it). There is no default, because "
                "the two are different questions and the wrong one answers silently.")
        lo, hi = _day_start(since, "since"), _day_end(until, "until")
        if lo > hi:
            raise RulesError(f"the range runs backwards: {lo} is after {hi}")
        now = _now()
        rows = self.cx.execute(
            self._TASK_SELECT + f" WHERE t.project=? AND t.consumer_id=? "
            f"AND t.{col} IS NOT NULL AND t.{col} >= ? AND t.{col} <= ? "
            "ORDER BY t.urgent DESC, t.created_at ASC, t.seq ASC",
            (p, cid, lo, hi)).fetchall()
        items, total, cut = self._order_and_cap(rows, now)
        return {"project": p, "consumer": owner, "on": col, "since": lo, "until": hi,
                "tasks": items, "total": total, "truncated": cut,
                "cap": TASKS_LIST_CAP}

    def task_get(self, code: str, ids) -> dict:
        """The bodies, in a BATCH — the round trip per body is what would make
        the short form in the lists a false economy.

        TWO ceilings, and they are not the same one. The COUNT stops at
        TASKS_GET_IDS and REFUSES above it rather than truncating: a caller who
        asked for fifteen and silently got ten would act on ten. The BYTE
        ceiling is the one that actually bounds the answer — ten bodies at the
        body ceiling is 640,000 characters, far over any client's result cap —
        and there truncation is the right answer, declared, because the
        alternative is a result the client parks in a file.

        Bodies come back with their citations EXPANDED: a task that says
        `(VA-0002)` reads with that rule's current title beside it. Nothing is
        REFUSED here, unlike a rule's body — a task is prose about work, and a
        pointer that does not resolve is reported in the text, not at the
        door."""
        p = self._project(code)
        if isinstance(ids, str):
            ids = [ids]
        ids = list(ids or [])
        if not ids:
            raise RulesError("no IDs: tasks_get reads bodies by code")
        if len(ids) > TASKS_GET_IDS:
            raise RulesError(
                f"{len(ids)} IDs for a ceiling of {TASKS_GET_IDS}: the batch is refused, "
                "not trimmed — a caller who asked for more and quietly got fewer would "
                "act on the fewer. Split the call.")
        found, missing, budget, truncated = [], [], TASKS_GET_BYTES, False
        for raw in ids:
            tid = self._norm_task_id(raw)
            row = self._task_row(p, tid)
            if row is None:
                missing.append(tid)
                continue
            body = self._expand(p, row["body"])
            cost = len(body.encode())
            if found and cost > budget:
                truncated = True
                break
            budget -= cost
            item = self._task_brief(row, _now())
            item["consumer"] = self.cx.execute(
                "SELECT name FROM consumers WHERE id=?", (row["consumer_id"],)).fetchone()[0]
            item["body"] = body
            item["created_at"] = row["created_at"]
            if row["outcome"]:
                item["outcome"] = row["outcome"]
            if row["reason"]:
                item["reason"] = row["reason"]
            if row["actor"]:
                item["last_written_by"] = row["actor"]
            item["versions"] = self.cx.execute(
                "SELECT COUNT(*) FROM task_versions WHERE project=? AND task_id=?",
                (p, tid)).fetchone()[0]
            found.append(item)
        out = {"project": p, "found": found, "never_defined": missing,
               "requested": len(ids), "returned": len(found), "truncated": truncated,
               "byte_ceiling": TASKS_GET_BYTES}
        if truncated:
            out["note"] = ("the batch stopped at the byte ceiling: ask for the rest by "
                           "code. Truncated is DECLARED — a short answer that did not "
                           "say so would read as a complete one.")
        return out

    def _close_task(self, code: str, tid: str, by: str, status: str,
                    outcome: str = "", reason: str = "") -> dict:
        p = self._project(code)
        tid = self._norm_task_id(tid)
        row = self._task_row(p, tid)
        if row is None:
            raise RulesError(f"{tid}: never defined in this project")
        if row["status"] != "pending":
            raise RulesError(
                f"{tid} is already {row['status']}: a closed task is not re-closed and "
                "not reopened. Its outcome is what the log is quoted for — open a new "
                "task instead.")
        _, actor = self._consumer_id(p, by)
        outcome = self._task_prose(p, "outcome", outcome)
        reason = self._task_prose(p, "reason", reason)
        now = _now()
        self.cx.execute(
            "UPDATE tasks SET status=?, outcome=?, reason=?, actor=?, closed_at=?, "
            "updated_at=? WHERE project=? AND id=?",
            (status, outcome or None, reason or None, actor, now, now, p, tid))
        return {"project": p, "id": tid, "status": status, "by": actor, "closed_at": now}

    def task_complete(self, code: str, tid: str, outcome: str, by: str) -> dict:
        """Close a task WITH ITS OUTCOME. The outcome is mandatory and that is
        the whole design: the completed tasks with their outcomes ARE the
        consumer's changelog, and one closed without a word is an entry the
        changelog lost. Keep it short and queryable — the long story goes in
        the project's Storia, written by the same hand in the same moment."""
        if not (outcome or "").strip():
            raise RulesError(
                "outcome is mandatory on a completion: the completed tasks with their "
                "outcomes ARE the changelog of this consumer, and one closed in silence "
                "is an entry nobody can read back. One or two sentences: what came of it.")
        return self._close_task(code, tid, by, "completed", outcome=outcome.strip())

    def task_drop(self, code: str, tid: str, reason: str, by: str) -> dict:
        """Close a task WITHOUT doing it, with the reason why. Twin of denying
        a proposal: deciding not to do something is a decision, and a decision
        that leaves no reason gets re-taken from scratch the next time."""
        if not (reason or "").strip():
            raise RulesError(
                "reason is mandatory on a drop: closing without doing is a DECISION, and "
                "one with no reason will be taken again from scratch. Say why it will "
                "not be done.")
        return self._close_task(code, tid, by, "dropped", reason=reason.strip())

    def task_amend(self, code: str, tid: str, by: str, title: str = "",
                   body: str = "", consumer: str = "") -> dict:
        """Amend a task that is still OPEN: its title, its body, or its OWNER.

        Reassigning is here because a misdirected task is an ordinary event and
        not an incident: without it the only way out would be drop-and-recreate,
        which breaks the thread between the work and the request. What cannot
        move is `urgent` — the creator set it, and whoever receives it has an
        interest in clearing it."""
        p = self._project(code)
        tid = self._norm_task_id(tid)
        row = self._task_row(p, tid)
        if row is None:
            raise RulesError(f"{tid}: never defined in this project")
        if row["status"] != "pending":
            raise RulesError(
                f"{tid} is {row['status']}: a closed task is not amended. What was "
                "written when it closed is what the log is read for.")
        _, actor = self._consumer_id(p, by)
        sets, args, changed = [], [], []
        if (title or "").strip():
            sets.append("title=?")
            args.append(self._task_prose(p, "title", title.strip()))
            changed.append("title")
        if (body or "").strip():
            b = body.strip()
            if len(b.encode()) > MAX_BODY_BYTES:
                raise RulesError(f"the body is over {MAX_BODY_BYTES} bytes: split the task")
            sets.append("body=?")
            args.append(self._task_prose(p, "body", b))
            changed.append("body")
        moved_to = ""
        if (consumer or "").strip():
            cid, moved_to = self._consumer_id(p, consumer)
            sets.append("consumer_id=?"); args.append(cid); changed.append("consumer")
        if not sets:
            raise RulesError("nothing to amend: pass a title, a body or a consumer. "
                             "`urgent` is not amendable — it is the creator's.")
        now = _now()
        sets += ["actor=?", "updated_at=?"]
        args += [actor, now, p, tid]
        self.cx.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE project=? AND id=?",
                        args)
        out = {"project": p, "id": tid, "amended": changed, "by": actor}
        if moved_to:
            out["consumer"] = moved_to
        return out

    def task_overview(self, code: str) -> dict:
        """MAINTENANCE. The log across every consumer at once, which is the one
        reading a working chat has no business doing: it is how you see that a
        role is buried, or that one skill marks everything urgent.

        The urgent count is BY CREATOR on purpose. `urgent` has no ceiling and
        no levels — intermediate levels inflate and stop ordering anything —
        so the guard against inflation is visibility: if one creator's column
        is all urgent, the skill gets corrected, not the tasks.

        It also DECLARES the ceilings in force, because the day a ceiling is
        exported to the template there will be two places a number can live,
        and this says which one is commanding."""
        p = self._project(code)
        now = _now()
        per_consumer = {}
        for cid, name in self.cx.execute(
                "SELECT id, name FROM consumers WHERE project=? AND retired_at IS NULL "
                "ORDER BY name", (p,)):
            row = self.cx.execute(
                "SELECT SUM(status='pending'), SUM(status='completed'), "
                "SUM(status='dropped'), SUM(status='pending' AND urgent=1) "
                "FROM tasks WHERE project=? AND consumer_id=?", (p, cid)).fetchone()
            stale = 0
            for r in self.cx.execute("SELECT created_at FROM tasks WHERE project=? "
                                     "AND consumer_id=? AND status='pending'", (p, cid)):
                if self._age_days(r[0], now) >= TASKS_STALE_DAYS:
                    stale += 1
            per_consumer[name] = {"pending": row[0] or 0, "completed": row[1] or 0,
                                  "dropped": row[2] or 0, "urgent_open": row[3] or 0,
                                  "stale": stale}
        urgent_by_creator = {r[0]: r[1] for r in self.cx.execute(
            "SELECT created_by, COUNT(*) FROM tasks WHERE project=? AND urgent=1 "
            "GROUP BY created_by ORDER BY COUNT(*) DESC, created_by", (p,))}
        oldest = self.cx.execute(
            self._TASK_SELECT + " WHERE t.project=? AND t.status='pending' "
            "ORDER BY t.created_at ASC LIMIT 5", (p,)).fetchall()
        return {
            "project": p,
            "by_consumer": per_consumer,
            "urgent_created_by": urgent_by_creator,
            "oldest_open": [self._task_brief(r, now) for r in oldest],
            "caps_in_force": {"list": TASKS_LIST_CAP, "get_ids": TASKS_GET_IDS,
                              "get_bytes": TASKS_GET_BYTES,
                              "recent_window_days": TASKS_RECENT_DAYS,
                              "stale_after_days": TASKS_STALE_DAYS},
            "note": "the urgent count is by CREATOR because urgency is the creator's: an "
                    "inflated column is a skill to correct, not a set of tasks to "
                    "downgrade.",
        }

    def prune_tasks(self, code: str, before: str) -> dict:
        """MAINTENANCE. Remove CLOSED tasks older than a date, and only those.

        It REFUSES anything still pending, and the refusal is the point rather
        than a safety net: deleting open work by seniority is the hard expiry
        this design threw out, wearing the clothes of housekeeping. A task that
        has gone stale is closed by a person, with a reason.

        The counter is NOT rewound — the numbers stay burnt, because an ID that
        came back would make an old citation point at somebody else's work."""
        p = self._project(code)
        cutoff = _day_end(before, "before")
        open_ones = self.cx.execute(
            "SELECT COUNT(*) FROM tasks WHERE project=? AND status='pending' "
            "AND created_at <= ?", (p, cutoff)).fetchone()[0]
        rows = [r[0] for r in self.cx.execute(
            "SELECT id FROM tasks WHERE project=? AND status<>'pending' AND closed_at <= ? "
            "ORDER BY seq", (p, cutoff))]
        # The task first, then its history — in that order, because deleting
        # the task WRITES one last version (the safety net that records a hand
        # deletion), so clearing the history first would leave that row
        # behind. The counter is not touched by either: it is the one thing
        # here that only ever goes up.
        for tid in rows:
            self.cx.execute("DELETE FROM tasks WHERE project=? AND id=?", (p, tid))
            self.cx.execute("DELETE FROM task_versions WHERE project=? AND task_id=?",
                            (p, tid))
        return {"project": p, "before": cutoff, "pruned": rows,
                "left_open_untouched": open_ones,
                "note": "closed tasks only. Open ones are never pruned by age — that "
                        "would be an expiry with no reason, written by the clock — and "
                        "the counter is not rewound: the numbers stay burnt."}

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
            legend = self._legend(p, [r["id"] for r in data["rules"]])
            if legend:
                lines += [f"Domains: {self._legend_line(legend)}", ""]
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
            legend = self._legend(p, [r["id"] for r in rows])
            if legend:
                lines += [f"Domains: {self._legend_line(legend)}", ""]
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
