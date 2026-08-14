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

import contextlib
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

VERSION = "4.0.1"

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
#
# And a name is ONE WORD: the space is OUT. A consumer or a group is quoted
# exactly — in `groups=[…]`, in a chat's instructions, in the prompt of a
# scheduled task — and the space is the character you cannot see when it is
# wrong: `fidelity  advisory` with two of them reads the same on a page and
# resolves to nothing. One word makes that class of mistake unwritable
# instead of merely discouraged.
RE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,40}$")
# A PROJECT name keeps its spaces, and this is a SECOND expression rather than
# a widening of the one above on purpose: the folder is the name as it is
# spelled (`Financial Portfolio`) and the file is the slug derived from it, so
# the project side has to stay wide. One pattern serving both would have to be
# the wider of the two — which is how the narrow side quietly stops being
# enforced.
RE_PROJECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,40}$")
# What a slug may hold, one character at a time — the pattern is per character
# and not per string so a refusal can name WHICH characters were the trouble.
RE_SLUG_CHAR = re.compile(r"[a-z0-9-]")

FILE_MODE = 0o644                       # root writes, everyone else reads
DIR_MODE = 0o755
DEFAULT_PROVISIONAL_DAYS = 90
MAX_BODY_BYTES = 64_000

# The ceiling of a LIST of rules. Generous on purpose: the cap exists so that a
# runaway answer cannot eat a chat's context, not to discipline anybody. When
# it cuts, it says how many there really were.
RULES_LIST_CAP = 50

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

# The ceilings, NAMED, because a literal repeated in four queries is a number
# written four times.
#
# TASKS_LIST_CAP is the ceiling of every list of tasks. Fifty, like the rules'.
TASKS_LIST_CAP = 50
# GET_IDS is the batch of BODIES, and it is the same number for rules and for
# tasks because it is the same ceiling doing the same job — the surface
# redesign says so in as many words, and two constants would be one number
# written twice. Ten, and the arithmetic behind it was done in delivery rather
# than estimated: a body is capped at MAX_BODY_BYTES, so ten of them is 640,000
# characters — far over any client's result ceiling. The count alone therefore
# does NOT bound the answer, and the real limit is the byte one below.
GET_IDS = 10
# The byte ceiling of that same batch, and the number that actually bounds it.
# 60,000 leaves a single full-size body whole — the case where truncating would
# be useless, since there is nothing smaller to fall back to — and stops the
# batch at the first body that would cross it, declaring the truncation.
GET_BYTES = 60_000
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
# ONE message for a code that is missing and for a code that is wrong, and the
# wording has to hold both without lying about either. It used to open with
# "project not specified", which is true of the first case and MISLEADING in
# the second: somebody who mistyped a code reads that they passed no argument
# and goes to check the call instead of the code. "not recognised" covers the
# two without telling them apart — and telling them apart is the oracle this
# refusal exists to avoid. Found on the live service, 2026-Ago-14, by asking
# for a project by NAME and then by a code that does not exist: both answered
# this, and the second answer sent the reader the wrong way.
ERR_PROJECT = ("project not recognised: this needs the project CODE, the one at the top "
               "of its instructions — not its name, and not a code from somewhere else. "
               "Without one that resolves the registry does not answer, and there is no "
               "way to list projects: either you have it, or you ask for it.")

# One message for every way administration can fail to open — wrong reference
# code, wrong admin code, missing either. Which half was wrong is not said, on
# purpose: telling them apart would confirm a valid code to whoever holds only
# that. "Architect key" left the vocabulary in v4.0.0: it was this, under a
# name that said a ROLE where what is meant is a POSSESSION. The role does not
# elevate; the code does.
ERR_MAINT = ("administration refused: the project code or the admin code is missing "
             "or wrong — and which one is not said, on purpose. Administration is done "
             "by the chat Alfredo hands the admin code to at launch. Do not guess it: "
             "ask.")

# The second factor, missing. Its cure is the whole point of the sentence: a
# one-time code cannot be reasoned out, it has to be minted, and the page that
# mints it is named here so nobody goes looking.
ERR_AUTH_CODE = ("this gesture changes something that already exists, so it takes a "
                 "one-time auth_code as well as the admin code. Mint one on the "
                 "maintenance page of the administration UI — it lives minutes and it "
                 "is spent by the gesture that succeeds. No live code is not a door to "
                 "force: it is a door that is shut.")

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

# The TEMPORARY admin auth code: the second factor on every modification of
# something that already exists. Shorter than the others because its whole life
# is measured in minutes and it is read off a page and typed into a chat once —
# and out of the same alphabet, for the same reason: it gets retyped by a
# person, and I/l and O/0 are where that goes wrong.
#
# It does not exist to stop malice. A chat is not a burglar; what a chat has is
# FOGA — the pull to get the problem off its desk now — and a code somebody has
# to go and mint is a breath it cannot skip.
AUTH_CODE_LEN = 12
# Minutes. Five and not one, because with the flat ladder gestures come in
# chains and a code per gesture at one minute is a person running. Overridable
# at minting time from the page, and by ADMIN_AUTH_CODE_DURATION in the
# template — born optional with a working default HERE, because Unraid does not
# propagate a new variable to containers already installed.
DEFAULT_AUTH_CODE_MINUTES = 5


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


def _plus_minutes(minutes: int) -> str:
    return (datetime.now(timezone.utc)
            + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        if " " in s:
            # The culprit gets NAMED. `invalid name` plus a character class is
            # a refusal the reader has to decode against their own string; the
            # space is the one mistake worth spelling out, because it is the
            # one the eye does not find.
            raise RulesError(
                f"invalid {what} name {name!r}: a {what} name is ONE WORD and this one "
                "has a space in it. Names are quoted exactly — in `groups`, in a chat's "
                "instructions, in a scheduled prompt — so use '-' or '_' instead. "
                "(A PROJECT name may hold spaces; this is not one.)")
        raise RulesError(
            f"invalid {what} name {name!r}: letters, digits, '-' and '_', one word, "
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


# `_norm_scope_list` used to sit here, and it is GONE rather than carried
# along: scopes died with the v3 schema (`reach` is declared now, and the
# audience is groups plus exceptions), nothing called it, and its body read
# `ALL_ALIASES` and `ALL` — two names this module no longer defines, so the
# first call would have raised NameError. Dead code that cannot even run is
# not a spare part; it is a third caller of `_valid_name` that would have made
# the rule above look like it had an exception.


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
-- whose rows never arrived must FAIL, not quietly become universal. And what
-- these two catch is not a stranger with sqlite3 open on the share — it is a
-- BRANCH OF THIS ENGINE that writes the rule and forgets the audience. That
-- rule would reach nobody, and nobody would be told.
--
-- Rev. 8 had two more, BEFORE INSERT on the two audience tables, refusing a
-- row whose rule was not already `targeted`. They are gone, and the reason is
-- worth keeping: they never had a case to stop. On the creation path the rule
-- does not exist yet, the subquery reads NULL and they let everything through
-- by construction; on every other path they fired too early — half the
-- picture written — and between them and these two there was no legal instant
-- in which a universal rule could become targeted. `all -> targeted` is the
-- widest narrowing there is, `rules_amend` is documented to do it, and the
-- pair made it unreachable in EVERY order. These two carry the invariant on
-- their own: they fire when the picture is whole.
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

    NO REFUSAL HERE EVER ECHOES THE CONTENT OF THE LINE IT IS ABOUT. Two of
    the three fields are the project's codes, in clear, and a diagnostic
    message travels: it goes to stdout at boot, into the container log, and
    from there into whatever gets pasted somewhere else when help is asked
    for. The line number is what the person needs — they have the file open —
    and once the field count is right the name is quotable, because then it is
    the name. The count itself is diagnosed by SHAPE: a lost separator shows
    up as a first field far too long, which is exactly what somebody staring
    at the line cannot see.

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
            # The shape, never the content: the codes are on this line and the
            # count is what is wrong with it, so the lengths carry the whole
            # diagnosis — a separator lost between the name and the reference
            # code shows up as one field of sixty characters where two were
            # meant.
            #
            # The two numbers are bound to their OWN names before the message,
            # so that not one name holding content — `line`, `raw`, `parts`,
            # `ref`, `adm` — appears inside a string this function builds. That
            # is what `test_registry` checks from the AST, and it is the half
            # of the cure that outlives this branch: the raw line stays in
            # scope for the whole loop, so the next refusal written here could
            # reach it, which is how it got in the first time.
            widths = [len(p) for p in parts]
            count = len(parts)
            shape = " | ".join(f"[{w} chars]" for w in widths)
            raise RulesFault(
                f"{where} line {n}: {count} field(s) where {REGISTRY_FIELDS} are "
                f"expected. A project line is `name | reference code | admin code` "
                f"and nothing else is served — comment it out with # while you fix "
                f"it. That line, by shape: {shape}")
        name, ref, adm = parts
        if not RE_PROJECT_NAME.match(name):
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
                 auth_code_minutes: int = DEFAULT_AUTH_CODE_MINUTES) -> None:
        self.root = root
        self.file = os.path.join(root, REGISTRY_FILE)
        self.provisional_days = int(provisional_days or DEFAULT_PROVISIONAL_DAYS)
        self.auth_code_minutes = int(auth_code_minutes or DEFAULT_AUTH_CODE_MINUTES)
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
                                    auth_code_minutes=self.auth_code_minutes)
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
# The three gates
# =====================================================================
# Three credentials, two gates for the tools and one for the page:
#
#   reference code  every chat of the project — working: rules, proposals,
#                   tasks. It is `project` on every call, and it is the whole
#                   of the boundary between projects.
#   admin code      the chat Alfredo hands it to at launch — CREATING things,
#                   closing someone else's task, the cross views, and the
#                   manual in full. Travels in `key`, LAST, on every call:
#                   elevation is per call because MCP has no session and there
#                   is no `su` that persists.
#   auth_code       minted on the page, minutes to live, spent once — the
#                   second factor on every MODIFICATION of what exists.
#   ui password     the administration UI, and NO TOOL ASKS FOR IT. That is
#                   the shape of "what is catastrophic has no tool": approving
#                   is not gated from a chat, it is out of reach of one.
#
# The ladder is FLAT, and it fits in a line: creating takes the admin code,
# modifying takes the admin code plus a one-time code, proposing takes the
# reference code. A criterion with a case list grows exceptions that rot; this
# one has exactly one, declared — someone else's task, which reopens as a new
# task if it is closed wrong.


def check_admin(registry: Registry, project: str, key: str) -> Project:
    """The admin gate: a reference code that resolves AND the admin code of
    that same line.

    One refusal for both halves. A message that told them apart would confirm
    a valid reference code to whoever holds only that — and the pair is worth
    more than its halves, which is the entire reason there are two."""
    try:
        prj = registry.project(project)
    except RulesFault:
        # A registry that will not parse is not a wrong password: swallowing it
        # into ERR_MAINT would hide a broken file behind "wrong code", and
        # somebody would spend an evening retyping credentials at it.
        raise
    except RulesError:
        raise RulesError(ERR_MAINT) from None
    if not secrets.compare_digest((key or "").strip(), prj.admin_code):
        raise RulesError(ERR_MAINT)
    return prj


def _auth_row(prj: Project, auth_code: str):
    """The second factor VERIFIED and not spent: present, this project's, not
    already burned, not expired. Raises with the reason, returns the row.

    It is a function of its own because VERIFYING AND SPENDING HAPPEN AT
    DIFFERENT MOMENTS, and that is the whole of the design: the check runs
    EARLY, before anything is said about what the caller was reaching for, and
    the burn runs LATE, inside the transaction of a gesture that succeeded. Put
    them back together at either end and one of the two guarantees goes — burn
    early and a typo further down costs a trip to the page, check late and the
    refusal has already answered a question about the state."""
    given = (auth_code or "").strip()
    if not given:
        raise RulesError(ERR_AUTH_CODE)
    row = prj.cx.execute(
        "SELECT code_id, expires_at, spent_at, spent_action FROM auth_code "
        "WHERE code_hash=?", (_key_hash(given),)).fetchone()
    if row is None:
        live = prj.cx.execute("SELECT COUNT(*) FROM auth_code WHERE spent_at IS NULL "
                              "AND expires_at > ?", (_now(),)).fetchone()[0]
        raise RulesError(
            f"that auth_code is not one of this project's. {live} live right now — "
            "mint one on the maintenance page, and check you are on the right project: "
            "a code belongs to the database it was minted in, by construction.")
    if row["spent_at"]:
        raise RulesError(
            f"that auth_code was already spent on {row['spent_at']}, by "
            f"{row['spent_action']!r}. One code, one gesture: mint another.")
    if row["expires_at"] <= _now():
        raise RulesError(
            f"that auth_code expired on {row['expires_at']}. They live minutes on "
            "purpose — mint another, and mint it for the gesture you are about to "
            "make rather than in advance.")
    return row


def check_auth_code(prj: Project, auth_code: str, action: str) -> int:
    """The second factor, SPENT — and it must be called inside the gesture's
    own transaction.

    That is the whole of "burned in the same transaction as the SUCCEEDED
    gesture": the burn is written first and rolls back with everything else if
    the gesture is refused, so a typo further down the call does not cost a
    trip to the page. A code still inside its minutes and already spent is
    refused too — expiry alone would leave a spent code working, and the
    database says so as well (trg_auth_code_spent_once), because a guarantee
    that lives only in the function that checks it is a habit.

    The refusals say which of the four it was, on purpose: whoever gets here
    already holds the admin code, so there is nothing left to reveal, and the
    difference between "expired" and "already spent" is the difference between
    minting another and going to look for what spent it.

    It re-runs the verification rather than trusting the early one: the two are
    minutes apart in the worst case, and a code that expired in between must
    not be burned by a gesture that no longer had it."""
    row = _auth_row(prj, auth_code)
    prj.cx.execute("UPDATE auth_code SET spent_at=?, spent_action=? WHERE code_id=?",
                   (_now(), action, row["code_id"]))
    return row["code_id"]


def check_web(given: str, expected: str) -> bool:
    """The administration UI's password, compared in constant time.

    The expected value is HANDED IN and not read from the environment here:
    every secret of this service is read in one place, server.py, and a layer
    that reached for its own configuration would be a second place where it is
    decided. No tool calls this — and `test_surface` fixes that as a case."""
    return bool(expected) and secrets.compare_digest((given or "").strip(),
                                                     (expected or "").strip())


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
                 auth_code_minutes: int = DEFAULT_AUTH_CODE_MINUTES) -> None:
        self.name = (name or "").strip()
        self.slug = _slug(self.name)
        self.reference_code = reference_code
        self.admin_code = admin_code
        self.dir = os.path.join(root, self.name)
        self.path = os.path.join(self.dir, self.slug + ".db")
        self.provisional_days = int(provisional_days or DEFAULT_PROVISIONAL_DAYS)
        self.auth_code_minutes = int(auth_code_minutes or DEFAULT_AUTH_CODE_MINUTES)
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

    @contextlib.contextmanager
    def _transaction(self):
        """BEGIN IMMEDIATE … COMMIT, and ROLLBACK on any refusal.

        Private, and that is not tidiness: `_serialised` wraps only the public
        names, and a context manager it wrapped would take the lock while the
        generator was being BUILT and drop it before the body ran — the exact
        opposite of what it looks like. Every caller is a public method that
        already holds the lock.

        IMMEDIATE and not DEFERRED because the write lock is taken at BEGIN:
        a transaction that only discovers the database is busy halfway through
        is a transaction that fails after having decided something."""
        self.cx.execute("BEGIN IMMEDIATE")
        try:
            yield self.cx
        except BaseException:
            self.cx.execute("ROLLBACK")
            raise
        self.cx.execute("COMMIT")

    def _verify_auth(self, auth_code: str, entity: str, action: str) -> None:
        """THE SECOND FACTOR, CHECKED BEFORE ANYTHING IS SAID ABOUT THE STATE.

        Call it as the first statement of a gesture that needs one, before the
        row is looked up. Without it the order was the other way round and the
        service answered a question nobody was entitled to ask: with the admin
        code and an INVENTED one-time code, `rules_retire` on a rule that does
        not exist replied `PE-9999: never defined in this project`. The state
        came out through a door whose second lock had not been opened.

        The house rule is the ordinary one — validate every parameter, the
        credentials first, and refuse without saying anything about what was
        being reached for. It was argued once that this case was harmless
        because whoever holds the admin code can read the whole corpus anyway.
        That is true and it is not the point: it makes the ordering an argument
        to be re-made at every door, and the doors then disagree. The second
        factor does not defend against a stolen admin code, it defends against
        a distracted admin — and it can only do that if it is asked first.

        ⚠ THE TASK DOORS DO NOT DO THIS YET, and saying so here is the point of
        saying it at all. `task_close` and `task_amend` check `consumer_key` —
        which is a credential, the consumer's own secret — LAST, after the
        task's existence, its state and its owner, so `TK-0001 belongs to
        advisory` reaches somebody who has not got the secret. Found by the
        review of the same release that wrote this paragraph. It is the same
        principle and it is a change to three tools nobody asked for, so it is
        written down rather than made quietly: an exception that lives in
        somebody's head is how the doors disagree in the first place.

        ⚠ VERIFYING IS NOT BURNING. This only reads; the code is spent inside
        `_gesture`, in the transaction of the gesture that succeeded. Moving
        the burn up here would mean a refusal further down eats the code and
        sends the caller back to the page for a typo, which is exactly what the
        late burn was designed to prevent.

        It asks `port_for` rather than assuming: both call sites today are
        rule gestures that always want a code, so the condition is constant —
        but the ladder is the one place that knows, and a door that decided for
        itself is how the doors get out of step. `amend_project` does not come
        through here: it has already asked `port_for` with its `fields`, which
        this signature deliberately does not take."""
        if self.port_for(entity, action) == "auth":
            _auth_row(self, auth_code)

    @contextlib.contextmanager
    def _gesture(self, auth_code: str, action: str, needs_auth: bool):
        """A transaction with the SECOND FACTOR burned inside it.

        This is the whole of "burned in the same transaction as the SUCCEEDED
        gesture", and it is why the code travels down here instead of being
        checked at the door: every method of this class opens its own
        transaction, so a gate called by the surface would burn a code and then
        watch the gesture roll back without it — a trip to the maintenance page
        paid for a typo.

        The burn is the FIRST statement inside, so any refusal below rolls it
        back with everything else, and `needs_auth` is answered by `port_for`
        at the top of the call: the ladder is asked, never repeated."""
        with self._transaction():
            if needs_auth:
                check_auth_code(self, auth_code, action)
            yield self.cx

    # ---------- the one-time codes ----------

    def mint_auth_code(self, minutes: int = 0) -> dict:
        """Mint one. The page's gesture, behind the UI password, on the project
        it has open — which is why a code belongs to its project by
        CONSTRUCTION rather than by a check: it is a row in that database.

        The code is handed back ONCE, here. What is stored is its hash, so a
        registry file, a backup or a stolen database yields nothing that can be
        spent — and the spent rows are left where they are, because they are
        the audit of every structural gesture this project has had."""
        minutes = int(minutes or self.auth_code_minutes)
        if minutes < 1:
            raise RulesError("an auth_code that lives less than a minute is a code "
                             "nobody can carry from the page to a chat")
        code = _gen(AUTH_CODE_LEN)
        expires = _plus_minutes(minutes)
        self.cx.execute(
            "INSERT INTO auth_code (code_hash, minted_at, expires_at) VALUES (?,?,?)",
            (_key_hash(code), _now(), expires))
        return {"auth_code": code, "expires_at": expires, "minutes": minutes,
                "project": self.name,
                "note": "shown once, and once is all it is good for: it is spent by the "
                        "gesture that succeeds, and a refused gesture rolls it back."}

    def auth_codes(self) -> dict:
        """What the maintenance page shows: the live ones with their expiry,
        and the spent ones with what spent them."""
        now = _now()
        live = [dict(r) for r in self.cx.execute(
            "SELECT code_id, minted_at, expires_at FROM auth_code "
            "WHERE spent_at IS NULL AND expires_at > ? ORDER BY expires_at", (now,))]
        spent = [dict(r) for r in self.cx.execute(
            "SELECT code_id, minted_at, spent_at, spent_action FROM auth_code "
            "WHERE spent_at IS NOT NULL ORDER BY spent_at DESC LIMIT 50")]
        return {"live": live, "spent": spent, "count_live": len(live),
                "default_minutes": self.auth_code_minutes}

    def queue_cap(self) -> int | None:
        """The proposal ceiling, and it is POLICY OF THE PROJECT: NULL means
        unlimited, 0 means the queue is closed, N means N.

        It used to be two knobs in the container's template — PENDING_CAP for
        whoever writes and WEB_ACTION_CAP for whoever reads — which the batch
        page's own contract forced to be equal (unticked means denied, so a
        queue of 100 has to be approved in one go). Two numbers that must
        agree are one number, and it belongs to the project rather than to the
        container: the container is multi-tenant."""
        row = self.cx.execute("SELECT queue_cap FROM project_profile "
                              "WHERE profile_id=1").fetchone()
        return None if row is None else row["queue_cap"]

    # =================================================================
    # Resolving a name into a row
    # =================================================================
    # Every one of these answers with the STORED spelling, whatever spelling
    # the call arrived with: identity is the casefolded name, the spelling is
    # data, and what comes back in a verdict is the name its owner chose.

    def _profile_row(self):
        return self.cx.execute("SELECT * FROM project_profile "
                               "WHERE profile_id=1").fetchone()

    def _domain_codes(self) -> list[str]:
        """Every domain code this project ever declared, retired ones
        INCLUDED. Retiring a domain does not un-print the IDs it handed out,
        so a citation towards `VE-0003` has to keep resolving, and the relic
        hunt has to keep recognising `VE` as one of ours."""
        return [r[0] for r in self.cx.execute(
            "SELECT code FROM domain ORDER BY code")]

    def _domain_row(self, code: str, *, live: bool = False):
        d = (code or "").strip()
        row = self.cx.execute("SELECT * FROM domain WHERE lower(code)=?",
                              (d.lower(),)).fetchone()
        if row is None:
            declared = ", ".join(self._domain_codes()) or "none"
            raise RulesError(f"domain {d!r} is not declared by this project "
                             f"(declared: {declared})")
        if live and row["retired_at"]:
            raise RulesError(
                f"domain {row['code']} was retired on {row['retired_at']}: its numbers "
                "stay readable for ever, but nothing new is filed under it. Use a live "
                "domain, or revive that one first.")
        return row

    def _consumer_row(self, name: str, *, live: bool = True):
        n = _fold(name)
        if not n:
            raise RulesError("consumer not specified: the name is in your instructions, "
                             f"and project_info lists them all. This project has: "
                             f"{self._consumer_names() or 'none'}")
        row = self.cx.execute("SELECT * FROM consumer WHERE lower(name)=?",
                              (n,)).fetchone()
        if row is None:
            raise RulesError(
                f"{name!r} is not a consumer of this project. Live ones: "
                f"{self._consumer_names() or 'none'}. Names are not guessed — "
                "project_info reads them.")
        if live and row["retired_at"]:
            raise RulesError(
                f"{row['name']} ENDED on {row['retired_at']}"
                + (f" — {row['retired_reason']}" if row["retired_reason"] else "")
                + ". Its row stays because the history points at it, but it takes no "
                  "work and no rule. Whoever took the work over is the one to name.")
        return row

    def _consumer_names(self, live: bool = True) -> str:
        sql = "SELECT name FROM consumer"
        if live:
            sql += " WHERE retired_at IS NULL"
        return ", ".join(r[0] for r in self.cx.execute(sql + " ORDER BY name"))

    def _group_row(self, name: str, *, live: bool = True):
        n = _fold(name)
        row = self.cx.execute("SELECT * FROM consumer_group WHERE lower(name)=?",
                              (n,)).fetchone()
        if row is None:
            groups = ", ".join(r[0] for r in self.cx.execute(
                "SELECT name FROM consumer_group WHERE retired_at IS NULL "
                "ORDER BY name")) or "none"
            raise RulesError(
                f"{name!r} is not a group of this project (groups: {groups}). A single "
                "consumer is not a group: name it in `exceptions`, which is what "
                "exceptions are for.")
        if live and row["retired_at"]:
            raise RulesError(f"group {row['name']} was retired on {row['retired_at']}: "
                             "revive it, or aim at another one.")
        return row

    def _rule_row(self, rid: str):
        """A rule by its display ID. Reads forgive the short form — `VA-02`
        resolves — because there it identifies a row that EXISTS. Prose never
        forgives, and that is `_relics`' job, not this one."""
        try:
            full = _norm_id(rid)
        except RulesError:
            return None
        dom, seq = full.split("-")
        return self.cx.execute(
            "SELECT * FROM v_rule WHERE lower(display_id)=?",
            (f"{dom}-{seq}".lower(),)).fetchone()

    def _rule_by_pk(self, rule_id):
        if rule_id is None:
            return None
        return self.cx.execute("SELECT * FROM v_rule WHERE rule_id=?",
                               (rule_id,)).fetchone()

    def _display(self, rule_id) -> str:
        row = self._rule_by_pk(rule_id)
        return row["display_id"] if row is not None else ""

    # =================================================================
    # The audience: groups UNION exceptions, and it is computed
    # =================================================================

    def _audience(self, rule_id) -> dict:
        """What the rule DECLARES: the names of its groups and of its
        exceptions. The perimeter is shown, never guessed — every read of a
        rule carries this."""
        groups = [r[0] for r in self.cx.execute(
            "SELECT g.name FROM rule_audience_group a "
            "JOIN consumer_group g ON g.group_id = a.group_id "
            "WHERE a.rule_id=? ORDER BY g.name", (rule_id,))]
        exceptions = [r[0] for r in self.cx.execute(
            "SELECT c.name FROM rule_audience_exception e "
            "JOIN consumer c ON c.consumer_id = e.consumer_id "
            "WHERE e.rule_id=? ORDER BY c.name", (rule_id,))]
        return {"groups": groups, "exceptions": exceptions}

    def _live_consumer_ids(self) -> set:
        return {r[0] for r in self.cx.execute(
            "SELECT consumer_id FROM consumer WHERE retired_at IS NULL")}

    def _members_of(self, group_ids) -> set:
        """The LIVE consumers a set of groups expands to. Retired members are
        not in it: a group is a door, and a door onto nobody opens onto
        nobody."""
        if not group_ids:
            return set()
        marks = ",".join("?" * len(group_ids))
        return {r[0] for r in self.cx.execute(
            f"SELECT m.consumer_id FROM consumer_group_member m "
            f"JOIN consumer c ON c.consumer_id = m.consumer_id "
            f"WHERE m.group_id IN ({marks}) AND c.retired_at IS NULL",
            tuple(group_ids))}

    def _effective(self, rule_id, reach: str) -> set:
        """WHO this rule reaches right now. `all` is every live consumer;
        `targeted` is the union of the groups' live members and the
        exceptions.

        This is the set `rules_amend` compares — the shape is not trusted,
        because two different shapes can describe the same people and the same
        shape can describe different people a week later."""
        if reach == "all":
            return self._live_consumer_ids()
        gids = [r[0] for r in self.cx.execute(
            "SELECT group_id FROM rule_audience_group WHERE rule_id=?", (rule_id,))]
        exc = {r[0] for r in self.cx.execute(
            "SELECT e.consumer_id FROM rule_audience_exception e "
            "JOIN consumer c ON c.consumer_id = e.consumer_id "
            "WHERE e.rule_id=? AND c.retired_at IS NULL", (rule_id,))}
        return self._members_of(gids) | exc

    def _would_reach(self, reach: str, group_ids, exception_ids) -> set:
        """The same computation on a perimeter that is not written yet."""
        if reach == "all":
            return self._live_consumer_ids()
        live = self._live_consumer_ids()
        return self._members_of(group_ids) | ({int(c) for c in exception_ids} & live)

    def _containment(self, group_ids, exception_ids) -> None:
        """THE INVARIANT, checked where the rule is written.

        An exception that is already inside this rule's own groups is refused:
        either it is a mistake, or it is a tie that survives in silence the day
        that consumer leaves the group — and a perimeter nobody can read off
        the page is a perimeter that will be read wrong. Group-with-group
        overlap is allowed on purpose: the structure moves on its own, and
        forbidding it would mean revalidating every rule at every tweak. An
        exception that belongs to OTHER groups of the project is its own
        business: what counts is the perimeter of THIS rule."""
        if not (group_ids and exception_ids):
            return
        covered = self._members_of(group_ids)
        clash = [int(c) for c in exception_ids if int(c) in covered]
        if not clash:
            return
        names, doors = [], []
        for cid in clash:
            names.append(self.cx.execute("SELECT name FROM consumer WHERE consumer_id=?",
                                         (cid,)).fetchone()[0])
            marks = ",".join("?" * len(group_ids))
            doors += [r[0] for r in self.cx.execute(
                f"SELECT g.name FROM consumer_group_member m "
                f"JOIN consumer_group g ON g.group_id = m.group_id "
                f"WHERE m.consumer_id=? AND m.group_id IN ({marks})",
                (cid, *group_ids))]
        raise RulesError(
            f"{', '.join(sorted(set(names)))} "
            f"{'are' if len(set(names)) > 1 else 'is'} already inside "
            f"{', '.join(sorted(set(doors)))}, which this rule already reaches: drop the "
            "exception. An exception stands NEXT TO the groups and can only ADD — one "
            "that repeats what a group already covers is either a mistake or a tie that "
            "outlives the moment that consumer leaves the group, quietly.")

    def _reach_of(self, reach: str) -> str:
        r = (reach or "").strip().lower()
        if r not in REACH:
            raise RulesError(
                f"reach {reach!r}: it is 'all' — everyone, no audience row — or "
                "'targeted', at least one group or one exception. It is DECLARED and "
                "never deduced: a targeted rule whose perimeter never arrived must "
                "fail, not quietly bind the whole project.")
        return r

    def _resolve_audience(self, reach: str, groups, exceptions) -> tuple:
        """Names in, surrogate keys out, with the invariant already checked.

        Refuses the empty targeted perimeter here as well as in the schema: the
        trigger's message is about the arc, and this one can say which list was
        left empty."""
        reach = self._reach_of(reach)
        groups = [g for g in (groups or []) if str(g).strip()]
        exceptions = [e for e in (exceptions or []) if str(e).strip()]
        if reach == "all":
            if groups or exceptions:
                raise RulesError(
                    "reach='all' takes no group and no exception: a universal rule binds "
                    "everyone, including whoever is created tomorrow. If you meant a "
                    "perimeter, declare reach='targeted'.")
            return reach, [], []
        if not groups and not exceptions:
            raise RulesError(
                "reach='targeted' with no group and no exception reaches NOBODY. Name at "
                "least one group, or one consumer in `exceptions` — or say reach='all' "
                "if it really binds the whole project.")
        gids = [self._group_row(g)["group_id"] for g in groups]
        cids = [self._consumer_row(e)["consumer_id"] for e in exceptions]
        if len(set(gids)) != len(gids) or len(set(cids)) != len(cids):
            raise RulesError("the same group or the same consumer is named twice in the "
                             "perimeter: say it once.")
        self._containment(gids, cids)
        return reach, gids, cids

    def _write_audience(self, rule_id, gids, cids) -> None:
        """The perimeter, replaced whole. Called INSIDE the transaction and
        BEFORE the write to `rule`, always — the AFTER INSERT trigger on the
        rule photographs the audience, so an audience that arrives after the
        rule is a version 1 that photographed nobody."""
        self.cx.execute("DELETE FROM rule_audience_group WHERE rule_id=?", (rule_id,))
        self.cx.execute("DELETE FROM rule_audience_exception WHERE rule_id=?", (rule_id,))
        for g in gids:
            self.cx.execute("INSERT INTO rule_audience_group (rule_id, group_id) "
                            "VALUES (?,?)", (rule_id, g))
        for c in cids:
            self.cx.execute("INSERT INTO rule_audience_exception (rule_id, consumer_id) "
                            "VALUES (?,?)", (rule_id, c))

    def _orphaned_by(self, gone_consumers: set, *, ignore_rule=None) -> list[str]:
        """THE EMPTY GUARD (A3-a), and it names the rules.

        Given a set of consumers about to stop being reachable — retired, or
        pulled out of a group — which rules IN FORCE would be left reaching
        nobody? Those rules are still law, and law that binds nobody is a
        retirement nobody decided and nobody can find. So the gesture is
        refused, the rules are named, and they get sorted out first: retired
        properly, or given a perimeter that still means something.

        It is a REFUSAL and not a report because the damage is silent — unlike
        an overlap, which is visible in the next read of the rule."""
        stuck = []
        for row in self.cx.execute(
                "SELECT rule_id, display_id, title, reach FROM v_rule "
                "WHERE status='active'"):
            if ignore_rule is not None and row["rule_id"] == ignore_rule:
                continue
            now = self._effective(row["rule_id"], row["reach"])
            if now and not (now - gone_consumers):
                stuck.append(f"{row['display_id']} — {row['title']}")
        return stuck

    # =================================================================
    # Prose: the sanitisation, the citations, the gloss
    # =================================================================

    SANITISED = "reference sanitisation failed"

    def _relics(self, field: str, text: str) -> None:
        """THE SANITISATION, and it runs on EVERY piece of prose an author
        writes — every field, every door, the task log included.

        It exists because of one observed behaviour: the identifiers of the old
        Markdown corpus get dragged along. Not out of carelessness — out of
        memory. Somebody who spent two years reading `VE-05` writes `VE-05`,
        and the registry this is migrating away from grows back one relic at a
        time. The manual promises those identifiers do not enter the registry
        AT ALL; before this, they entered through every field that was not a
        body, and the worst of them is `reason`, which is IMMUTABLE — a relic
        landing there could never be removed by anything.

        Two shapes are refused, and the second is the one that used to slip
        through in silence:

          · a BARE ID outside brackets of its own, in a domain this project
            declares. A forgotten bracket, or a relic; either way it must not
            be able to become a citation nobody sees;
          · an ID written with FEWER THAN FOUR DIGITS, anywhere, brackets
            included. In this registry every number is four digits, so a
            two- or three-digit one is a relic BY CONSTRUCTION. It used to be
            padded — `(VE-05)` became VE-0005 — which is the most effective
            way to smuggle an old reference in: it does not fail, it silently
            points at a DIFFERENT rule.

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
        # TK IS IN THE SET, and it is not a domain: no project may declare it
        # as one, so it would never arrive from `_domain_codes`. Without it a
        # forgotten bracket around a TASK id — `see TK-0001` — went in, was
        # stored, and was seen by nobody: `dangling_citations` reads citations
        # and that is not one. It matters now more than it did, because since
        # 4.0.1 pointing at a task is the commonest citation the log has, which
        # makes the missing bracket the commonest typo.
        doms = set(self._domain_codes()) | {TASK_PREFIX}
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

    def _prose(self, field: str, text: str) -> str:
        """A prose field of a RULE, validated the way a body is and handed back
        in its stored form. One door, no exceptions: a `reason` that could
        carry what a `body` cannot would be the hole with a different name —
        and `reason` is the field that can never be repaired."""
        if not (text or "").strip():
            return text or ""
        self._cites(text, field=field)
        return self._compact(text)

    def _cites(self, body: str, field: str = "body",
               in_task: bool = False) -> list[str]:
        """Parse a body and VALIDATE its citations. Raises, so this is the door.

        It opens with `_relics`, the sanitisation every field of every door
        runs. What is left here is the part about THIS corpus rather than the
        old one:

          · a citation that does not RESOLVE — a chat cannot hallucinate a
            pointer, because the proposal does not go in;
          · a citation towards a rule that is NOT YET APPROVED;
          · a citation towards a rule that is no longer USABLE — retired, or
            superseded, and the superseded one is refused by NAMING its heir;
          · a gloss of your own inside the brackets, because what is between
            them is not stored and dropping it silently would be worse.

        THE THIRD ONE IS THE LOAD-BEARING ONE, and it is a decision about how
        the corpus is built. You may only cite a rule that has already been
        through approval, so the order of work is forced: file the cited rule,
        get it approved, then file the one that cites it. A batch whose members
        cite each other can be approved into a state where the pointers were
        only ever right at the moment they were written.

        `in_task` switches the door to the TASK LOG, and it changes two things
        because a task is not law:

          · a task may cite a TASK, which a rule may not. That is the commonest
            citation the log has, and it is refused only when it resolves to
            nothing. A CLOSED task stays citable on purpose: the rule about
            what is still usable is about FORCE, and a task never had any —
            it is a message with an outcome, and being readable afterwards is
            the whole of its value;
          · a task may cite a PROPOSED rule, because sending somebody a
            proposal and asking what they think of it is the log doing its job.

        Everything else is the same door, `denied` and `retired` included: a
        task pointing at a rule that was thrown out or taken out of force is a
        message that misinforms whoever picks it up.

        There is NO escape hatch on the bare-ID check. An exception was
        proposed once — IDs inside backticks do not count — so that a rule
        ABOUT the format of IDs could be written. A rule about how rules are
        written must not exist: that matter belongs to the manual, which a chat
        reads before writing.

        It cannot live in a trigger: SQLite has no regular expressions, and a
        trigger calling a REGEXP the application registers would fail the
        moment somebody opened the file with sqlite3 by hand."""
        body = body or ""
        # THE SANITISATION FIRST, and before any padding happens: `_norm_id`
        # below would turn a two-digit relic into a valid-looking pointer, so a
        # check that ran after it would be looking at the cure instead of the
        # disease.
        self._relics(field, body)
        out: list[str] = []
        glossed: list[tuple[str, str]] = []
        for m in RE_CITE.finditer(body):
            dst = _norm_id(m.group(1))
            if m.group(2) is not None:
                glossed.append((dst, m.group(2).strip()))
            if dst not in out:
                out.append(dst)
        cited_tasks = [d for d in out if d.startswith(TASK_PREFIX + "-")]
        rule_ids = [d for d in out if d not in cited_tasks]
        found: dict = {}
        if cited_tasks and not in_task:
            raise RulesError(
                f"a rule cites a rule, never a task: {', '.join(cited_tasks)} in `{field}`. "
                "Rules bind and tasks wait — a rule that pointed at a piece of work would "
                "be law with an expiry date nobody set. Say it in words, or cite the rule "
                "the work came from.")
        for d in cited_tasks:
            found[d] = self._task_row(d)
        gone = sorted(d for d in cited_tasks if found[d] is None)
        if gone:
            raise RulesError(
                f"citation in `{field}` that does not resolve: {', '.join(gone)} "
                f"{'are' if len(gone) > 1 else 'is'} not a task in this project. A CLOSED "
                "task may be cited — pointing back at work that is done is what the log is "
                "for, and the reader is told the state when they read it — but one that was "
                "never opened is a pointer nobody can follow.")
        for d in rule_ids:
            found[d] = self._rule_row(d)
        missing = [d for d in rule_ids if found[d] is None]
        if missing:
            raise RulesError(
                f"citation in `{field}` that does not resolve: {', '.join(missing)} "
                f"{'were' if len(missing) > 1 else 'was'} never defined in this project.")
        # A PROPOSED rule may be cited from the task log and nowhere else: asking
        # another desk what it thinks of a proposal is the log doing its job. A
        # DENIED one may be cited from neither — it was thrown out, and a pointer
        # at it hands the reader a decision that was never taken.
        blocked = ("denied",) if in_task else ("proposed", "denied")
        unborn = sorted(d for d in rule_ids if found[d]["status"] in blocked)
        if unborn:
            raise RulesError(
                f"citation towards a rule that was REFUSED: {', '.join(unborn)}. It was "
                "proposed and thrown out, so it binds nobody and never will. "
                "rules_list(pending=True) says why. An OPEN proposal may be cited from a "
                "task, so that another desk can be asked what it thinks of it; a denied one "
                "may not, from anywhere."
                if in_task else
                f"citation towards a rule that is not in force yet: {', '.join(unborn)}. "
                "You may only cite a rule that has ALREADY been approved. File the cited "
                "rule first, have it approved, then file this one — a batch whose members "
                "cite each other can be approved into a state where the pointers were only "
                "ever right at the moment they were written. If it was refused, "
                "rules_list(pending=True) says why.")
        # OUT OF FORCE, and this is the door the 4.0.1 closed. A citation points
        # at something that can still be USED; a retired rule binds nobody, so a
        # pointer at it reads as law and is not. Where the row names an heir the
        # refusal names it too — a refusal that says 'cite this one instead' is
        # actionable, and the succession is a FIELD of the row, never a citation
        # in a body, so moving the pointer loses nothing.
        dead = [d for d in rule_ids if found[d]["status"] == "retired"]
        # EXPIRED IS THE SAME QUESTION WITH A DIFFERENT ANSWER. `_in_force` is
        # this registry's own word for "binds", `project_status` counts with it
        # and `_expand` writes `· expired` when it reads one — so a door that
        # filtered on the verb `retired` instead was the only part of the
        # system that still thought a lapsed provisional was law. Same refusal,
        # different way out: a retirement is a gesture somebody made, an expiry
        # is a date passing on its own, and the rule comes back with the SAME
        # ID the moment it is renewed from the page. So it is named apart.
        lapsed = [d for d in rule_ids if found[d]["status"] == "active"
                  and not self._in_force(found[d])]
        if dead:
            bits = []
            for d in dead:
                heir = self._display(found[d]["superseded_by_rule_id"])
                bits.append(f"{d} → superseded by {heir}" if heir
                            else f"{d} → retired, and nothing replaced it")
            raise RulesError(
                f"citation in `{field}` towards a rule that is out of force: "
                f"{'; '.join(bits)}. Where an heir is named, cite that one; where none is, "
                "say it in words. A rule that has been taken out of force still reads like "
                "law when somebody follows the pointer, which is the whole reason this is "
                "refused instead of marked.")
        if lapsed:
            raise RulesError(
                f"citation in `{field}` towards a rule whose term has EXPIRED: "
                f"{', '.join(lapsed)}. It is provisional and its date has passed, so it "
                "binds nobody right now — and nobody decided that, a clock did. It is not "
                "gone: renew it from the administration page and it is in force again "
                "under the same ID, and this citation goes through. Renew it first, or "
                "say the thing in words.")
        # THE GLOSS IS CHECKED, NOT SWALLOWED. Reading hands back
        # `(VA-0002 — its title)` and pasting that straight back must work — but
        # anything else inside those brackets is the author's own words, and
        # dropping them on the way to storage would be a registry losing text in
        # silence.
        for dst, gloss in glossed:
            wanted = self._gloss(found[dst])
            if gloss == wanted or gloss.startswith(wanted + " ·"):
                continue
            what = "task" if dst.startswith(TASK_PREFIX + "-") else "rule"
            raise RulesError(
                f"the text inside ({dst} — …) is not that {what}'s title. A citation is the "
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
        day would leave a stale copy of itself inside somebody else's rule."""
        def one(m):
            try:
                return f"({_norm_id(m.group(1))})"
            except RulesError:
                return m.group(0)
        return RE_CITE.sub(one, body or "")

    def _write_refs(self, rule_id, cites: list[str]) -> int:
        """The citations, as FOREIGN KEYS. In 3.x these were text and an audit
        went hunting for pointers that pointed nowhere; the database refuses to
        write one now, so the audit is gone and `project_status` only looks at
        the prose."""
        self.cx.execute("DELETE FROM rule_ref WHERE src_rule_id=?", (rule_id,))
        n = 0
        for dst in cites:
            row = self._rule_row(dst)
            if row is None or row["rule_id"] == rule_id:
                continue
            self.cx.execute("INSERT OR IGNORE INTO rule_ref (src_rule_id, dst_rule_id) "
                            "VALUES (?,?)", (rule_id, row["rule_id"]))
            n += 1
        return n

    def _expand(self, body: str) -> str:
        """Expand every citation with the CURRENT title of the rule it points
        at.

        The gloss is NOT written, it is GENERATED — so it cannot go stale. And
        the expansion knows the STATE of the rule it points at, so a citation
        towards a retired one arrives already marked as such, in the text,
        while the chat is reading.

        It never raises: what is in the database has already passed the door,
        and a reading path that can fail is a reading path that will."""
        now = _now()

        def one(m):
            try:
                rid = _norm_id(m.group(1))
            except RulesError:
                return m.group(0)
            if rid.startswith(TASK_PREFIX + "-"):
                # A task can be cited too, and in a task's body it is the
                # commonest citation there is. It never refuses: a broken
                # pointer is NAMED in the text and reading carries on.
                trow = self.cx.execute("SELECT * FROM v_task WHERE display_id=?",
                                       (rid,)).fetchone()
                if trow is None:
                    return f"({rid}{GLOSS_SEP}⚠ no such task)"
                mark = "" if trow["status"] == "pending" else f" · {trow['status']}"
                return f"({rid}{GLOSS_SEP}{self._gloss(trow)}{mark})"
            row = self._rule_row(rid)
            if row is None:
                return f"({rid}{GLOSS_SEP}⚠ never defined)"
            mark = ""
            if row["status"] == "retired" and row["superseded_by_rule_id"]:
                mark = f" · retired → superseded by {self._display(row['superseded_by_rule_id'])}"
            elif row["status"] != "active":
                mark = f" · {row['status']}"
            elif (row["permanence"] != "permanent" and row["expires_at"]
                  and row["expires_at"] <= now):
                mark = " · expired"
            return f"({rid}{GLOSS_SEP}{self._gloss(row)}{mark})"

        return RE_CITE.sub(one, body or "")

    @staticmethod
    def _gloss(row) -> str:
        """The title as it appears inside a citation. It gives up its own round
        brackets first: a title holding a ')' would close the citation early,
        and pasting the body back would then be refused for text the registry
        itself generated — blaming the author for the writer's mistake."""
        return (row["title"] or "").replace("(", "[").replace(")", "]")

    def _task_prose(self, field: str, text: str) -> str:
        """The task log's prose goes through the same door as the corpus'. It
        was not so until 3.1.0, and the hole was found by an injection: the
        relics came back in through the titles of tasks.

        Until 4.0.1 it ran the SANITISATION alone: the shape was checked and
        the pointer was never followed, so `(PE-9999)` went in and came back
        marked at reading time and nowhere else. Now it resolves them too, with
        the log's own policy — see `_cites(in_task=True)`.

        There is ONE of these, and all three gestures come through it:
        `task_add` (title, body), `task_close` (outcome, reason) and
        `task_amend` (title, body). `outcome` is the one that would have been
        missed by fixing the doors one at a time, and it is the worst to miss:
        it is written once, at the close, and `closed is closed` means nothing
        can ever repair it."""
        if not (text or "").strip():
            return text or ""
        self._cites(text, field=field, in_task=True)
        return self._compact(text)

    # =================================================================
    # Reading
    # =================================================================

    @staticmethod
    def _in_force(row, now: str = "") -> bool:
        """Active, and not past its expiry. Permanence is checked first because
        a permanent rule has no expiry to be past."""
        if row["status"] != "active":
            return False
        if row["permanence"] == "permanent" or not row["expires_at"]:
            return True
        return row["expires_at"] > (now or _now())

    LEGEND = {
        "type": {"R": "binding rule", "M": "method", "F": "technical fact"},
        "reach": {"all": "binds every consumer of the project, present and future",
                  "targeted": "binds the union of the groups and the exceptions listed"},
        "permanence": {"provisional": "expires on the date shown unless it is promoted",
                       "permanent": "stays until it is retired or superseded"},
        "citation": f"(XX-{'N' * ID_DIGITS}) — the ID alone, inside round brackets",
    }

    def profile(self) -> dict:
        """The project talking about itself: brief, specs, queue cap.

        A project that has never been given a profile answers with nulls rather
        than with an error — a fresh database is a legitimate state, and the
        registry line that created it says so in the log."""
        row = self._profile_row()
        if row is None:
            return {"brief": None, "specs": None, "queue_cap": None,
                    "updated_at": None}
        return {"brief": row["brief"], "specs": row["specs"],
                "queue_cap": row["queue_cap"], "updated_at": row["updated_at"]}

    def project_info(self) -> dict:
        """The TECHNICAL structure of the project, and only what is ALIVE in
        it: the domains with their gloss, the consumers with kind and brief,
        the groups with their live members, and the three counts a caller
        cannot work out from the payload.

        It does two jobs, and the first one is silent: if this answers, the
        registry parsed, the file opened and the schema generation matched.
        The health probe IS the reply, which is why no separate tool asks for
        one.

        The second is the one that reads. It hands back the NAMES every other
        call expects — guessing a consumer or a group is how a proposal gets
        refused for a reason that has nothing to do with what it says.

        NO PROFILE HERE. Brief, specs, `queue_cap` and `updated_at` are what
        `rules_list` opens with, and a session start calls both: kept in the
        two, they are paid for twice.

        ONLY THE LIVE. A retired consumer stays in the database — the row is
        marked, not deleted, because `rule_version` keeps who was reached as
        text — but it is not in here. So `my name is in the list` means `my
        role is alive`, and a check a chat could forget stops existing: a door
        that is not there instead of a door that is shut. That is also why
        `retired_at` and `retired_reason` are gone from this payload, and why
        the retired have to be readable SOMEWHERE — they are, in
        `project_status`, behind the admin code, because the name stays taken
        even retired and a revive needs a target you can see."""
        domains = []
        for d in self.cx.execute("SELECT * FROM domain WHERE retired_at IS NULL "
                                 "ORDER BY code"):
            n = self.cx.execute("SELECT COUNT(*) FROM rule WHERE domain_id=? "
                                "AND status='active'", (d["domain_id"],)).fetchone()[0]
            domains.append({"code": d["code"], "description": d["description"],
                            "reason": d["reason"], "rules_in_force": n})
        consumers = []
        for c in self.cx.execute("SELECT * FROM consumer WHERE retired_at IS NULL "
                                 "ORDER BY name"):
            consumers.append({"name": c["name"], "kind": c["kind"],
                              "brief": c["brief"], "specs": c["specs"],
                              # The secret itself never leaves the database; what
                              # a caller needs to know is whether its gestures
                              # have to be signed.
                              "signed": bool(c["secret"])})
        groups = []
        for g in self.cx.execute("SELECT * FROM consumer_group WHERE retired_at IS NULL "
                                 "ORDER BY name"):
            # LIVE members only, and it is the same rule one level down: a
            # retirement deletes no junction row, so the membership of a
            # retired consumer is still sitting there. Listed here it would put
            # a dead name back in front of a chat by the side door.
            members = [r[0] for r in self.cx.execute(
                "SELECT c.name FROM consumer_group_member m "
                "JOIN consumer c ON c.consumer_id = m.consumer_id "
                "WHERE m.group_id=? AND c.retired_at IS NULL ORDER BY c.name",
                (g["group_id"],))]
            groups.append({"name": g["name"], "members": members})
        now = _now()
        in_force = sum(1 for r in self.cx.execute(
            "SELECT * FROM rule WHERE status='active'") if self._in_force(r, now))
        return {
            "project": self.name,
            "domains": domains, "consumers": consumers, "groups": groups,
            # THREE, and the three that are left are the ones the payload
            # cannot yield. The `_live` counters that used to be here were the
            # len() of the lists next to them — a number written twice, and the
            # second copy is the one that goes stale.
            "counts": {
                "rules_in_force": in_force,
                "proposed": self.cx.execute("SELECT COUNT(*) FROM rule WHERE "
                                            "status='proposed'").fetchone()[0],
                "tasks_open": self.cx.execute("SELECT COUNT(*) FROM task WHERE "
                                              "status='pending'").fetchone()[0],
            },
            "note": "everything here is ALIVE, and these are the names every other call "
                    "expects: find YOUR consumer in this list, spelled exactly, before "
                    "you go any further — if it is not here, your role is retired or "
                    "misspelt, and no other call will tell you so kindly. A consumer or "
                    "a group is READ, never guessed.",
        }

    def _reaching(self, consumer_id) -> dict:
        """Every rule in force that reaches this consumer, and BY WHICH DOOR.

        The door decides the reading order — universal first, then groups from
        the widest, then the exceptions — because that is the order in which a
        person builds the picture: what binds everybody, what binds my kind of
        work, what was aimed at me by name. Breadth is the count of LIVE
        members, computed now: a group that has emptied out sorts where it
        belongs today, not where it belonged when the rule was written."""
        out = {}
        now = _now()
        for row in self.cx.execute("SELECT * FROM v_rule WHERE status='active' "
                                   "ORDER BY domain_id, seq"):
            if not self._in_force(row, now):
                continue
            if row["reach"] == "all":
                out[row["rule_id"]] = (row, "all", 10 ** 9, ["everyone"])
                continue
            direct = self.cx.execute(
                "SELECT 1 FROM rule_audience_exception WHERE rule_id=? AND consumer_id=?",
                (row["rule_id"], consumer_id)).fetchone()
            doors = [r[0] for r in self.cx.execute(
                "SELECT g.name FROM rule_audience_group a "
                "JOIN consumer_group g ON g.group_id = a.group_id "
                "JOIN consumer_group_member m ON m.group_id = g.group_id "
                "WHERE a.rule_id=? AND m.consumer_id=? ORDER BY g.name",
                (row["rule_id"], consumer_id))]
            if direct:
                # An exception was declared BY HAND, so it is the door that gets
                # named even when a group happens to cover the same person: the
                # snapshot works the same way, and two answers that disagreed
                # about the door would be worse than either.
                out[row["rule_id"]] = (row, "exception", -1, ["by name"])
            elif doors:
                gids = [r[0] for r in self.cx.execute(
                    "SELECT a.group_id FROM rule_audience_group a WHERE a.rule_id=?",
                    (row["rule_id"],))]
                out[row["rule_id"]] = (row, "group", len(self._members_of(gids)), doors)
        return out

    def _brief(self, row, via: str = "", doors=None, fragment: str = "") -> dict:
        """A rule in short form: what a list shows. The perimeter is IN it —
        `reach` plus the names — because a line that hides who it binds is a
        line that gets quoted at the wrong person."""
        aud = self._audience(row["rule_id"])
        d = {"id": row["display_id"], "type": row["type"], "title": row["title"],
             "reach": row["reach"], "permanence": row["permanence"],
             "expires_at": row["expires_at"], "status": row["status"]}
        if row["reach"] == "targeted":
            d["groups"] = aud["groups"]
            d["exceptions"] = aud["exceptions"]
        if via:
            d["reaches_you"] = "everyone" if via == "all" else ", ".join(doors or [])
        if fragment:
            d["fragment"] = fragment
        if row["superseded_by_rule_id"]:
            d["superseded_by"] = self._display(row["superseded_by_rule_id"])
        return d

    @staticmethod
    def _fragment(q: str, text: str, width: int = 60) -> str:
        """The piece of text a query matched, with a little air around it: a
        search that answers with titles alone makes the caller open every hit."""
        t = (text or "").replace("\n", " ")
        i = t.lower().find((q or "").lower())
        if i < 0:
            return ""
        a, b = max(0, i - width // 2), min(len(t), i + len(q) + width // 2)
        return ("…" if a else "") + t[a:b].strip() + ("…" if b < len(t) else "")

    def _desk(self, consumer_id) -> dict:
        """The open tasks on this desk, in the SHORTEST form there is: id,
        title, urgent, age. Ordered the way `tasks_list` orders — urgent
        first, then the oldest — through the one sort key both call, so the
        two views cannot drift apart.

        This SUPERSEDES the two counters that used to be here, and the reason
        they were two is still the reason this line exists: session start is
        the comfortable place to add things, so it needs a rule for what does
        NOT come in. Two bare numbers were not a desk, though — they said
        whether there was post, never what it was, and the reader had to make
        a second call to find out whether it mattered.

        The new line is PROSE. Bodies stay with `tasks_get`, which carries a
        60.000-byte ceiling precisely because prose weighs: a chat that will
        never open a task pays four fields, not a document."""
        now = _now()
        rows = list(self.cx.execute(
            "SELECT * FROM v_task WHERE consumer_id=? AND status='pending' "
            "AND archived_at IS NULL", (consumer_id,)))
        ordered = sorted(rows, key=self._task_order)
        out = [{"id": r["display_id"], "title": r["title"],
                "urgent": bool(r["urgent"]),
                "age_days": self._age_days(r["created_at"], now)}
               for r in ordered[:TASKS_LIST_CAP]]
        desk = {"open": out, "open_count": len(ordered)}
        if len(ordered) > len(out):
            # Declared against the REAL total, like every other cut in here: a
            # truncated list that does not say so is a short list.
            desk["truncated"] = True
            desk["note"] = (f"{len(ordered)} open and the first {TASKS_LIST_CAP} are "
                            "here: the cut falls on the FRESH work, because the oldest "
                            "is what the desk owes. `tasks_list` for the rest, "
                            "`tasks_get` for the bodies — they are not in this call.")
        return desk

    def list_rules(self, consumer: str, query: str = "", pending: bool = False) -> dict:
        """SESSION START, in one call.

        The document a chat reads top to bottom when it wakes up. The project
        first — brief then specs, identity then the living facts — then this
        consumer's brief and specs, the legend, the rules in force for it,
        and, at the foot, the OPEN TASKS in short form. One call because the
        alternative was four, and a chat that has to make four calls before it
        can work makes three of them wrong once.

        The profile lives HERE and nowhere else: `project_info` is the
        technical half — names, and only the live ones — and the two of them
        together are a session start with nothing paid for twice.

        ⚠ EXCEPT FOR A SKILL, which does not get the project's profile at all.
        A skill executes one job; the brief and the specs are what a chat
        deliberates with. It is withheld and SAID so, never dropped in silence.

        `query` filters on title and body and hands back the matching fragment.
        `pending=True` answers with the proposal queue instead: reasons and
        proposers, which is what you look at before proposing something that is
        already in there."""
        c = self._consumer_row(consumer)
        head = {"project": self.name, "profile": self.profile(),
                "consumer": {"name": c["name"], "kind": c["kind"],
                             "brief": c["brief"], "specs": c["specs"],
                             "signed": bool(c["secret"])},
                "legend": self.LEGEND}
        if c["kind"] == "skill":
            # A SKILL DOES NOT GET THE PROJECT'S PROFILE, and this is the only
            # door it could come through: `project_info` carries no profile by
            # design and `rules_export` is behind the admin code.
            #
            # A skill runs ONE job. The project's brief and specs are its
            # identity and its living facts — the material a chat deliberates
            # with — and handing them to something that executes does two
            # unwanted things: it pays for context nobody reads, and it invites
            # improvisation from the one caller that must not improvise. Its
            # OWN brief and specs stay: that is its mandate, and it is above.
            #
            # WITHHELD, NOT MISSING. The key stays and says why. A payload that
            # simply dropped a field would look like an empty project to
            # whoever is debugging it, and the fastest way to a bad hour is a
            # silence that reads like a fault.
            head["profile"] = {
                "withheld": "skill",
                "note": "a skill does not receive the project's brief and specs: it "
                        "runs one job, and the project's identity is not part of it. "
                        "Your own brief and specs are in `consumer` — that is your "
                        "mandate. If you need something from the project's profile to "
                        "do the job, the job belongs to a chat, or the thing you need "
                        "belongs in your own brief.",
            }
        if pending:
            queue = []
            for row in self.cx.execute("SELECT * FROM v_rule WHERE status='proposed' "
                                       "ORDER BY rule_id"):
                d = self._brief(row)
                d["reason"] = row["reason"]
                d["proposed_by"] = row["proposed_by"]
                d["proposed_at"] = row["created_at"]
                if row["supersedes_rule_id"]:
                    victim = self._rule_by_pk(row["supersedes_rule_id"])
                    d["supersedes"] = f"{victim['display_id']} — {victim['title']}"
                queue.append(d)
            cap = self.queue_cap()
            head.update({"pending": queue, "count": len(queue), "queue_cap": cap,
                         "note": "approval is on the web page, never here: unticked means "
                                 "denied, and the noes are recorded with their reason."})
            return head
        q = (query or "").strip()
        rows = self._reaching(c["consumer_id"])
        ordered = sorted(rows.values(), key=lambda t: (-t[2], t[0]["display_id"]))
        out, total = [], 0
        for row, via, _breadth, doors in ordered:
            frag = ""
            if q:
                frag = self._fragment(q, row["title"]) or self._fragment(q, row["body"])
                if not frag:
                    continue
            total += 1
            if len(out) < RULES_LIST_CAP:
                out.append(self._brief(row, via, doors, frag))
        head.update({"rules": out, "count": total,
                     "truncated": total > len(out),
                     "desk": self._desk(c["consumer_id"])})
        if total > len(out):
            head["note"] = (f"{total} rules reach you and the first {len(out)} are here: "
                            "narrow with `query`, or read them by ID.")
        return head

    def _gestures(self, rule_id) -> list[dict]:
        """The history as DATED GESTURES: for every version, the date, the verb,
        the hand, and ONLY the fields that differ from the version before.

        Whole snapshots go in and the diff comes out here, which is the whole
        of decision 7 of the schema redesign: a stored delta cannot tell
        "unchanged" from "cleared", and it would fail to tell them apart
        precisely on `expires_at`. The audience is diffed too, and by NAMES —
        a photograph that answered in surrogate keys would be a photograph
        nobody can read."""
        fields = ("type", "title", "body", "status", "permanence", "expires_at",
                  "reach", "superseded_by_rule_id")
        out, prev, prev_aud = [], None, None
        for v in self.cx.execute("SELECT * FROM rule_version WHERE rule_id=? "
                                 "ORDER BY version", (rule_id,)):
            names = sorted(r[0] for r in self.cx.execute(
                "SELECT c.name FROM rule_version_audience a "
                "JOIN consumer c ON c.consumer_id = a.consumer_id "
                "WHERE a.rule_id=? AND a.version=?", (rule_id, v["version"])))
            changed = {}
            for f in fields:
                now = v[f]
                if prev is None or prev[f] != now:
                    if f == "superseded_by_rule_id":
                        if now:
                            changed["superseded_by"] = self._display(now)
                    else:
                        changed[f] = now
            g = {"version": v["version"], "timestamp": v["timestamp"],
                 "action": v["action"], "actor": v["actor"], "changed": changed}
            if v["reason"]:
                g["reason"] = v["reason"]
            if prev_aud is None:
                g["reaches"] = names
            elif names != prev_aud:
                g["reaches"] = names
                g["joined"] = sorted(set(names) - set(prev_aud))
                g["left"] = sorted(set(prev_aud) - set(names))
            g["reaches_count"] = len(names)
            out.append(g)
            prev, prev_aud = v, names
        return out

    def get_rules(self, ids, consumer: str = "", history: bool = False) -> dict:
        """Full detail, up to GET_IDS rules at a time.

        The two ceilings are of different natures on purpose: too many IDs is
        REFUSED, because a caller who asked for thirty wanted thirty and a
        silent ten would be an answer to a different question; too many BYTES
        truncates and says so, because there the caller cannot know in advance."""
        if isinstance(ids, str):
            ids = [i.strip() for i in ids.replace(",", " ").split() if i.strip()]
        ids = [str(i).strip() for i in (ids or []) if str(i).strip()]
        if not ids:
            raise RulesError("no ID given: rules_get takes the IDs of the rules to read, "
                             "up to " + str(GET_IDS) + " at a time.")
        if len(ids) > GET_IDS:
            raise RulesError(
                f"{len(ids)} IDs asked for and the ceiling is {GET_IDS}: this one is "
                "REFUSED and not trimmed, because a silent cut answers a question you did "
                "not ask. Split the batch.")
        if consumer:
            self._consumer_row(consumer)
        out, missing, size, cut = [], [], 0, False
        for rid in ids:
            row = self._rule_row(rid)
            if row is None:
                missing.append(rid)
                continue
            if cut:
                continue
            aud = self._audience(row["rule_id"])
            body = self._expand(row["body"])
            d = {"id": row["display_id"], "type": row["type"], "title": row["title"],
                 "body": body, "status": row["status"],
                 "in_force": self._in_force(row),
                 "permanence": row["permanence"], "expires_at": row["expires_at"],
                 "reach": row["reach"], "groups": aud["groups"],
                 "exceptions": aud["exceptions"],
                 "reaches_count": len(self._effective(row["rule_id"], row["reach"])),
                 "reason": row["reason"], "source": row["source"],
                 "event": row["event"], "proposed_by": row["proposed_by"],
                 "created_at": row["created_at"], "updated_at": row["updated_at"]}
            if row["supersedes_rule_id"]:
                d["supersedes"] = self._display(row["supersedes_rule_id"])
            if row["superseded_by_rule_id"]:
                d["superseded_by"] = self._display(row["superseded_by_rule_id"])
            cited_by = [r[0] for r in self.cx.execute(
                "SELECT v.display_id FROM rule_ref f JOIN v_rule v "
                "ON v.rule_id = f.src_rule_id WHERE f.dst_rule_id=? "
                "ORDER BY v.display_id", (row["rule_id"],))]
            if cited_by:
                d["cited_by"] = cited_by
            if history:
                d["history"] = self._gestures(row["rule_id"])
            size += len(str(d).encode())
            if size > GET_BYTES and out:
                cut = True
                continue
            out.append(d)
        res = {"project": self.name, "rules": out, "count": len(out)}
        if missing:
            res["not_found"] = missing
            res["note"] = ("never defined in this project: " + ", ".join(missing)
                           + ". Reading forgives a short ID — VA-02 resolves — so this is "
                             "not a formatting refusal: those rules are not here.")
        if cut:
            res["truncated"] = True
            res["note"] = (f"cut at {GET_BYTES} bytes: {len(out)} of {len(ids)} read. "
                           "Ask for the rest in a second call.")
        return res

    def expiry(self, rid: str) -> dict:
        """State, permanence, date and in-force for one rule. It exists because
        the detail page needed it and a page must not compute a lifecycle by
        itself: the engine is the one that knows when a provisional rule stops
        binding."""
        row = self._rule_row(rid)
        if row is None:
            raise RulesError(f"{rid}: never defined in this project.")
        return {"id": row["display_id"], "status": row["status"],
                "permanence": row["permanence"], "expires_at": row["expires_at"],
                "in_force": self._in_force(row)}

    # =================================================================
    # Writing rules
    # =================================================================

    def _next_seq(self, domain_id, code: str) -> int:
        """The next number in that domain, counting from the LAST ever used —
        retired and denied rows included, because an ID is never reused."""
        last = self.cx.execute("SELECT IFNULL(MAX(seq),0) FROM rule WHERE domain_id=?",
                               (domain_id,)).fetchone()[0]
        n = int(last) + 1
        if n > MAX_SEQ:
            raise RulesError(f"domain {code} has burned all {MAX_SEQ} numbers: it needs a "
                             "new domain, because IDs are never reused.")
        return n

    def _check_consumer_key(self, row, consumer_key: str, *, admin: bool = False) -> None:
        """A consumer's identity is declarative until somebody decides it is
        not. `secret` NULL means the name is enough — which is the truth of
        today, and pretending otherwise would be theatre. Set, and every
        gesture in that consumer's name carries `consumer_key`.

        The admin code goes over the top of it: whoever holds that already has
        more than this protects."""
        if admin or not row["secret"]:
            return
        if not secrets.compare_digest((consumer_key or "").strip(), row["secret"]):
            raise RulesError(
                f"{row['name']} signs its gestures: this call needs `consumer_key`, the "
                "secret in that consumer's own instructions. It was switched on for this "
                "consumer alone — the others still go by name.")

    def propose(self, domain: str, rtype: str, title: str, body: str, reason: str,
                reach: str, proposed_by: str, groups=None, exceptions=None,
                supersedes: str = "", source: str = "",
                consumer_key: str = "", admin: bool = False) -> dict:
        """File a proposal. It reaches NOBODY until a person approves it on the
        page, which is why this needs only the reference code: an unapproved
        proposal cannot do harm, and a chat that deposits one stops carrying a
        note about it.

        Everything is validated BEFORE anything is written — the perimeter
        resolved, the citations checked, the prose sanitised — so a refusal
        spends neither a number nor a place in the queue.

        `supersedes` names the rule this one REPLACES: a field of its own,
        never a citation in the body, so the registry can impose the atomicity
        — at approval, in the same transaction, the heir goes active and the
        named rule is retired pointing at it. Changing a decision is ONE
        gesture."""
        dom = self._domain_row(domain, live=True)
        if dom["code"] in RESERVED_DOMAINS:
            raise RulesError(
                f"domain {dom['code']!r} is RESERVED: it is the prefix of the task log, "
                f"and a rule numbered {dom['code']}-0001 could not be told apart from a "
                "task.")
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
        author = (proposed_by or "").strip()
        if not author:
            raise RulesError(
                "proposed_by is required: an unsigned proposal is an orphan, and whoever "
                "reads the queue has to know who to ask. It is a signature — the name of "
                "the consumer, or of a person.")
        signer = self.cx.execute("SELECT * FROM consumer WHERE lower(name)=?",
                                 (_fold(author),)).fetchone()
        if signer is not None:
            self._check_consumer_key(signer, consumer_key, admin=admin)

        cap = self.queue_cap()
        if cap is not None:
            n = self.cx.execute("SELECT COUNT(*) FROM rule "
                                "WHERE status='proposed'").fetchone()[0]
            if cap == 0:
                raise RulesError(
                    "the proposal queue is CLOSED on this project (queue_cap 0). Nothing "
                    "is lost by waiting: say it to whoever administers the project.")
            if n >= cap:
                raise RulesError(
                    f"the queue already holds {n} proposals and the ceiling is {cap}. The "
                    "ceiling is there so that whoever approves reads what they tick: the "
                    "queue has to be decided before it grows. Titles waiting: "
                    + "; ".join(r[0] for r in self.cx.execute(
                        "SELECT title FROM rule WHERE status='proposed' ORDER BY rule_id")))

        victim = None
        if (supersedes or "").strip():
            sup = _norm_id(supersedes)
            victim = self._rule_row(sup)
            if victim is None:
                raise RulesError(
                    f"{sup}: never defined in this project. `supersedes` must name a rule "
                    "in force — the one this proposal replaces.")
            if not self._in_force(victim):
                raise RulesError(
                    f"{sup} is {victim['status']} and not in force: only a rule in force "
                    "can be superseded. A rule already retired needs no heir declared "
                    "after the fact.")
            claimed = self.cx.execute(
                "SELECT v.display_id, v.title FROM v_rule v WHERE v.status='proposed' "
                "AND v.supersedes_rule_id=?", (victim["rule_id"],)).fetchone()
            if claimed is not None:
                raise RulesError(
                    f"{sup} is already claimed by the pending proposal {claimed[0]} — "
                    f"{claimed[1]!r}. Two heirs for one rule is a batch that decides by "
                    "order of approval: settle that one first.")

        # Validated on WHAT ARRIVED, then compacted, then measured. The order is
        # the whole safety of it: compacting first would drop a gloss before the
        # bare-ID check could look at it, so a body could lose a pointer and a
        # sentence without anybody being told. Measured last, because the
        # ceiling has to be about what actually goes into the database.
        cites = self._cites(body)
        body = self._compact(body)
        if len(body.encode()) > MAX_BODY_BYTES:
            raise RulesError(f"the body is {len(body.encode())} bytes and the ceiling is "
                             f"{MAX_BODY_BYTES}: a rule that long is two rules.")
        title = self._prose("title", title)
        reason = self._prose("reason", reason)
        source = self._prose("source", source)
        reach, gids, cids = self._resolve_audience(reach, groups, exceptions)

        with self._transaction():
            rule_id = self.cx.execute(
                "SELECT IFNULL(MAX(rule_id),0)+1 FROM rule").fetchone()[0]
            # THE AUDIENCE FIRST. The two references are DEFERRED for exactly
            # this: the AFTER INSERT trigger on `rule` photographs the
            # perimeter, so a perimeter written afterwards is a version 1 that
            # photographed nobody — and nobody would complain.
            self._write_audience(rule_id, gids, cids)
            now = _now()
            self.cx.execute(
                "INSERT INTO rule (rule_id, domain_id, seq, type, title, body, status, "
                "permanence, reach, supersedes_rule_id, source, reason, event, "
                "proposed_by, actor, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,'proposed','provisional',?,?,?,?,?,?,?,?,?)",
                (rule_id, dom["domain_id"], self._next_seq(dom["domain_id"], dom["code"]),
                 rtype, title, body, reach,
                 victim["rule_id"] if victim is not None else None,
                 source or None, reason, "proposed", author, author, now, now))
            self._write_refs(rule_id, cites)
            display = self._display(rule_id)
        out = {"id": display, "status": "proposed", "reach": reach,
               "reaches": len(self._would_reach(reach, gids, cids)),
               "queued": self.cx.execute("SELECT COUNT(*) FROM rule "
                                         "WHERE status='proposed'").fetchone()[0],
               "note": "it binds nobody until a person approves it on the page. The "
                       "outcome is in rules_list(pending=True)."}
        if victim is not None:
            out["supersedes"] = victim["display_id"]
        if cites:
            out["cites"] = cites
        return out

    def amend_rule(self, rid: str, reach: str, groups, exceptions,
                   expected_version: int, reason: str, actor: str = "",
                   auth_code: str = "") -> dict:
        """THE PERIMETER of a rule in force, NARROWED, as one atomic gesture.

        The new effective set of consumers — the union of the groups and the
        exceptions — must be CONTAINED in the old one. Both are computed and
        compared: the shape is not trusted, because two shapes can describe the
        same people and one shape can describe different people a week later.

        And never to zero. A narrowing that leaves nobody is a retirement in
        disguise, and retirement is the least reversible gesture on this
        surface — it does not get to happen through the side door.

        Widening binds somebody new, which is promulgation, and promulgation
        goes through the page: propose a supersede with the wider audience. The
        CONTENT is not touched from here either — a rule that must SAY
        something else is a new decision."""
        self._verify_auth(auth_code, "rule", "amend")
        row = self._rule_row(rid)
        if row is None:
            raise RulesError(f"{rid}: never defined in this project.")
        if not self._in_force(row):
            raise RulesError(
                f"{row['display_id']} is {row['status']} and not in force: there is no "
                "perimeter to narrow. A proposal changes by being withdrawn and filed "
                "again; a retired rule comes back through a proposal.")
        if not (reason or "").strip():
            raise RulesError("reason is required: a perimeter that shrank without a "
                             "sentence is a change nobody can explain six months later.")
        current = self.cx.execute("SELECT IFNULL(MAX(version),0) FROM rule_version "
                                  "WHERE rule_id=?", (row["rule_id"],)).fetchone()[0]
        if int(expected_version or 0) != current:
            raise RulesError(
                f"{row['display_id']} is at version {current} and you wrote against "
                f"{expected_version}: somebody changed it after you read it. Read it "
                "again — rules_get(history=True) says what moved — and decide on what "
                "is there now.")
        reason = self._prose("reason", reason)
        new_reach, gids, cids = self._resolve_audience(reach, groups, exceptions)
        before = self._effective(row["rule_id"], row["reach"])
        after = self._would_reach(new_reach, gids, cids)
        if not after:
            raise RulesError(
                "this narrowing leaves NO consumer: that is a retirement in disguise, and "
                "the way out is rules_retire — which costs a reason and a one-time code, "
                "on purpose. A rule in force that binds nobody is a decision nobody took "
                "and nobody can find.")
        grown = after - before
        if grown:
            names = ", ".join(sorted(
                self.cx.execute("SELECT name FROM consumer WHERE consumer_id=?",
                                (c,)).fetchone()[0] for c in grown))
            raise RulesError(
                f"this is not a narrowing: it would newly bind {names}. Widening is "
                "PROMULGATION — it puts an obligation on somebody who did not have it — "
                "and it goes through the page: propose a supersede carrying the wider "
                "audience, and let the approval retire this one in the same decision.")
        with self._gesture(auth_code, "rule.amend",
                           self.port_for("rule", "amend") == "auth"):
            self._write_audience(row["rule_id"], gids, cids)
            self.cx.execute(
                "UPDATE rule SET reach=?, event=?, actor=?, updated_at=? WHERE rule_id=?",
                (new_reach, reason, (actor or "").strip() or None, _now(),
                 row["rule_id"]))
        left = sorted(self.cx.execute("SELECT name FROM consumer WHERE consumer_id=?",
                                      (c,)).fetchone()[0] for c in (before - after))
        return {"id": row["display_id"], "reach": new_reach,
                "version": current + 1,
                "reaches": len(after), "no_longer_reaches": left,
                "note": "narrowed. The action in the history stays 'amended' — narrowing "
                        "a targeted rule leaves `reach` where it was, so the verb is not "
                        "one the database can derive — and the reason is next to a "
                        "snapshot that shows the audience shrink."}

    def retire(self, rid: str, reason: str, actor: str = "",
               superseded_by=None, auth_code: str = "") -> dict:
        """End a rule without an heir. With an heir the road is the supersede,
        which retires the victim inside the same decision — this is for the rule
        that simply stops applying."""
        self._verify_auth(auth_code, "rule", "retire")
        row = self._rule_row(rid)
        if row is None:
            raise RulesError(f"{rid}: never defined in this project.")
        if row["status"] == "retired":
            raise RulesError(f"{row['display_id']} was already retired on "
                             f"{row['updated_at']}.")
        if not self._in_force(row) and row["status"] != "active":
            raise RulesError(
                f"{row['display_id']} is {row['status']}: only a rule in force is "
                "retired. A proposal is denied on the page, not retired here.")
        if not (reason or "").strip():
            raise RulesError("reason is the price of a retirement: a rule that disappears "
                             "without one comes back as an argument.")
        reason = self._prose("reason", reason)
        with self._gesture(auth_code, "rule.retire",
                           self.port_for("rule", "retire") == "auth"):
            self.cx.execute(
                "UPDATE rule SET status='retired', event=?, actor=?, "
                "superseded_by_rule_id=?, updated_at=? WHERE rule_id=?",
                (reason, (actor or "").strip() or None, superseded_by, _now(),
                 row["rule_id"]))
        out = {"id": row["display_id"], "status": "retired", "reason": reason}
        if superseded_by:
            out["superseded_by"] = self._display(superseded_by)
        return out

    # =================================================================
    # The batch page: one gesture, two verdicts
    # =================================================================

    def _digest(self, rows) -> str:
        """The fingerprint of WHAT WAS LOOKED AT. It covers the ID and the last
        write of every proposal in the queue, so a proposal that arrives — or
        changes — between the reading and the tick makes the digest stale and
        the whole gesture is refused. Nothing is approved that nobody read."""
        material = "|".join(f"{r['display_id']}@{r['updated_at']}" for r in rows)
        return hashlib.sha256(material.encode()).hexdigest()

    def batch(self) -> dict:
        """The queue as the page shows it: the WHOLE lot, never a page of it,
        because unticked means denied and a lot cut in half would deny the
        other half.

        THREE ROWS per proposal, and the third is the one that was missing:

          · the perimeter as DECLARED — reach, groups, exceptions;
          · the consumers it EFFECTIVELY reaches, expanded and counted, because
            a group is a label and a chat can have filled it a minute ago;
          · what already binds that same audience — the rules in force that
            reach every one of them — because a rule that repeats one already
            in force is the commonest thing worth catching at the door."""
        rows = list(self.cx.execute("SELECT * FROM v_rule WHERE status='proposed' "
                                    "ORDER BY rule_id"))
        items = []
        for row in rows:
            aud = self._audience(row["rule_id"])
            reached = self._effective(row["rule_id"], row["reach"])
            names = sorted(self.cx.execute("SELECT name FROM consumer WHERE consumer_id=?",
                                           (c,)).fetchone()[0] for c in reached)
            already = []
            for other in self.cx.execute("SELECT * FROM v_rule WHERE status='active' "
                                         "ORDER BY domain_id, seq"):
                if not self._in_force(other):
                    continue
                if reached and reached <= self._effective(other["rule_id"], other["reach"]):
                    already.append(f"{other['display_id']} — {other['title']}")
            d = {"id": row["display_id"], "type": row["type"], "title": row["title"],
                 "body": self._expand(row["body"]), "reason": row["reason"],
                 "proposed_by": row["proposed_by"], "proposed_at": row["created_at"],
                 "source": row["source"],
                 "declared": {"reach": row["reach"], "groups": aud["groups"],
                              "exceptions": aud["exceptions"]},
                 "reaches": names, "reaches_count": len(names),
                 "already_bound_by": already}
            if row["supersedes_rule_id"]:
                victim = self._rule_by_pk(row["supersedes_rule_id"])
                d["supersedes"] = {"id": victim["display_id"], "title": victim["title"]}
            items.append(d)
        return {"project": self.name, "pending": items, "count": len(items),
                "digest": self._digest(rows), "queue_cap": self.queue_cap(),
                "contract": "ticked is approved, unticked is DENIED, in one turn. A "
                            "proposal that arrives between reading and posting makes the "
                            "digest stale and nothing is written."}

    def decide(self, digest: str, approve, denials=None, actor: str = "web ui") -> dict:
        """ONE turn of the page: the ticks go in, the rest are refused with
        their reason, and both halves are recorded as a single DECISION.

        Approving and denying are the same gesture here, so an "approval" that
        forgot the noes would be a record of half of what happened. Denying
        costs a sentence — the schema's CHECK is the guarantee — and approving
        does not, because the yes is the tick and the rule's own reason is
        already written."""
        denials = {str(k).strip().upper(): v for k, v in (denials or {}).items()}
        rows = list(self.cx.execute("SELECT * FROM v_rule WHERE status='proposed' "
                                    "ORDER BY rule_id"))
        if not rows:
            raise RulesError("the queue is empty: there is nothing to decide.")
        if not secrets.compare_digest((digest or "").strip(), self._digest(rows)):
            raise RulesError(
                "the queue changed between the reading and this post: nothing was "
                "written. Read it again — a proposal that arrived in between would "
                "otherwise be denied by a tick nobody put on it.")
        by_id = {r["display_id"]: r for r in rows}
        ticked = []
        for a in (approve or []):
            aid = _norm_id(str(a))
            if aid not in by_id:
                raise RulesError(f"{aid} is not in this lot: nothing was written.")
            ticked.append(aid)
        cap = self.queue_cap()
        if cap is not None and cap > 0 and len(ticked) > cap:
            raise RulesError(
                f"{len(ticked)} ticks against a ceiling of {cap}. The ceiling is the "
                "point: at the twelfth signature in a row a person signs without reading.")
        refused = [r["display_id"] for r in rows if r["display_id"] not in ticked]
        for rid in refused:
            if not (denials.get(rid) or "").strip():
                raise RulesError(
                    f"{rid} is not ticked, so it is DENIED, and a denial costs a sentence. "
                    "Say why — it is what the proposer reads, and what stops the same "
                    "proposal coming back next week unchanged.")
        approved_out, denied_out = [], []
        with self._transaction():
            now = _now()
            self.cx.execute("INSERT INTO decision (digest, decided_at) VALUES (?,?)",
                            (digest, now))
            did = self.cx.execute("SELECT last_insert_rowid()").fetchone()[0]
            for rid in ticked:
                row = by_id[rid]
                expires = _plus_days(self.provisional_days)
                self.cx.execute(
                    "UPDATE rule SET status='active', permanence='provisional', "
                    "expires_at=?, event='approved', actor=?, updated_at=? "
                    "WHERE rule_id=?", (expires, actor, now, row["rule_id"]))
                self.cx.execute("INSERT INTO decision_rule (decision_id, rule_id, verdict) "
                                "VALUES (?,?,'approved')", (did, row["rule_id"]))
                approved_out.append({"id": rid, "expires_at": expires})
                if row["supersedes_rule_id"]:
                    victim = self._rule_by_pk(row["supersedes_rule_id"])
                    self.cx.execute(
                        "UPDATE rule SET status='retired', superseded_by_rule_id=?, "
                        "event=?, actor=?, updated_at=? WHERE rule_id=?",
                        (row["rule_id"], f"superseded by {rid}", actor, now,
                         victim["rule_id"]))
                    approved_out[-1]["retired"] = victim["display_id"]
            for rid in refused:
                row = by_id[rid]
                why = (denials.get(rid) or "").strip()
                self.cx.execute(
                    "UPDATE rule SET status='denied', event=?, actor=?, updated_at=? "
                    "WHERE rule_id=?", (why, actor, now, row["rule_id"]))
                self.cx.execute("INSERT INTO decision_rule (decision_id, rule_id, verdict, "
                                "reason) VALUES (?,?,'denied',?)", (did, row["rule_id"], why))
                denied_out.append({"id": rid, "reason": why})
        return {"decision": did, "approved": approved_out, "denied": denied_out,
                "provisional_days": self.provisional_days}

    def renew(self, ids, days: int = 0, actor: str = "web ui") -> dict:
        """Push the expiry of a provisional rule out by another term. It is a
        decision to keep something, so it is taken on the page and it is
        recorded like every other gesture."""
        days = int(days or self.provisional_days)
        out = []
        with self._transaction():
            for rid in (ids or []):
                row = self._rule_row(str(rid))
                if row is None:
                    raise RulesError(f"{rid}: never defined in this project.")
                if row["status"] != "active":
                    raise RulesError(f"{row['display_id']} is {row['status']}: only a rule "
                                     "in force is renewed.")
                if row["permanence"] == "permanent":
                    raise RulesError(f"{row['display_id']} is permanent: it has no expiry "
                                     "to push.")
                expires = _plus_days(days)
                self.cx.execute(
                    "UPDATE rule SET expires_at=?, event=?, actor=?, updated_at=? "
                    "WHERE rule_id=?",
                    (expires, f"renewed for {days} days", actor, _now(), row["rule_id"]))
                out.append({"id": row["display_id"], "expires_at": expires})
        return {"renewed": out, "days": days}

    def promote(self, ids, actor: str = "web ui") -> dict:
        """From provisional to permanent: the rule stops having to be renewed.
        Staying costs a decision, and this is that decision."""
        out = []
        with self._transaction():
            for rid in (ids or []):
                row = self._rule_row(str(rid))
                if row is None:
                    raise RulesError(f"{rid}: never defined in this project.")
                if row["status"] != "active":
                    raise RulesError(f"{row['display_id']} is {row['status']}: only a rule "
                                     "in force is promoted.")
                self.cx.execute(
                    "UPDATE rule SET permanence='permanent', expires_at=NULL, "
                    "event='promoted to permanent', actor=?, updated_at=? "
                    "WHERE rule_id=?", (actor, _now(), row["rule_id"]))
                out.append(row["display_id"])
        return {"promoted": out}

    # =================================================================
    # The anagrafica: the project itself, and its structure
    # =================================================================

    ENTITIES = ("project", "domain", "consumer", "group")
    ACTIONS = ("create", "amend", "retire", "revive")

    # THE COMBINATIONS THAT HAVE NO TOOL, declared here rather than discovered
    # inside a handler. It is the SHAPE of the call and not the state of the
    # project, which is what decides where it is refused: before the
    # credentials, with the entity and the action, because sending somebody to
    # the maintenance page to mint a one-time code for a gesture that does not
    # exist is a trip that ends in the same refusal.
    NO_TOOL = {("project", "create"), ("project", "retire"), ("project", "revive")}
    NO_TOOL_WHY = ("the project is not created, retired or revived from here: it is a "
                   "line in projects.txt and a folder on disk, and both are Unraid's. "
                   "What is catastrophic has no tool.")

    # The fields each entity accepts, per action. Written once and read by both
    # the door and the ladder below: a field the door accepts and the ladder
    # has never heard of would be a field with no gate.
    FIELDS = {
        "project": {"amend": ("brief", "specs", "queue_cap")},
        "domain": {"create": ("code", "description", "reason"),
                   "amend": ("description",)},
        "consumer": {"create": ("name", "kind", "brief", "specs", "secret"),
                     "amend": ("name", "brief", "specs", "secret")},
        "group": {"create": ("name", "members"),
                  "amend": ("name", "members")},
    }

    # The ONE exception downward, and it is declared rather than deduced:
    # operational data moves on the reference code. `brief` is identity and
    # does not — a chat holding only the reference code must not be able to
    # rewrite its own mandate.
    SPECS_ONLY = {("project", "specs"), ("consumer", "specs")}

    @classmethod
    def port_for(cls, entity: str, action: str, fields=None) -> str:
        """WHICH GATE this gesture needs: 'project', 'admin' or 'auth'.

        The ladder lives HERE, in one classmethod, and the surface asks it
        rather than repeating it: a rule written at each door is a rule with
        one door out of step. It is FLAT, and it fits in a line — creating
        takes the admin code, modifying anything that already exists takes the
        admin code AND a one-time auth code. A criterion with a case list grows
        exceptions that rot; this one has exactly one, and it is above.

        A mixed `fields` answers with the HIGHEST port it contains, which is
        what makes 'refuse the call whole' possible: the caller is told the
        field that needs the higher gate instead of getting the authorised
        subset written and the rest dropped.

        It answers for RULES as well — `port_for('rule', 'amend')`,
        `port_for('rule', 'retire')` — and for the same reason: narrowing a
        perimeter and ending a rule are modifications of something that
        exists, and the ladder does not learn a second shape for them. TASKS
        do not come here, and that is the one declared exception: their gate
        is about OWNERSHIP, not about entity and action, so it lives where
        ownership is known — inside task_close and task_amend."""
        entity = (entity or "").strip().lower()
        action = (action or "").strip().lower()
        if action != "amend":
            return "admin" if action == "create" else "auth"
        keys = [k for k in (fields or {})]
        if keys and all((entity, k) in cls.SPECS_ONLY for k in keys):
            return "project"
        return "auth"

    @classmethod
    def refuse_mixed(cls, entity: str, action: str, fields=None) -> None:
        """The MIXED call, refused WHOLE and naming the field that costs more.

        It lives here, next to the ladder it reads, and not at the door: a
        refusal written on the surface is a refusal no suite can exercise
        without a server, and the one guarantee it carries — that the
        authorised subset is NOT written while the rest is dropped — is
        precisely the one worth a case.

        Only genuinely mixed fields get this sentence. A call where everything
        needs the higher gate is not a caller who chose the wrong field, it is
        a caller who brought no credential, and the ordinary refusal says that
        better. Called by the surface when no pair was presented at all."""
        fields = dict(fields or {})
        low = [f for f in fields if cls.port_for(entity, action, {f: fields[f]}) == "project"]
        if not low or len(low) == len(fields):
            return
        high = ", ".join(sorted(set(fields) - set(low)))
        raise RulesError(
            f"{high}: this field is not operational data, and it does not travel on the "
            f"reference code — {', '.join(sorted(low))} would. The call is refused WHOLE: "
            "the part you are allowed is not written and the rest dropped, because a "
            "gesture that half happened is a gesture nobody can read six months later. "
            "Bring the admin code in `key`, and the one-time `auth_code` with it.")

    def amend_project(self, entity: str, name: str, action: str, fields=None,
                      reason: str = "", actor: str = "",
                      auth_code: str = "") -> dict:
        """The project itself and its STRUCTURE — profile, domains, consumers,
        groups. Rules and tasks are the project's OBJECTS and have tools of
        their own; the prefix says which level a call works on.

        Every refusal in here repeats a guarantee that lives in the schema,
        because the schema's message is about a table and this one can be about
        the gesture. Where a guarantee CANNOT live in the schema — the empty
        perimeter, which needs groups expanded and retirements subtracted — it
        lives here and nowhere else, and it names the rules it is protecting."""
        entity = (entity or "").strip().lower()
        action = (action or "").strip().lower()
        fields = dict(fields or {})
        actor = (actor or "").strip() or None
        if entity not in self.ENTITIES:
            raise RulesError(f"entity {entity!r}: one of {', '.join(self.ENTITIES)}. "
                             "Rules and tasks have their own tools.")
        if action not in self.ACTIONS:
            raise RulesError(f"action {action!r}: one of {', '.join(self.ACTIONS)}.")
        if (entity, action) in self.NO_TOOL:
            raise RulesError(self.NO_TOOL_WHY)
        # The order of these two matters: on a retirement EVERY field is
        # unknown, and "not a field" would send the caller looking for the
        # right spelling of something that has no business being there.
        if action in ("retire", "revive") and fields:
            raise RulesError(f"{action} takes no fields: it is one gesture, and mixing a "
                             "change into it would hide the change — retire it, then "
                             "amend it, and the history keeps the two apart.")
        allowed = self.FIELDS.get(entity, {}).get(action, ())
        unknown = [k for k in fields if k not in allowed]
        if unknown:
            raise RulesError(
                f"{', '.join(sorted(unknown))}: not a field of {entity} on {action}. "
                f"What this takes: {', '.join(allowed) or 'nothing but a reason'}.")
        if action == "retire" and not (reason or "").strip():
            raise RulesError("retiring anything costs a reason: it is the sentence "
                             "whoever finds the dead row six months later will read.")
        if action == "amend" and not fields:
            raise RulesError("nothing to amend: `fields` carries only what changes, and "
                             "an empty one is a gesture with no content.")
        # The ladder is ASKED, once, and carried down as a prepared gesture: the
        # handlers open the transaction that writes, so that is where the
        # one-time code has to burn. `create` needs none — a created thing is
        # attached to nothing — and `specs` alone needs neither.
        needs_auth = self.port_for(entity, action, fields) == "auth"
        # AND THE SECOND FACTOR IS CHECKED HERE, before the handler is reached,
        # because the handlers are where the state lives: `_amend_consumer`
        # answers "this project has no consumer by that name" and would answer
        # it to somebody whose one-time code was never valid.
        #
        # It cannot be literally the first line, and that is not a compromise:
        # `port_for` needs `fields` to know whether a code is wanted at all, so
        # the field names have to be validated before the question can be
        # asked. What comes before this point says nothing about the state of
        # the project — only about the shape of the call.
        if needs_auth:
            _auth_row(self, auth_code)
        code, verb = auth_code, f"{entity}.{action}"

        def gesture():
            return self._gesture(code, verb, needs_auth)

        handler = getattr(self, f"_amend_{entity}")
        return handler(name, action, fields, (reason or "").strip(), actor, gesture)

    # ---------- the profile ----------

    def _amend_project(self, name, action, fields, reason, actor,
                     gesture) -> dict:
        # The impossible combinations are refused by `amend_project`, from
        # NO_TOOL, before the credentials are asked for. This is the last line
        # and it says the same thing from the same constant: a message written
        # twice is a message that goes out of step once.
        if action != "amend":
            raise RulesError(self.NO_TOOL_WHY)
        row = self._profile_row()
        brief = fields.get("brief", row["brief"] if row else None)
        specs = fields.get("specs", row["specs"] if row else None)
        cap = fields.get("queue_cap", row["queue_cap"] if row else None)
        if "queue_cap" in fields and cap is not None:
            cap = int(cap)
            if cap < 0:
                raise RulesError("queue_cap: null is unlimited, 0 closes the queue, N is "
                                 "N. A negative ceiling is none of the three.")
        if "brief" in fields:
            brief = self._prose("brief", brief)
        if "specs" in fields:
            specs = self._prose("specs", specs)
        with gesture():
            if row is None:
                self.cx.execute(
                    "INSERT INTO project_profile (profile_id, brief, specs, queue_cap, "
                    "updated_at, actor) VALUES (1,?,?,?,?,?)",
                    (brief, specs, cap, _now(), actor))
            else:
                self.cx.execute(
                    "UPDATE project_profile SET brief=?, specs=?, queue_cap=?, "
                    "updated_at=?, actor=? WHERE profile_id=1",
                    (brief, specs, cap, _now(), actor))
        return {"entity": "project", "action": "amended",
                "changed": sorted(fields), "profile": self.profile()}

    # ---------- domains ----------

    def _amend_domain(self, name, action, fields, reason, actor,
                     gesture) -> dict:
        if action == "create":
            # ONE door for the letter-pair, wherever it is declared: the
            # reservation of TK holds here and at every use, because a row put
            # in by hand never passed this one.
            code = _valid_domain((fields.get("code") or name or "").strip().upper())
            if self.cx.execute("SELECT 1 FROM domain WHERE lower(code)=?",
                               (code.lower(),)).fetchone():
                raise RulesError(f"domain {code} is already declared.")
            why = self._prose("reason", fields.get("reason") or reason)
            if not why.strip():
                raise RulesError("a domain needs a reason to exist: one nobody can justify "
                                 "is a drawer, and drawers fill up.")
            desc = self._prose("description", fields.get("description") or "")
            with gesture():
                self.cx.execute(
                    "INSERT INTO domain (code, description, reason, created_at, actor) "
                    "VALUES (?,?,?,?,?)", (code, desc or None, why, _now(), actor))
            return {"entity": "domain", "action": "created", "code": code}

        row = self._domain_row(name, live=False)
        if action == "amend":
            desc = self._prose("description", fields.get("description") or "")
            with gesture():
                self.cx.execute("UPDATE domain SET description=?, actor=? WHERE domain_id=?",
                                (desc or None, actor, row["domain_id"]))
            return {"entity": "domain", "action": "amended", "code": row["code"],
                    "note": "the CODE is not amendable: it is printed inside every ID this "
                            "domain ever handed out, and changing it would relabel history."}
        if action == "retire":
            if row["retired_at"]:
                raise RulesError(f"domain {row['code']} was already retired on "
                                 f"{row['retired_at']}.")
            # The trigger refuses this too, and it is the guarantee; here the
            # message can name the rules instead of naming the table.
            live = [f"{r[0]} — {r[1]}" for r in self.cx.execute(
                "SELECT display_id, title FROM v_rule WHERE domain_id=? AND status='active' "
                "ORDER BY seq", (row["domain_id"],))]
            if live:
                raise RulesError(
                    f"domain {row['code']} still has rules in force: "
                    f"{'; '.join(live)}. Retire or supersede them first — a rule whose "
                    "domain is dead carries a label nobody can look up.")
            with gesture():
                self.cx.execute("UPDATE domain SET retired_at=?, retired_reason=?, actor=? "
                                "WHERE domain_id=?", (_now(), reason, actor,
                                                      row["domain_id"]))
            return {"entity": "domain", "action": "retired", "code": row["code"],
                    "note": "its numbers stay readable for ever; nothing new is filed "
                            "under it."}
        if not row["retired_at"]:
            raise RulesError(f"domain {row['code']} is not retired: there is nothing to "
                             "revive.")
        with gesture():
            self.cx.execute("UPDATE domain SET retired_at=NULL, retired_reason=NULL, "
                            "actor=? WHERE domain_id=?", (actor, row["domain_id"]))
        return {"entity": "domain", "action": "revived", "code": row["code"]}

    # ---------- consumers ----------

    def _amend_consumer(self, name, action, fields, reason, actor,
                     gesture) -> dict:
        if action == "create":
            who = _valid_name(fields.get("name") or name, "consumer")
            if self.cx.execute("SELECT 1 FROM consumer WHERE lower(name)=?",
                               (_fold(who),)).fetchone():
                raise RulesError(f"{who}: this project already has a consumer by that "
                                 "name — identity is the casefolded name.")
            kind = (fields.get("kind") or "").strip().lower()
            if kind not in KINDS:
                raise RulesError(
                    f"kind {kind!r}: one of {', '.join(KINDS)}. A human calls no tool but "
                    "owns tasks; it is not guessed from the name, because a wrong guess "
                    "would be written in silence.")
            with gesture():
                self.cx.execute(
                    "INSERT INTO consumer (name, kind, brief, specs, secret, created_at, "
                    "actor) VALUES (?,?,?,?,?,?,?)",
                    (who, kind, self._prose("brief", fields.get("brief") or "") or None,
                     self._prose("specs", fields.get("specs") or "") or None,
                     (fields.get("secret") or "").strip() or None, _now(), actor))
            return {"entity": "consumer", "action": "created", "name": who, "kind": kind}

        row = self._consumer_row(name, live=False)
        if action == "amend":
            new_name = row["name"]
            if "name" in fields:
                new_name = _valid_name(fields["name"], "consumer")
                clash = self.cx.execute(
                    "SELECT name FROM consumer WHERE lower(name)=? AND consumer_id<>?",
                    (_fold(new_name), row["consumer_id"])).fetchone()
                if clash:
                    raise RulesError(f"{clash[0]} already carries that name.")
            brief = (self._prose("brief", fields["brief"]) if "brief" in fields
                     else row["brief"])
            specs = (self._prose("specs", fields["specs"]) if "specs" in fields
                     else row["specs"])
            secret = row["secret"]
            if "secret" in fields:
                secret = (fields["secret"] or "").strip() or None
            with gesture():
                self.cx.execute(
                    "UPDATE consumer SET name=?, brief=?, specs=?, secret=?, actor=? "
                    "WHERE consumer_id=?",
                    (new_name, brief, specs, secret, actor, row["consumer_id"]))
            out = {"entity": "consumer", "action": "amended", "name": new_name,
                   "changed": sorted(fields)}
            if new_name != row["name"]:
                out["renamed_from"] = row["name"]
                out["note"] = self._rename_note(row["name"], new_name)
            return out
        if action == "retire":
            if row["retired_at"]:
                raise RulesError(f"{row['name']} ended on {row['retired_at']} already.")
            open_tasks = self.cx.execute(
                "SELECT COUNT(*) FROM task WHERE consumer_id=? AND status='pending'",
                (row["consumer_id"],)).fetchone()[0]
            if open_tasks:
                raise RulesError(
                    f"{row['name']} still has {open_tasks} open "
                    f"{'task' if open_tasks == 1 else 'tasks'}: close them or hand them to "
                    "whoever takes the work over. A desk that ends with post on it loses "
                    "the post.")
            stuck = self._orphaned_by({row["consumer_id"]})
            if stuck:
                raise RulesError(
                    f"{row['name']} is the last consumer these rules in force reach: "
                    f"{'; '.join(stuck)}. Sort them out first — retire them properly, or "
                    "give them a perimeter that still means something. A rule left binding "
                    "nobody is a retirement nobody decided and nobody can find.")
            with gesture():
                # MARKED, and not one junction row deleted: `ba7dde8` paid for
                # that sentence. Deleting from the audience tables would change
                # the perimeter of LIVE RULES as the side effect of a gesture
                # aimed at somebody else. The reads exclude the retired by
                # themselves.
                self.cx.execute("UPDATE consumer SET retired_at=?, retired_reason=?, "
                                "actor=? WHERE consumer_id=?",
                                (_now(), reason, actor, row["consumer_id"]))
            return {"entity": "consumer", "action": "retired", "name": row["name"],
                    "note": "the row stays and every pointer with it: the history reads. "
                            "Nothing reaches it any more."}
        if not row["retired_at"]:
            raise RulesError(f"{row['name']} is not retired: there is nothing to revive.")
        with gesture():
            self.cx.execute("UPDATE consumer SET retired_at=NULL, retired_reason=NULL, "
                            "actor=? WHERE consumer_id=?", (actor, row["consumer_id"]))
        return {"entity": "consumer", "action": "revived", "name": row["name"],
                "note": "it is back in every perimeter it was named in: nothing had been "
                        "deleted."}

    @staticmethod
    def _rename_note(old: str, new: str) -> str:
        """A rename is a gesture in TWO TIMES, and the second one is not the
        registry's. The old name stops resolving — one name for one thing — so
        the verdict has to say out loud what lives outside and now points at
        nothing. Without this the scheduled task fails at 03:00 and nobody sees
        it until morning."""
        return (f"{old!r} STOPS RESOLVING from now on: one name, one thing. What lives "
                f"outside this registry and still says {old!r} has to be updated by hand "
                f"— the skill's file, the chat's instructions, the prompt of any "
                f"scheduled task. Nothing out there is going to tell you.")

    # ---------- groups ----------

    def _amend_group(self, name, action, fields, reason, actor,
                     gesture) -> dict:
        if action == "create":
            gname = _valid_name(fields.get("name") or name, "group")
            if self.cx.execute("SELECT 1 FROM consumer_group WHERE lower(name)=?",
                               (_fold(gname),)).fetchone():
                raise RulesError(f"{gname}: this project already has a group by that name.")
            members = [self._consumer_row(m)["consumer_id"]
                       for m in (fields.get("members") or [])]
            if not members:
                raise RulesError("a group with no members is a name: give it at least one "
                                 "consumer, or do not create it yet.")
            self._no_mirror(set(members), gname)
            with gesture():
                self.cx.execute("INSERT INTO consumer_group (name, created_at, actor) "
                                "VALUES (?,?,?)", (gname, _now(), actor))
                gid = self.cx.execute("SELECT last_insert_rowid()").fetchone()[0]
                for cid in dict.fromkeys(members):
                    self.cx.execute("INSERT INTO consumer_group_member (group_id, "
                                    "consumer_id) VALUES (?,?)", (gid, cid))
            return {"entity": "group", "action": "created", "name": gname,
                    "members": len(set(members))}

        row = self._group_row(name, live=False)
        if action == "amend":
            new_name = row["name"]
            if "name" in fields:
                new_name = _valid_name(fields["name"], "group")
                clash = self.cx.execute(
                    "SELECT name FROM consumer_group WHERE lower(name)=? AND group_id<>?",
                    (_fold(new_name), row["group_id"])).fetchone()
                if clash:
                    raise RulesError(f"{clash[0]} already carries that name.")
            out = {"entity": "group", "action": "amended", "name": new_name,
                   "changed": sorted(fields)}
            before = self._members_of([row["group_id"]])
            after = before
            if "members" in fields:
                after = {self._consumer_row(m)["consumer_id"]
                         for m in (fields.get("members") or [])}
                if not after:
                    raise RulesError(
                        "a group emptied of its members is a name pointing at nobody: if "
                        "the group is finished, retire it — that costs a reason and says "
                        "so in the history.")
                gone = before - after
                if gone:
                    # THE EMPTY GUARD, the twin of the one on a consumer's
                    # retirement: pulling people out of a group can leave a rule
                    # in force reaching nobody, and that damage is silent.
                    stuck = self._orphaned_by(gone)
                    if stuck:
                        names = ", ".join(sorted(
                            self.cx.execute("SELECT name FROM consumer WHERE consumer_id=?",
                                            (c,)).fetchone()[0] for c in gone))
                        raise RulesError(
                            f"taking {names} out of {row['name']} would leave these rules "
                            f"in force reaching nobody: {'; '.join(stuck)}. Sort the rules "
                            "out first.")
                # An ADDITION passes even when it covers a rule's exception: the
                # anagrafica does not pay for a defect that sits in the rule.
                # The overlap goes to project_status's report, and the next
                # write on that rule refuses it. Blocking the irreparable,
                # reporting the repairable.
                out["joined"] = sorted(self.cx.execute(
                    "SELECT name FROM consumer WHERE consumer_id=?", (c,)).fetchone()[0]
                    for c in (after - before))
                out["left"] = sorted(self.cx.execute(
                    "SELECT name FROM consumer WHERE consumer_id=?", (c,)).fetchone()[0]
                    for c in gone)
            with gesture():
                self.cx.execute("UPDATE consumer_group SET name=?, actor=? WHERE group_id=?",
                                (new_name, actor, row["group_id"]))
                if "members" in fields:
                    self.cx.execute("DELETE FROM consumer_group_member WHERE group_id=?",
                                    (row["group_id"],))
                    for cid in after:
                        self.cx.execute("INSERT INTO consumer_group_member (group_id, "
                                        "consumer_id) VALUES (?,?)", (row["group_id"], cid))
            if new_name != row["name"]:
                out["renamed_from"] = row["name"]
                out["note"] = self._rename_note(row["name"], new_name)
            return out
        if action == "retire":
            if row["retired_at"]:
                raise RulesError(f"group {row['name']} was already retired on "
                                 f"{row['retired_at']}.")
            stuck = self._orphaned_by(self._members_of([row["group_id"]]))
            if stuck:
                raise RulesError(
                    f"retiring {row['name']} would leave these rules in force reaching "
                    f"nobody: {'; '.join(stuck)}. Sort them out first.")
            with gesture():
                self.cx.execute("UPDATE consumer_group SET retired_at=?, retired_reason=?, "
                                "actor=? WHERE group_id=?",
                                (_now(), reason, actor, row["group_id"]))
            return {"entity": "group", "action": "retired", "name": row["name"]}
        if not row["retired_at"]:
            raise RulesError(f"group {row['name']} is not retired: nothing to revive.")
        with gesture():
            self.cx.execute("UPDATE consumer_group SET retired_at=NULL, "
                            "retired_reason=NULL, actor=? WHERE group_id=?",
                            (actor, row["group_id"]))
        return {"entity": "group", "action": "revived", "name": row["name"]}

    def _no_mirror(self, members: set, gname: str) -> None:
        """A group CREATED to mirror the exceptions of a rule in force is
        refused, naming the rule.

        It is the third door of the containment invariant, and it is the one
        that looks like a kindness rather than a guard: somebody sees three
        exceptions on a rule, makes a group of exactly those three, and now the
        same three people are reached twice by two mechanisms that will drift
        apart. The two other doors are the write of a rule and the write of its
        perimeter."""
        if not members:
            return
        for row in self.cx.execute("SELECT * FROM v_rule WHERE status='active' "
                                   "AND reach='targeted'"):
            exc = {r[0] for r in self.cx.execute(
                "SELECT consumer_id FROM rule_audience_exception WHERE rule_id=?",
                (row["rule_id"],))}
            if exc and exc == members:
                raise RulesError(
                    f"{gname} would be exactly the exceptions of {row['display_id']} — "
                    f"{row['title']!r}. Two ways to reach the same people drift apart: "
                    f"create the group, then narrow that rule onto it with rules_amend — "
                    "or give this group a different membership.")

    # =================================================================
    # The report
    # =================================================================

    def status(self) -> dict:
        """THE PROJECT'S HEALTH, in one call, and it REPORTS: it does not
        correct. What it finds is sorted out by whoever has the context, not by
        whoever happens to be running an audit.

        Half of the old `rules_check` died with the schema — a structural
        pointer that points nowhere is impossible now, because it is a foreign
        key. What is left is the half a database cannot see: citations inside
        PROSE, and the overlaps that formed after the fact."""
        now = _now()
        rules = list(self.cx.execute("SELECT * FROM v_rule ORDER BY domain_id, seq"))
        in_force = [r for r in rules if self._in_force(r, now)]

        expiring = []
        for r in in_force:
            if r["permanence"] == "permanent" or not r["expires_at"]:
                continue
            days = (datetime.strptime(r["expires_at"], "%Y-%m-%dT%H:%M:%SZ")
                    - datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ")).days
            if days <= 30:
                expiring.append({"id": r["display_id"], "title": r["title"],
                                 "expires_at": r["expires_at"], "in_days": days,
                                 "reason": r["reason"]})

        # Citations in PROSE, towards a rule that is retired or was never
        # defined. The body of a rule is only one of the fields it can hide in.
        #
        # THE DOOR AND THE SWEEP DO DIFFERENT JOBS, and neither replaces the
        # other: the door catches what is born crooked, the sweep catches what
        # BECOMES crooked. A task filed today citing a rule in force is
        # legitimate; the rule is retired tomorrow and the task now points at a
        # dead one, and it is already inside. Nothing re-refuses it.
        dangling = []
        seen_fields = (("title", "title"), ("body", "body"), ("reason", "reason"),
                       ("source", "source"))

        def _walk(where: str, text: str, label: str) -> None:
            for m in RE_CITE.finditer(text or ""):
                try:
                    dst = _norm_id(m.group(1))
                except RulesError:
                    continue
                if dst.startswith(TASK_PREFIX + "-"):
                    # A task cited from a task is only broken when it resolves
                    # to nothing: a CLOSED one is a legitimate pointer back at
                    # work that is done, and reading labels its state.
                    if self._task_row(dst) is None:
                        dangling.append({"in": where, "field": label, "cites": dst,
                                         "state": "no such task"})
                    continue
                target = self._rule_row(dst)
                if target is None:
                    dangling.append({"in": where, "field": label, "cites": dst,
                                     "state": "never defined"})
                elif target["status"] in ("retired", "denied"):
                    state = target["status"]
                    if state == "retired" and target["superseded_by_rule_id"]:
                        state = ("superseded by "
                                 + self._display(target["superseded_by_rule_id"]))
                    dangling.append({"in": where, "field": label, "cites": dst,
                                     "state": state})
                elif target["status"] == "active" and not self._in_force(target):
                    # The one that arrives without anybody doing anything: a
                    # provisional term runs out and every pointer at it goes
                    # quiet. The door cannot catch this one by construction —
                    # it was in force when it was written.
                    dangling.append({"in": where, "field": label, "cites": dst,
                                     "state": "expired"})

        for r in rules:
            for col, label in seen_fields:
                _walk(r["display_id"], r[col], label)
        # OPEN tasks only, and that is a decision about what a report is FOR.
        # A closed task cannot be amended — `task_amend` answers `closed is
        # closed` — so a finding on one is a line nobody can ever clear, and it
        # would accumulate for the life of the project. Every other section
        # here is actionable; a section that is not teaches people to skim.
        for t in self.cx.execute("SELECT * FROM v_task WHERE status='pending' "
                                 "AND archived_at IS NULL ORDER BY seq"):
            for col in ("title", "body"):
                _walk(t["display_id"], t[col], col)

        # The overlaps that formed AFTER the rule was written: a consumer that
        # joined a group which the rule already reaches, while it also sits in
        # that rule's exceptions. Not blocked when it happens — the anagrafica
        # does not pay for a defect in a rule — so this is where it surfaces.
        overlaps = []
        for r in in_force:
            if r["reach"] != "targeted":
                continue
            gids = [x[0] for x in self.cx.execute(
                "SELECT group_id FROM rule_audience_group WHERE rule_id=?",
                (r["rule_id"],))]
            covered = self._members_of(gids)
            for x in self.cx.execute(
                    "SELECT c.consumer_id, c.name FROM rule_audience_exception e "
                    "JOIN consumer c ON c.consumer_id = e.consumer_id "
                    "WHERE e.rule_id=?", (r["rule_id"],)):
                if x[0] in covered:
                    overlaps.append({"rule": r["display_id"], "consumer": x[1],
                                     "note": "now inside a group this rule already "
                                             "reaches: the exception has become a "
                                             "duplicate, and the next write on this rule "
                                             "will refuse it"})

        # The price of dropping the two BEFORE INSERT guards, paid where it was
        # promised: an audience row sitting next to a universal rule. It is
        # inert — the photograph reads those tables only when reach is targeted
        # — but the next write on that rule will be refused for it, and the
        # gesture refused will belong to somebody else.
        strays = []
        for r in rules:
            if r["reach"] != "all":
                continue
            n = (self.cx.execute("SELECT COUNT(*) FROM rule_audience_group WHERE rule_id=?",
                                 (r["rule_id"],)).fetchone()[0]
                 + self.cx.execute("SELECT COUNT(*) FROM rule_audience_exception "
                                   "WHERE rule_id=?", (r["rule_id"],)).fetchone()[0])
            if n:
                strays.append({"rule": r["display_id"], "rows": n,
                               "note": "audience rows next to a rule declared universal. "
                                       "They reach nobody — the snapshot ignores them — "
                                       "but the next write on this rule is refused until "
                                       "they go. Nothing here put them in."})

        # THE RETIRED, and this report is the ONLY place they are readable.
        # `project_info` shows the live alone, on purpose — but the identity of
        # a consumer is its casefolded name and that name stays TAKEN once it
        # is retired: `_amend_consumer` answers a create with "this project
        # already has a consumer by that name". Without this section the
        # Architect meets a refusal pointed at something invisible, and
        # `revive` is a gesture on a target nobody can read. The admin code is
        # already in hand here, and whoever revives is holding it.
        retired = {
            "domains": [{"code": r["code"], "retired_at": r["retired_at"],
                         "reason": r["retired_reason"]}
                        for r in self.cx.execute(
                            "SELECT * FROM domain WHERE retired_at IS NOT NULL "
                            "ORDER BY code")],
            "consumers": [{"name": r["name"], "kind": r["kind"],
                           "retired_at": r["retired_at"],
                           "reason": r["retired_reason"]}
                          for r in self.cx.execute(
                              "SELECT * FROM consumer WHERE retired_at IS NOT NULL "
                              "ORDER BY name")],
            "groups": [{"name": r["name"], "retired_at": r["retired_at"],
                        "reason": r["retired_reason"]}
                       for r in self.cx.execute(
                           "SELECT * FROM consumer_group WHERE retired_at IS NOT NULL "
                           "ORDER BY name")],
        }

        orphan_domains = [d[0] for d in self.cx.execute(
            "SELECT code FROM domain WHERE retired_at IS NULL AND domain_id NOT IN "
            "(SELECT DISTINCT domain_id FROM rule) ORDER BY code")]
        unreached = []
        for c in self.cx.execute("SELECT consumer_id, name, kind FROM consumer "
                                 "WHERE retired_at IS NULL ORDER BY name"):
            # A HUMAN IS A DESTINATION, NOT A SUBJECT, and this line is the
            # whole of it. They receive tasks; no rule binds them through the
            # registry, because a rule that binds a person says so in its body.
            # Without this they would ALL sit in this list, permanently, for
            # doing exactly what they are for — and a report with a permanent
            # resident is a report people stop reading.
            if c["kind"] == "human":
                continue
            if not self._reaching(c["consumer_id"]):
                unreached.append(c["name"])

        return {
            "project": self.name,
            "counted": {"rules": len(rules), "in_force": len(in_force),
                        "proposed": sum(1 for r in rules if r["status"] == "proposed"),
                        "retired": sum(1 for r in rules if r["status"] == "retired"),
                        "denied": sum(1 for r in rules if r["status"] == "denied"),
                        "consumers": self.cx.execute(
                            "SELECT COUNT(*) FROM consumer WHERE retired_at IS NULL"
                        ).fetchone()[0],
                        "groups": self.cx.execute(
                            "SELECT COUNT(*) FROM consumer_group WHERE retired_at IS NULL"
                        ).fetchone()[0],
                        "tasks_open": self.cx.execute(
                            "SELECT COUNT(*) FROM task WHERE status='pending'"
                        ).fetchone()[0]},
            "queue_cap": self.queue_cap(),
            "expiring": expiring,
            "dangling_citations": dangling,
            "overlaps": overlaps,
            "stray_audience_rows": strays,
            "domains_with_no_rules": orphan_domains,
            "consumers_no_rule_reaches": unreached,
            "retired": retired,
            "note": "counted on read, never stored. Structural pointers are not checked "
                    "because they cannot break: the schema refuses them at write time. "
                    "`retired` is here because it is nowhere else: project_info shows "
                    "the LIVE only, and a retired name is still a name taken — revive "
                    "it, or pick another.",
        }

    def export(self, consumer: str = "", expand: bool = False) -> dict:
        """The corpus in one call, for a migration or for reading off-site.
        `consumer` narrows to one perimeter; `expand` inlines the cited titles.

        Mind the client's result ceiling: this is the tool that meets it
        first, and the answer says how many bytes it is so the next call can
        be aimed."""
        if consumer:
            c = self._consumer_row(consumer)
            rows = [t[0] for t in sorted(self._reaching(c["consumer_id"]).values(),
                                         key=lambda t: t[0]["display_id"])]
        else:
            rows = [r for r in self.cx.execute(
                "SELECT * FROM v_rule WHERE status='active' ORDER BY domain_id, seq")
                if self._in_force(r)]
        lines = [f"# {self.name} — rules in force", ""]
        prof = self.profile()
        if prof["brief"]:
            lines += ["## The project", "", prof["brief"], ""]
        if prof["specs"]:
            lines += ["### Specs", "", prof["specs"], ""]
        for r in rows:
            aud = self._audience(r["rule_id"])
            perimeter = ("everyone" if r["reach"] == "all" else
                         ", ".join(aud["groups"] + [f"{e} (by name)"
                                                    for e in aud["exceptions"]]))
            lines += [f"## {r['display_id']} — {r['title']}",
                      "",
                      f"*{r['type']} · {r['permanence']} · reaches: {perimeter}*"
                      + (f" · expires {r['expires_at']}" if r["expires_at"] else ""),
                      "",
                      self._expand(r["body"]) if expand else r["body"],
                      "",
                      f"> why: {r['reason']}",
                      ""]
        md = "\n".join(lines)
        return {"project": self.name, "consumer": consumer or None,
                "rules": len(rows), "markdown": md, "bytes": len(md.encode())}

    # =================================================================
    # The task log: work, not law
    # =================================================================

    @staticmethod
    def _norm_task_id(tid: str) -> str:
        t = (tid or "").strip().upper()
        m = RE_ID_IN.match(t)
        if not m or m.group(1) != TASK_PREFIX:
            raise RulesError(f"malformed task ID {tid!r}: it is "
                             f"{TASK_PREFIX}-{'N' * ID_DIGITS}, e.g. {TASK_PREFIX}-0012")
        return f"{TASK_PREFIX}-{int(m.group(2)):0{ID_DIGITS}d}"

    def _task_row(self, tid: str):
        return self.cx.execute("SELECT * FROM v_task WHERE display_id=?",
                               (self._norm_task_id(tid),)).fetchone()

    def _next_task_seq(self) -> int:
        """MAX(seq)+1, and it can be trusted because the prune ARCHIVES instead
        of deleting: every number ever handed out is still a row. In 3.1.0 the
        prune deleted, MAX went backwards, and TK-0004 came back after
        TK-0007."""
        last = self.cx.execute("SELECT IFNULL(MAX(seq),0) FROM task").fetchone()[0]
        n = int(last) + 1
        if n > MAX_SEQ:
            raise RulesError(f"the task log has burned all {MAX_SEQ} numbers.")
        return n

    @staticmethod
    def _age_days(stamp: str, now: str) -> int:
        try:
            return (datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ")
                    - datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")).days
        except (TypeError, ValueError):
            return 0

    def _task_brief(self, row, now: str) -> dict:
        d = {"id": row["display_id"], "title": row["title"],
             "owner": self.cx.execute("SELECT name FROM consumer WHERE consumer_id=?",
                                      (row["consumer_id"],)).fetchone()[0],
             "created_by": row["created_by"], "status": row["status"],
             "urgent": bool(row["urgent"]), "created_at": row["created_at"]}
        if row["status"] == "pending":
            age = self._age_days(row["created_at"], now)
            if age >= TASKS_STALE_DAYS:
                # A LABEL on a reading, never a lifecycle: a task does not
                # expire, because an automatic expiry would be a `dropped` with
                # no reason, written by the clock.
                d["stale"] = f"open for {age} days"
        else:
            d["closed_at"] = row["closed_at"]
            d["outcome"] = row["outcome"]
            d["reason_dropped"] = row["reason_dropped"]
        return d

    @staticmethod
    def _task_order(row):
        """Urgent first, then the oldest — ONE definition of that sentence.

        Three places order tasks now (`tasks_list`, its query fragments, and
        the desk at session start), and three copies of a sort key is how two
        views of the same desk come back in different orders while every case
        stays green."""
        return (0 if row["urgent"] else 1, row["created_at"])

    def _order_and_cap(self, rows, now: str) -> tuple:
        """Urgent first, then the oldest. When the cap cuts, it cuts the FRESH
        work — what has been waiting longest is what a desk needs to see."""
        ordered = sorted(rows, key=self._task_order)
        return [self._task_brief(r, now) for r in ordered[:TASKS_LIST_CAP]], len(ordered)

    def task_add(self, consumer: str, title: str, body: str, created_by: str,
                 urgent: bool = False, idem_key: str = "",
                 consumer_key: str = "", admin: bool = False) -> dict:
        """Open a task on a desk — yours or anybody's. Opening for others is
        the POINT of the log: the audit that finds something routes it to the
        owner who can fix it, instead of carrying it.

        Opening one for a HUMAN does not notify them. A human calls no tool;
        their post is seen by whoever reads the overview or the page. Tasks are
        not a notification channel to the owner, and the manual says so next to
        this call."""
        owner = self._consumer_row(consumer)
        who = (created_by or "").strip()
        if not who:
            raise RulesError("created_by is required: it is a signature, and a task whose "
                             "sender is unknown is a task the owner cannot answer.")
        signer = self.cx.execute("SELECT * FROM consumer WHERE lower(name)=?",
                                 (_fold(who),)).fetchone()
        if signer is not None:
            self._check_consumer_key(signer, consumer_key, admin=admin)
        if not (title or "").strip():
            raise RulesError("the task needs a title")
        if not (body or "").strip():
            raise RulesError("the task needs a body: a title alone is a reminder to "
                             "whoever wrote it, not work anybody else can pick up.")
        title = self._task_prose("title", title)
        body = self._task_prose("body", body)
        if len(body.encode()) > MAX_BODY_BYTES:
            raise RulesError(f"the body is {len(body.encode())} bytes, ceiling "
                             f"{MAX_BODY_BYTES}.")
        key = (idem_key or "").strip() or None
        if key:
            twin = self.cx.execute(
                "SELECT * FROM v_task WHERE consumer_id=? AND idem_key=? "
                "AND status='pending'", (owner["consumer_id"], key)).fetchone()
            if twin is not None:
                # It does not PUNISH the repeat, it absorbs it: a recurring
                # audit that finds the same thing again is reporting it again.
                return {"id": twin["display_id"], "owner": owner["name"],
                        "already_open": True,
                        "note": "same idem_key, same desk, still open: this is the task "
                                "that was already there, not a twin."}
        with self._transaction():
            self.cx.execute(
                "INSERT INTO task (seq, title, body, consumer_id, created_by, urgent, "
                "status, actor, idem_key, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?, 'pending', ?,?,?,?)",
                (self._next_task_seq(), title, body, owner["consumer_id"], who,
                 1 if urgent else 0, who, key, _now(), _now()))
            tid = self.cx.execute("SELECT display_id FROM v_task WHERE task_id="
                                  "last_insert_rowid()").fetchone()[0]
        out = {"id": tid, "owner": owner["name"], "urgent": bool(urgent)}
        if owner["kind"] == "human":
            out["note"] = (f"{owner['name']} is a human: this does NOT notify them. Their "
                           "post is read from the overview or from the web page.")
        return out

    def task_list(self, consumer: str, query: str = "", since: str = "",
                  until: str = "", authored: bool = False) -> dict:
        """One desk, short form, ordered by the server.

        `authored=True` turns the view round: the tasks THIS consumer opened on
        OTHER desks, with status and outcome. A task for somebody else is also
        a message, and a sender who cannot see it close sends it again."""
        c = self._consumer_row(consumer, live=False)
        now = _now()
        if authored:
            rows = [r for r in self.cx.execute(
                "SELECT * FROM v_task WHERE lower(created_by)=? AND consumer_id<>? "
                "AND archived_at IS NULL", (_fold(c["name"]), c["consumer_id"]))]
        else:
            rows = [r for r in self.cx.execute(
                "SELECT * FROM v_task WHERE consumer_id=? AND archived_at IS NULL",
                (c["consumer_id"],))]
        q = (query or "").strip()
        if q:
            rows = [r for r in rows
                    if q.lower() in (r["title"] or "").lower()
                    or q.lower() in (r["body"] or "").lower()]
        if since or until:
            lo = _day_start(since, "since") if since else ""
            hi = _day_end(until, "until") if until else ""
            rows = [r for r in rows if r["closed_at"]
                    and (not lo or r["closed_at"] >= lo)
                    and (not hi or r["closed_at"] <= hi)]
        else:
            # Closed tasks trail the list for a month and then stop showing up.
            # They are not gone: they are asked for by date.
            rows = [r for r in rows if r["status"] == "pending"
                    or self._age_days(r["closed_at"] or now, now) <= TASKS_RECENT_DAYS]
        pending = [r for r in rows if r["status"] == "pending"]
        closed = [r for r in rows if r["status"] != "pending"]
        out, total = self._order_and_cap(pending, now)
        closed_out = sorted((self._task_brief(r, now) for r in closed),
                            key=lambda d: d["closed_at"] or "", reverse=True)
        res = {"project": self.name, "consumer": c["name"],
               "view": "authored" if authored else "desk",
               "open": out, "open_count": total,
               "closed_recent": closed_out[:TASKS_LIST_CAP]}
        if total > len(out):
            res["truncated"] = True
            res["note"] = (f"{total} open and the first {TASKS_LIST_CAP} are here: the cut "
                           "falls on the FRESH work, because the oldest is what the desk "
                           "owes.")
        if q:
            res["query"] = q
            for d, r in zip(out, sorted(pending, key=self._task_order)):
                frag = self._fragment(q, r["title"]) or self._fragment(q, r["body"])
                if frag:
                    d["fragment"] = frag
        return res

    def task_get(self, ids) -> dict:
        """Full bodies, citations expanded, up to GET_IDS at a time. It reads
        ANY task of the project on purpose: reads are project-wide and the
        boundary is the reference code."""
        if isinstance(ids, str):
            ids = [i.strip() for i in ids.replace(",", " ").split() if i.strip()]
        ids = [str(i).strip() for i in (ids or []) if str(i).strip()]
        if not ids:
            raise RulesError("no ID given: tasks_get takes the IDs of the tasks to read.")
        if len(ids) > GET_IDS:
            raise RulesError(
                f"{len(ids)} IDs asked for and the ceiling is {GET_IDS}: REFUSED, not "
                "trimmed. A silent cut answers a question you did not ask.")
        now, out, missing, size, cut = _now(), [], [], 0, False
        for tid in ids:
            try:
                row = self._task_row(tid)
            except RulesError:
                # An ID of the RIGHT shape but the wrong family — a rule read as
                # a task — is NAMED rather than reported missing: the caller
                # went to the wrong tool, and "not found" would send them
                # looking for a row that is sitting right there.
                rule = self._rule_row(tid)
                if rule is not None:
                    raise RulesError(
                        f"{rule['display_id']} is a RULE, not a task: read it with "
                        "rules_get. Tasks are TK-NNNN.") from None
                raise
            if row is None:
                missing.append(self._norm_task_id(tid))
                continue
            if cut:
                continue
            d = {"id": row["display_id"], "title": row["title"],
                 "body": self._expand(row["body"]),
                 "owner": self.cx.execute("SELECT name FROM consumer WHERE consumer_id=?",
                                          (row["consumer_id"],)).fetchone()[0],
                 "created_by": row["created_by"], "urgent": bool(row["urgent"]),
                 "status": row["status"], "outcome": row["outcome"],
                 "reason_dropped": row["reason_dropped"],
                 "created_at": row["created_at"], "closed_at": row["closed_at"]}
            if row["status"] == "pending":
                age = self._age_days(row["created_at"], now)
                if age >= TASKS_STALE_DAYS:
                    d["stale"] = f"open for {age} days"
            size += len(str(d).encode())
            if size > GET_BYTES and out:
                cut = True
                continue
            out.append(d)
        res = {"project": self.name, "tasks": out, "count": len(out)}
        if missing:
            res["not_found"] = missing
        if cut:
            res["truncated"] = True
            res["note"] = f"cut at {GET_BYTES} bytes: {len(out)} of {len(ids)} read."
        return res

    def task_close(self, tid: str, by: str, outcome: str = "", reason: str = "",
                   consumer_key: str = "", admin: bool = False) -> dict:
        """Close a task: ONE gesture, two verdicts. `outcome` completes it,
        `reason` drops it, exactly one of the two — and the guarantee is the
        schema's CHECK, not this method.

        Closing a task you do not own takes the admin code, and it is the ONE
        declared exception to the flat ladder: it stays at one factor because a
        task closed wrong reopens as a new task, while a rule retired wrong
        loses its ID and its continuity."""
        row = self._task_row(tid)
        if row is None:
            raise RulesError(f"{self._norm_task_id(tid)}: no such task in this project.")
        owner = self.cx.execute("SELECT * FROM consumer WHERE consumer_id=?",
                                (row["consumer_id"],)).fetchone()
        who = (by or "").strip()
        if not who:
            raise RulesError("`by` is required: a closure is signed.")
        if row["status"] != "pending":
            raise RulesError(
                f"{row['display_id']} is already {row['status']} since {row['closed_at']}: "
                "closed is closed — no amend, no reopen. If the work came back, open a new "
                "task and cite this one.")
        if not admin and _fold(who) != _fold(owner["name"]):
            raise RulesError(
                f"{row['display_id']} belongs to {owner['name']}: closing somebody else's "
                "task takes the admin code in `key`. Opening one for another desk is free; "
                "closing it is not, because only the owner knows how it went.")
        signer = self.cx.execute("SELECT * FROM consumer WHERE lower(name)=?",
                                 (_fold(who),)).fetchone()
        if signer is not None:
            self._check_consumer_key(signer, consumer_key, admin=admin)
        has_out, has_why = bool((outcome or "").strip()), bool((reason or "").strip())
        if has_out == has_why:
            raise RulesError(
                "exactly one of the two: `outcome` completes the task — what came of it —"
                " and `reason` drops it — why it will not be done. Both is a task that was"
                " and was not done; neither is a closure nobody can read." )
        outcome = self._task_prose("outcome", outcome) if has_out else None
        reason = self._task_prose("reason", reason) if has_why else None
        with self._transaction():
            self.cx.execute(
                "UPDATE task SET status=?, outcome=?, reason_dropped=?, closed_at=?, "
                "actor=?, updated_at=? WHERE task_id=?",
                ("completed" if has_out else "dropped", outcome, reason, _now(), who,
                 _now(), row["task_id"]))
        return {"id": row["display_id"],
                "status": "completed" if has_out else "dropped",
                "by": who, "outcome": outcome, "reason": reason}

    def task_amend(self, tid: str, by: str, title: str = "", body: str = "",
                   consumer: str = "", consumer_key: str = "",
                   admin: bool = False) -> dict:
        """Amend an OPEN task: title, body, or `consumer` to reassign — the
        reassignment is named in the story, which keeps both owners.

        `urgent` has no parameter here, and that is not an oversight: urgency
        belongs to whoever created the task, and a door that let the receiver
        clear it would put the lever in the hand of whoever has an interest in
        postponing. It is not a closed door, it is a door that does not
        exist."""
        row = self._task_row(tid)
        if row is None:
            raise RulesError(f"{self._norm_task_id(tid)}: no such task in this project.")
        owner = self.cx.execute("SELECT * FROM consumer WHERE consumer_id=?",
                                (row["consumer_id"],)).fetchone()
        who = (by or "").strip()
        if not who:
            raise RulesError("`by` is required: an amendment is signed.")
        if row["status"] != "pending":
            raise RulesError(f"{row['display_id']} is {row['status']}: closed is closed.")
        if not admin and _fold(who) != _fold(owner["name"]):
            raise RulesError(
                f"{row['display_id']} belongs to {owner['name']}: amending somebody else's "
                "task takes the admin code in `key`.")
        signer = self.cx.execute("SELECT * FROM consumer WHERE lower(name)=?",
                                 (_fold(who),)).fetchone()
        if signer is not None:
            self._check_consumer_key(signer, consumer_key, admin=admin)
        new_owner = owner
        if (consumer or "").strip():
            new_owner = self._consumer_row(consumer)
        new_title = self._task_prose("title", title) if (title or "").strip() \
            else row["title"]
        new_body = self._task_prose("body", body) if (body or "").strip() else row["body"]
        if (new_title == row["title"] and new_body == row["body"]
                and new_owner["consumer_id"] == owner["consumer_id"]):
            raise RulesError("nothing to amend: only what you pass changes, and nothing "
                             "passed is different from what is there.")
        with self._transaction():
            self.cx.execute(
                "UPDATE task SET title=?, body=?, consumer_id=?, actor=?, updated_at=? "
                "WHERE task_id=?",
                (new_title, new_body, new_owner["consumer_id"], who, _now(),
                 row["task_id"]))
        out = {"id": row["display_id"], "owner": new_owner["name"], "by": who}
        if new_owner["consumer_id"] != owner["consumer_id"]:
            out["reassigned_from"] = owner["name"]
        return out

    def task_overview(self) -> dict:
        """Every desk at once, short form, ceilings declared — the cross view
        that lets an audit route work to the owner who can do it. Reading it
        moves nothing: no counter, no timestamp."""
        now = _now()
        desks = []
        for c in self.cx.execute("SELECT * FROM consumer ORDER BY name"):
            rows = list(self.cx.execute(
                "SELECT * FROM v_task WHERE consumer_id=? AND status='pending' "
                "AND archived_at IS NULL", (c["consumer_id"],)))
            if not rows and c["retired_at"]:
                continue
            out, total = self._order_and_cap(rows, now)
            desks.append({"consumer": c["name"], "kind": c["kind"],
                          "retired": bool(c["retired_at"]),
                          "open": total,
                          "urgent": sum(1 for r in rows if r["urgent"]),
                          "stale": sum(1 for r in rows
                                       if self._age_days(r["created_at"], now)
                                       >= TASKS_STALE_DAYS),
                          "tasks": out})
        return {"project": self.name, "desks": desks,
                "caps": {"list": TASKS_LIST_CAP, "get_ids": GET_IDS,
                         "get_bytes": GET_BYTES, "stale_days": TASKS_STALE_DAYS},
                "note": "humans appear here too: it is where their post is read, because "
                        "a human calls no tool."}

    def prune_tasks(self, before: str, actor: str = "web ui") -> dict:
        """ARCHIVE what is finished and older than a date. It marks, it does
        not delete — which is what lets MAX(seq) be trusted — and it REFUSES
        the open ones, saying how many it left alone: an open task is not
        clutter, it is work, and hiding it from the desk that owes it is the
        one thing this must never do."""
        cut = _day_start(before, "before")
        rows = list(self.cx.execute(
            "SELECT * FROM v_task WHERE status<>'pending' AND archived_at IS NULL "
            "AND closed_at < ?", (cut,)))
        still_open = self.cx.execute(
            "SELECT COUNT(*) FROM task WHERE status='pending' AND created_at < ?",
            (cut,)).fetchone()[0]
        with self._transaction():
            for r in rows:
                self.cx.execute("UPDATE task SET archived_at=?, actor=?, updated_at=? "
                                "WHERE task_id=?", (_now(), actor, _now(), r["task_id"]))
        return {"archived": [r["display_id"] for r in rows], "count": len(rows),
                "left_open": still_open,
                "note": f"{still_open} open tasks older than {cut} were left where they "
                        "are: the prune is for what is finished."}

    # =================================================================
    # Backup
    # =================================================================

    def backup(self, dest_dir: str) -> dict:
        """A quiescent copy of this project's database (VACUUM INTO): it opens
        without recovery, which a copy of a live WAL file does not.

        PER PROJECT, because in this world a project is a folder: the file, its
        -wal and its -shm. Copying one of the three is a corrupt backup, and
        VACUUM INTO is how you get one file that is all three."""
        os.makedirs(dest_dir, exist_ok=True)
        try:
            os.chmod(dest_dir, DIR_MODE)
        except OSError:
            pass
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = os.path.join(dest_dir, f"{self.slug}-{stamp}.db")
        self.cx.execute("VACUUM INTO ?", (dest,))
        try:
            os.chmod(dest, FILE_MODE)
        except OSError:
            pass
        return {"project": self.name, "backup": dest,
                "bytes": os.path.getsize(dest),
                "note": "quiescent copy of THIS project: opens without recovery, and it is "
                        "the one to take off-site. ZFS snapshots stay the main net."}
