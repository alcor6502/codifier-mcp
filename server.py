"""
server.py — self-hosted MCP server for a RULES REGISTRY. ONE database, N projects.

Twin of archivist-mcp: same architecture, same OAuth gate, same blocking
preflight. Three deliberate differences:

- it runs as ROOT and the database files are 0644: whoever mounts the share
  READS them and does not touch them. Writing by hand bypasses the triggers and
  breaks history in silence;
- there is no container per project: the project is an ARGUMENT — and not its
  NAME but an opaque alphanumeric CODE that lives at the top of that project's
  instructions. No read tool lists projects and no error names one: whoever
  lacks the code cannot find the door;
- writing is a two-step affair. A chat PROPOSES; the batch is approved in the
  administration UI, behind the master, against the batch's DIGEST — you
  approve the batch you read. Since v3.0.0 approve/renew/promote and the
  master operations (create, registry, rekey) are NOT tools: they left the
  MCP surface when the UI replaced their placeholder, so the master never
  travels in a conversation. The backup left with them and lives on the UI's
  maintenance page, where the session is the whole of what it needs. What stays behind the tools is REDACTION,
  and it opens per project: the pair (project code, architect key) travels on
  every maintenance call. ADMIN_ACCESS_CODE is dead — one container-wide code
  opened the maintenance of every project, which is the defect you discover
  the day the projects are two.

Every tool that acts on a rule is prefixed `rules_`: the vault's tools (status,
history, diff, search...) live in the same chat, and two namesakes get confused.
The prefix stays `rules_` even though the repository is called codifier-mcp —
inside this project "rules" is the only subject there is. The one manual —
`reference_guide` — carries no prefix, because it touches no rule: it serves a
file, whole, with a stop line where the consumer's part ends.

Configuration, all through environment variables:
  DB_PATH                 the single database (default /db/rules.db)
  BACKUP_DIR              VACUUM INTO copies (default: <db dir>/backup)
  PROVISIONAL_DAYS        how long an approved rule lives (default 90)
  PENDING_CAP             pending proposals a project may hold (default 5).
                          Born optional with a working default in the code:
                          Unraid does not propagate new variables to
                          containers already installed
  BASE_URL                public URL (e.g. https://host.tailnet.ts.net)
  GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET / ALLOWED_GITHUB_LOGIN / JWT_SIGNING_KEY
  PORT                    default 3001
  BIND_HOST               the interface inside the container, default 127.0.0.1:
                          legitimate traffic comes from the Funnel, which runs
                          alongside. 0.0.0.0 exposes the service to the LAN
  LOG_LEVEL               INFO or WARNING (default INFO) — ours only, never the
                          root logger. Anything else falls back to INFO and says
                          so: there are no debug lines to switch on, and above
                          WARNING the gate's refusals disappear
  ALLOWED_CIDRS           accepted ranges, ';' between entries and '#' opening a
                          description. Empty string disables the filter
  ANTHROPIC_CIDR          DEPRECATED, still honoured: see ALLOWED_CIDRS
  WEB_PORT                the port the administration UI listens on (default
                          9443). Resolved in web.port_from_env so the service
                          and the preflight cannot disagree about it
  WEB_MASTER_CODE         the master of the administration UI — the root of
                          the deployment: it opens every page, approves,
                          creates projects, rekeys, backs up. It never
                          leaves the browser: no tool carries it
  WEB_ACTION_CAP          how many proposals the UI may approve in one action
                          (default 5)

Nothing here switches the argument redaction on or off, and that is deliberate:
a knob for it would be a knob for printing credentials into the log.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import uvicorn
from fastmcp import FastMCP
from fastmcp.server.auth.providers.github import GitHubProvider
# Imported BY NAME on purpose: `mcp` is the name of the server object below,
# and importing the package would shadow it.
from mcp.types import Icon

# The common engine: the gate, the refusal conversion and the config helpers
# live there since the adoption, pinned to a TAG in requirements.txt. The
# reasoning each piece carries stays in its module's docstring, next to the
# code it describes. What stays HERE is everything the engine cannot know:
# which login, which filter, which error class is a refusal and which a fault.
from mcp_common_engine import (VERSION as ENGINE_VERSION, cidrs_from_env,
                               describe_cidrs, log_level_from_env)
from mcp_common_engine.gate import Gate
from mcp_common_engine.logs import arm_argument_redaction
from mcp_common_engine.refusals import make_tool
import web
from rules import Registry, RulesError, RulesFault, VERSION

# The ROOT logger stays at WARNING. It used to be INFO, which switched on INFO
# for every library loaded, not for ours: that is where the noise came from.
# Only our own logger follows LOG_LEVEL.
logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("codifier-mcp")
# Resolved in the engine's log_level_from_env for the same reason as the IP
# filter: one expression, so the service and the preflight cannot disagree.
# The list is closed to INFO and WARNING there — and closed in the CODE, not
# only in the template's dropdown, because a container built by hand has no
# template.
_LEVEL, _REJECTED = log_level_from_env()
log.setLevel(_LEVEL)
if _REJECTED:
    # Said out loud, and at WARNING so it survives the level it is reporting on.
    log.warning("LOG_LEVEL=%r is not INFO or WARNING — using INFO", _REJECTED)

# uvicorn's access log is one line per request. Left on, it slowly becomes a
# record of who asked what and when — and the arguments here are project codes.
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

# FastMCP's banner, rich formatting and boot-time update check are switched off
# in the Dockerfile (ENV), NOT here: those are read when fastmcp is imported, so
# anything set after the import above would arrive too late.


def env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        log.error("missing environment variable: %s", name)
        sys.exit(2)
    return v


DB_PATH = env("DB_PATH", "/db/rules.db")
BASE_URL = env("BASE_URL")
ALLOWED_LOGIN = env("ALLOWED_GITHUB_LOGIN")
PORT = int(env("PORT", "3001"))
# The interface the MCP server listens on INSIDE the container. It is read
# once, here, because the startup line prints it and 0.0.0.0 is the one field
# on that line where being wrong matters.
BIND_HOST = os.environ.get("BIND_HOST", "127.0.0.1")
# Resolved in web.port_from_env for the same reason as the IP filter and the
# log level: one expression, so the service and the preflight cannot disagree.
WEB_PORT = web.port_from_env()
# The administration UI's master. Read HERE, like every other secret, and
# handed to the web layer: a layer that read its own configuration would be a
# second place where it is decided. The preflight has already refused a
# missing one, a placeholder and anything under 12 characters.
WEB_MASTER = env("WEB_MASTER_CODE")
# Resolved in web.action_cap_from_env, once, for the reason the port is.
WEB_ACTION_CAP = web.action_cap_from_env()
BACKUP_DIR = os.environ.get("BACKUP_DIR") or os.path.join(os.path.dirname(DB_PATH), "backup")
# Resolved in the engine's cidrs_from_env so the service and the preflight can
# never disagree about what the filter is.
ALLOWED_CIDRS = cidrs_from_env()

registry = Registry(DB_PATH,
                    provisional_days=int(os.environ.get("PROVISIONAL_DAYS") or 90),
                    pending_cap=int(os.environ.get("PENDING_CAP") or 5))
if registry.repaired:
    log.warning("schema rebuilt at open: %s — somebody had removed these objects",
                ", ".join(registry.repaired))
# A schema change on a database in service happens once and cannot be undone, so
# the boot that does it says so. It is deliberately a short list: the migration
# moves columns and converts NOTHING — the rules go back in by hand, one at a
# time, and an engine that rewrote them behind the author's back would be moving
# the very pointers that pass exists to re-decide.
if registry.migrated:
    log.warning("schema migrated at open: %s", ", ".join(registry.migrated))
log.info("registry %s — %s — %s projects — provisional %s days",
         VERSION, DB_PATH, registry.projects()["count"], registry.provisional_days)

auth = GitHubProvider(
    client_id=env("GITHUB_CLIENT_ID"),
    client_secret=env("GITHUB_CLIENT_SECRET"),
    base_url=BASE_URL,
    jwt_signing_key=env("JWT_SIGNING_KEY"),
    require_authorization_consent=True,
)

# The icon, and — the part that gets forgotten — where it is and is not seen.
#
# WHERE IT IS SEEN TODAY: the OAuth CONSENT PAGE, the one that comes up when
# the connector is added or reconnected. fastmcp reads the field there —
# `oauth_proxy/consent.py` takes `icons[0].src` and hands it to the logo — so
# this replaces FastMCP's own logo with ours. That page's default CSP is
# `img-src https: data:`, which is why a plain https URL is enough: a base64
# data URI would buy nothing and would ride on every initialize response.
#
# WHERE IT IS NOT SEEN, and this one is not ours to fix: the connector list in
# Claude, which ignores `serverInfo.icons` altogether. The spec has carried the
# field since revision 2025-11-25 (SEP-973); the client does not read it yet
# (anthropics/claude-ai-mcp#152, still open). Serving /favicon.ico and putting
# a <link rel="icon"> on a root page were both tried by others and are ignored
# as well, so there is nothing left here to try. The icon shown in that list
# appears to be derived from the DOMAIN, which under a Funnel is *.ts.net and
# therefore Tailscale's — no line in this repository can reach it.
#
# It is set anyway: it costs one argument, it wins the consent page now, and
# the day the client starts reading the field the list follows without anybody
# touching anything.
#
# THE URL IS NOT A SECOND COPY. It is the same string the Unraid template uses
# for the container icon, and nothing links the two files, so `icon_check` in
# test_surface compares them rather than trusting them to stay equal.
ICON_URL = ("https://raw.githubusercontent.com/alcor6502/codifier-mcp"
            "/main/codifier-icon.png")

mcp = FastMCP("codifier-mcp", auth=auth,
              icons=[Icon(src=ICON_URL, mimeType="image/png",
                          sizes=["256x256"])])

# A malformed call must not print what it carried, and this line is the whole
# cure. fastmcp validates arguments BEFORE any tool of ours runs, and logs what
# it rejected at WARNING with the arguments in the line — a record born on
# `fastmcp.server.server`, printed by fastmcp's own handler with
# `propagate=False`. It obeys neither our LOG_LEVEL nor our decorator, and it
# leaves no `refused` line: for this service, nothing happened. A clean run of
# `refused` lines is therefore no evidence that no call went malformed.
#
# What the payload holds here is not a document body, which is what it is on
# the twin: it is the PROJECT CODE and the ARCHITECT KEY, which travel as
# arguments on every maintenance call. One forgotten parameter and both are in
# the container's log. Measured on this shape, with fastmcp 3.4.5: before,
# `{'project': 'a3f9…', 'cod': 'TOPSECRET-ADMIN-CODE-24'}` printed twice in one
# line; after, `'<redacted>'` — both times, the bare string as well as the
# dictionary. The diagnosis survives whole: the tool, the parameter, the rule
# that was broken.
#
# AFTER the server object, and that is not style: building it is what makes
# fastmcp configure its logging, so armed any earlier there is no handler to
# filter. In that case the engine RAISES instead of reporting a comforting
# zero — a filter on a logger never runs for the records of its children, only
# a handler's does — and the raise is left to stop the boot, because a service
# that starts having protected nothing is worse than one that does not start.
arm_argument_redaction()


# The refusal-to-ToolError conversion and its one log line live in the
# engine's make_tool (mcp_common_engine/refusals.py) — where the trap, the
# fault-first order and the reason the line must be our own are reasoned at
# length, next to the code. The behaviour was measured on BOTH twins before
# the move. What this line decides is the binding the engine cannot: a
# RulesError is a designed refusal, a RulesFault is a genuine fault, caught
# first and left to rise with its traceback.
tool = make_tool(mcp, log, refusal=RulesError, fault=RulesFault)


# The Gate — GitHub identity plus source-IP filter, hooked on `on_request` —
# lives in the engine (mcp_common_engine/gate.py), with the reasoning about
# where it hooks, what it deliberately does not cover, and why its refusals
# are logged at WARNING. What this call decides is who is allowed in and from
# where; handed nothing, a gate would let nobody in and say nothing about it.
mcp.add_middleware(Gate(log=log, allowed_login=ALLOWED_LOGIN,
                        allowed_cidrs=ALLOWED_CIDRS))

# The manual. It ships inside the image, which is the whole reason it is a
# file here and not a document in the vault: a manual that travels with the
# code cannot describe a version that is not running, and a static check can
# hold it against the code it ships with. A copy anywhere else is verified by
# nobody. ONE manual since v2.0.0: the consumer part ends at a stop line, and
# what follows — maintenance, and the legislator's craft — is read past it by
# whoever holds the code. The separate legislator_guide door protected an
# hygiene that had no readers: the manual is read by three chats, and the
# skills do not read it at all.
_GUIDE = Path(__file__).with_name("reference-guide.md")


def _admin(project: str, key: str) -> None:
    """The MAINTENANCE gate, PER PROJECT since v3.0.0: the pair (project
    code, architect key) travels on every call — no session, no "mode" left
    open, and no container-wide code any more. The check lives in the engine
    (check_architect), because the key is per-project DATA now: a hash on the
    project's own row, not a variable of the container. The engine resolves
    the project first, then compares the hash, and answers every failure with
    ONE message — which half was wrong is not said, on purpose.

    No log line here, for the reason v1.2 dropped it: the Gate covers the
    handshake, so a wrong pair can only come from one of Alfredo's own chats
    — an ordinary refusal, logged once by the decorator, at INFO."""
    registry.check_architect(project, key)


# =====================================================================
# Reading — open to every consumer
# =====================================================================

@tool
def rules_project_info(project: str) -> dict:
    """What is inside the project whose code you hold: the CONSUMERS that exist,
    the SCOPES with who is in them, and the DOMAINS of the IDs. Call this first
    if you do not know which consumer to declare — it is also the proof that the
    registry answers.

    `project` is the alphanumeric CODE at the top of the project's instructions,
    not its name. There is no tool that lists projects: without the code the
    registry does not answer, and no error will ever hint at another one."""
    return registry.project_info(project)


@tool
def reference_guide() -> dict:
    """The manual for this registry, whole. The consumer part comes first —
    the model, the citations, which tool for which job, what the errors mean —
    and ends at a STOP line: a working session can stop there. Below the line
    live the maintenance tools, which want the architect key, and the craft of
    deciding what deserves to be a rule. Read the first part before your first
    write."""
    try:
        return {"version": VERSION, "guide": _GUIDE.read_text(encoding="utf-8")}
    except OSError as e:
        # A FAULT, not a refusal: the caller did nothing wrong, the image is
        # incomplete. As a RulesError it would leave one quiet INFO line
        # beginning with the word "refused" — a broken image wearing the face
        # of a normal answer, which is the exact inversion the decorator exists
        # to prevent. As a RulesFault it rises with its traceback at ERROR.
        raise RulesFault(f"guide not available in the image: {e}") from e


@tool
def rules_list(project: str, consumer: str) -> dict:
    """EVERY rule in force for you, in ONE call: pass the project CODE and your
    own consumer name, and you get them whole, ordered from the most widespread
    to the most specific. This replaces opening the rule files — there is
    nothing else to read.

    The answer LEADS with your `brief` — your mandate, who you are — before
    the rules: identity and law in one round trip. A consumer without a brief
    gets an empty field, not an error; skills leave it empty on purpose. Next
    to it rides the LEGEND of the domains present in your list, each with its
    gloss — two letters age badly in human memory, and the glosses already
    live in the project's declarations.

    The order is the BREADTH of the scope a rule reaches you through: what comes
    first binds everyone, what comes last is yours alone. Each rule arrives as
    its ID and its BODY, citations expanded — and nothing else. That is the
    CONSUMER reading: the title, the dates, the perimeter and the why are
    administration, and they live in the maintenance reading (rules_batch,
    rules_export) instead of costing context in every chat that works under
    the rules.

    Consumers are not fixed: every project declares its own, and a skill is a
    consumer exactly like a chat. The verdict also says HOW MANY rules stayed
    outside your perimeter: if an ID you need is not in the list, it is not
    undefined — it belongs to somebody else.

    Only rules IN FORCE: retired ones never appear here, and neither do expired
    provisional ones (both stay reachable by ID, because citations must keep
    resolving)."""
    return registry.list_rules(project, consumer)


@tool
def rules_get(project: str, ids: list[str], consumer: str) -> dict:
    """One or MANY rules by ID (e.g. ["VA-0002","ST-0011"]; the brackets of a
    citation and the type suffix are both tolerated, and a shorter number is
    padded — VA-02 is VA-0002).

    Three DIFFERENT answers, kept apart, and the difference is the point:
      found          the rule's ID and its expanded body. One that is NOT in
                     force additionally says so — retired, denied, expired —
                     because a body handed back as if it bound you would be a
                     lie by omission
      not_yours      they exist, but outside your perimeter — with who holds them
      never_defined  those IDs were never defined here: a BROKEN CITATION to be
                     reported, or you are using another project's code

    Asking for the batch at once is what turns a stumble into an audit: broken
    citations are worth much more seen together than one at a time.

    Bodies come back with every citation EXPANDED — `(VA-0002 — its title)` — so
    you understand a reference without a second call, and a pointer to a retired
    rule arrives already marked as such."""
    return registry.get_rules(project, ids, consumer)


@tool
def rules_search(project: str, text: str, consumer: str) -> dict:
    """Search a string in the title and body of the rules in force within your
    perimeter. It also says how many matches fell outside it, so you know they
    exist without seeing them. Hits arrive as ID and expanded body — the
    consumer reading, same as rules_list."""
    return registry.search(project, text, consumer)


@tool
def rules_pending(project: str, consumer: str = "") -> dict:
    """Your noticeboard: the proposals of yours still waiting, the ones that were
    DENIED with the reason why, and your rules expiring within 30 days.

    This is what replaces the note a chat used to keep in its own memory. You
    filed a proposal three weeks ago and you do not remember what became of it:
    ask here rather than proposing it again. Without `consumer` it shows the
    whole project, which is the maintainer's view.

    The expiring list — and only that one — carries each rule's original
    `reason`: it is the renewals queue, read to decide, and the decision
    needs the why in front of it."""
    return registry.pending(project, consumer)


# =====================================================================
# Proposing — no key: a proposal reaches nobody
# =====================================================================

@tool
def rules_propose(project: str, domain: str, type: str, title: str, body: str,
                  scopes: list[str], reason: str, proposed_by: str = "",
                  changelog: str = "", source: str = "",
                  supersedes: str = "") -> dict:
    """File a proposal for a new rule. It needs ONLY the project code, because a
    proposal reaches nobody until its batch is approved: it cannot do harm, and
    a chat that deposits one can stop keeping a note about it.

    THERE IS NO `id` PARAMETER. You give the `domain` — two uppercase letters,
    already declared by the project — and the registry assigns the next number
    in it, four digits: VA-0002. A number is not a choice, it is a position in a
    sequence. The assigned ID comes back in the verdict, and it is what other
    rules have to cite.

    `type`: R binding · M method · F technical fact. Retirement is a STATE, not
    a type. `scopes`: consumer names, group scope names, or ["*"] if it binds
    everyone present and future. `reason` is mandatory: without the why a rule
    cannot be defended, and at the first opportunity it gets reopened.
    `proposed_by` is MANDATORY too — your own consumer name, what makes the
    proposal yours: omitted it would orphan the proposal in silence, so the
    door refuses it.

    The project holds a LIMITED number of pending proposals (a deployment
    knob, default 5): whoever approves reads in small batches, and that
    rhythm is enforced here, by a refusal that lists what is in the queue.
    Approval and denial free the slots by themselves — there is no override. `supersedes` names the rule this proposal
    REPLACES — a dedicated field, never a citation in the body. The target
    must be in force, only one pending proposal may claim it, and at approval
    the swap is one transaction: the heir goes active and the named rule is
    retired pointing at it. Declare the heir's scopes yourself — the
    supersede is the moment the perimeter gets re-decided, not inherited.

    CITATIONS IN THE BODY are an ID in ROUND BRACKETS, `(VA-0002)`. An ordinary
    parenthesis is ordinary prose — what makes a token a citation is the shape
    XX-NNNN, not the bracket. Four refusals: a bare ID left outside a bracket of
    its own (case does not save you), one that does not resolve, one pointing at
    a rule THAT IS NOT APPROVED YET, and anything of your own written inside the
    brackets — the only text allowed there is the title the registry itself put
    there when you read it, because what is inside is not stored.

    Only the domains this project declared are hunted, so a ticket number or a
    locale in a URL stays prose.

    That last one decides the order of the work, so read it twice: file the
    cited rule, have it approved — the ID it comes back with is final — and only
    then file the rule that cites it. A rule that needs one which does not exist
    yet simply waits. Citing a proposal would mean a batch could be approved
    into a state where its own pointers were right only while it was being
    written."""
    return registry.propose(project, domain, type, title, body, scopes, reason,
                            proposed_by, changelog, source, supersedes)


# =====================================================================
# Reading the batch, and denying — the architect key. Approval is the UI's
# =====================================================================

@tool
def rules_batch(project: str, key: str) -> dict:
    """MAINTENANCE. The pending proposals, whole, plus the DIGEST of the batch.

    You approve the BATCH, never the single rule: seen side by side, three
    proposals that say the same thing become visible as what they are.

    Approval happens in the administration UI — the lot page, behind the
    master — against this same digest: if a proposal arrives in between, the
    digest changes and the stale one is refused. That is on purpose: it is
    the proof that what gets approved is the batch that was READ.

    Each proposal carries its `reason`: the why you are letting in is on the
    table where the decision happens, not a history call away. A proposal
    that SUPERSEDES a rule shows it here too — approving it also retires
    that rule, and whoever approves must see both halves of the move."""
    _admin(project, key)
    return registry.batch(project)


@tool
def rules_deny(project: str, ids: list[str], reason: str, key: str) -> dict:
    """MAINTENANCE. Refuse one or more proposals, with a reason. No signature is
    asked for: refusing cannot do harm.

    The row STAYS and the ID is burnt. It no longer BLOCKS a re-proposal: since
    the counter assigns the number, the same text filed again simply takes a new
    one. What the refusal buys is the REASON — rules_pending shows it to whoever
    proposed it, so silence becomes an answer and they learn something instead of
    guessing. Reading your own refusals is a habit now, not a guard rail."""
    _admin(project, key)
    return registry.deny(project, ids, reason)


# =====================================================================
# Maintaining rules
# =====================================================================

@tool
def rules_fix(project: str, id: str, expected_version: int, reason: str, key: str,
              title: str = "", body: str = "", type: str = "", changelog: str = "") -> dict:
    """MAINTENANCE. Fix a DEFECT in place: a wrong number, a broken pointer, a
    sentence that says something false. Same ID, the rule stays in force, and a
    new version is born in history.

    A superseded DECISION is NOT fixed this way: propose the new rule and retire
    the old one pointing at it. The difference matters — a defect never was
    right, a superseded decision was right and stopped being so.

    `expected_version` is the number you read with rules_get: if somebody wrote
    in the meantime the change is refused and you are told the current version.
    Leave empty whatever you are not changing.

    A `body` you pass goes through the SAME citation check as a proposal:
    `(VA-0002)` must resolve and must point at a rule already approved, a bare
    ID outside a bracket of its own is refused, and so is a note of your own
    inside one. This is the tool that repairs what rules_check lists as broken
    pointers, so it cannot be the one that lets a broken one in.

    OMIT `body` and nothing about it is checked — that is what lets a rule
    written before this format existed still be renamed, retyped or given a
    changelog. The exemption is the field you leave out, not the text that
    happens to be unchanged: a body passed back identical is still checked
    first. You may paste the body back exactly as you read it — the title
    inside the brackets is a gloss generated on reading, and it is dropped
    here.

    `reason` here is the why of the FIX: it lands in the event column and in
    the history. The rule's own `reason` — the why it was filed — is never
    rewritten by any event."""
    _admin(project, key)
    return registry.amend(project, id, expected_version, reason,
                          title or None, body or None, type or None, changelog or None)


@tool
def rules_widen(project: str, id: str, scopes: list[str], key: str, reason: str = "") -> dict:
    """MAINTENANCE. Make a rule ALSO reach somebody else: one more row, and the
    scope it already belonged to is not touched — that scope has other tenants
    who have nothing to do with this rule.

    This is the difference between moving a rule and widening a group, and they
    are two different things. To change who is in a GROUP, use rules_scope_edit,
    and know that it changes the perimeter of every rule pointing at it."""
    _admin(project, key)
    return registry.widen(project, id, scopes, reason)


@tool
def rules_narrow(project: str, id: str, scopes: list[str], key: str) -> dict:
    """MAINTENANCE. Stop a rule reaching a scope. Symmetric to rules_widen: one
    row less. If it ends up with no scope at all the verdict says so — a rule
    that reaches nobody is not retired, it is invisible, which is worse."""
    _admin(project, key)
    return registry.narrow(project, id, scopes)


@tool
def rules_retire(project: str, id: str, reason: str, key: str,
                 superseded_by: str = "", changelog: str = "") -> dict:
    """MAINTENANCE. Retire a rule: it leaves the consumers' lists, but the row
    STAYS. The ID is never reused and citations must keep resolving. There is no
    deletion.

    `superseded_by` when a new rule takes its place (create it first). The
    verdict lists the active rules that still cite this one: those need
    fixing."""
    _admin(project, key)
    return registry.retire(project, id, reason, superseded_by, changelog)


# =====================================================================
# Projects, consumers, scopes
# =====================================================================

@tool
def rules_status(project: str, key: str) -> dict:
    """MAINTENANCE. The verdict on the registry: database integrity, journal
    mode, file permissions, counts by domain and by consumer, how many rules
    have expired without being retired, how many batches were approved. The
    counts cover every perimeter, which is why it wants the architect key."""
    _admin(project, key)
    return registry.status(project)


@tool
def rules_check(project: str, key: str) -> dict:
    """MAINTENANCE. Audit of a project: broken pointers (IDs cited that do not
    exist), and citations made by a rule IN FORCE towards one that is retired,
    denied or still only proposed — plus rules with no perimeter and REDUNDANCY
    CANDIDATES.

    Those three buckets exist because the door can only judge a citation on the
    day it is written: a rule is filed citing a proposal, and the proposal is
    denied a week later. Nothing would ever say so. They count the SOURCE only
    when it is in force, so a batch citing itself is not reported as a defect —
    it is a batch.

    The candidates are a suspicion, not a verdict: two rules in force, in the
    same perimeter, citing the same IDs. The registry puts the pairs under your
    eyes — deciding they say the same thing is a judgement, and it stays
    yours.

    There is no numbering-gap report any more, and that is a decision: the
    number is assigned by the database, so a gap cannot happen — the counter
    does not skip and retiring leaves the row in place. A check that cannot tell
    a fault from a choice is a line you learn to skip."""
    _admin(project, key)
    return registry.check(project)


@tool
def rules_history(project: str, id: str, key: str) -> dict:
    """MAINTENANCE. How that rule changed over time: one row per version, with
    date, action and REASON, plus the perimeter in two columns — `scopes` what
    was declared, `consumers` who was actually reached that day.

    History is written by the database TRIGGERS, not by these tools, so a change
    made by hand with sqlite3 is in here too. It serves whoever MAINTAINS the
    rule, not whoever applies it: the latter only needs the text in force."""
    _admin(project, key)
    return registry.history(project, id)


@tool
def rules_diff(project: str, id: str, version_a: int, version_b: int, key: str) -> dict:
    """MAINTENANCE. What changed between two versions of ONE rule (the numbers
    come from rules_history). Whole versions are kept, not diffs: the comparison
    is computed on the fly between any two, however far apart."""
    _admin(project, key)
    return registry.compare(project, id, version_a, version_b)


@tool
def rules_export(project: str, key: str, consumer: str = "", expand: bool = False) -> dict:
    """MAINTENANCE. A Markdown snapshot, to be written into the vault with the
    archivist's write_file. Two uses:
      with `consumer`     only that perimeter, rules in force, widest first
      without `consumer`  the whole project, retired rules included — the
                          maintenance document, and the copy that goes into git

    Every rule carries its `reason`, and the whole-project export the last
    `event` too: this is the maintenance reading, where the why is on the page.
    The legend of the domains present leads the page, glosses from the
    project's own declarations.

    `expand` decides how citations read: compact `(VA-0002)` by default, or
    carrying the current title of what they point at. This is the only reader
    offered the choice, because it is read by a person — rules_list and rules_get
    always expand, since a chat is not given an option it can get wrong.

    It is a DERIVATIVE: the truth stays in the database and this regenerates. Do
    not edit it and expect the registry to notice."""
    _admin(project, key)
    return registry.export(project, consumer, expand)


@tool
def rules_consumers_add(project: str, consumers: list, key: str) -> dict:
    """MAINTENANCE. Add consumers to a project — chats or skills. Each one gets
    a scope of its own name, made by the database.

    An item may carry a `brief` — the consumer's mandate, in Markdown,
    returned at the head of its rules_list: creating a consumer and giving it
    its identity is one gesture. On a consumer that already EXISTS, an item
    with a brief updates it — this is the door briefs are written through,
    and the database versions every change by trigger, hand edits included.
    Same size discipline as a rule's body. For skills leave it empty: a skill
    describes itself in its own file, and a copy here would be verified by
    nobody.

    Only adding: removing a consumer would orphan the rules aimed at it. And a
    consumer is never RENAMED — a renamed consumer is a different consumer, and
    the rules that reached it need reviewing, not dragging along behind a name.
    Create the new one and retire the old."""
    _admin(project, key)
    return registry.add_consumers(project, consumers)


@tool
def rules_domains_add(project: str, domains: dict, key: str) -> dict:
    """MAINTENANCE. Add ID domains to a project: {"LQ":"liquidity"}. Two
    uppercase letters each. Only adding, for the same reason."""
    _admin(project, key)
    return registry.add_domains(project, domains)


@tool
def rules_scope_create(project: str, name: str, members: list[str], key: str) -> dict:
    """MAINTENANCE. Create a named group of consumers, e.g. "deliberativi" over
    the four chats that deliberate.

    At least two members: every consumer already has a singleton scope of its
    own name, made by the database, so a one-member group would add nothing but
    a second name for the same set. A group cannot take a consumer's name —
    consumers and scopes share one namespace, and that is the right
    constraint."""
    _admin(project, key)
    return registry.create_scope(project, name, members)


@tool
def rules_scope_edit(project: str, name: str, key: str,
                     add: list[str] = None, remove: list[str] = None) -> dict:
    """MAINTENANCE. Change who is in a GROUP scope. Careful: this changes the
    perimeter of EVERY rule pointing at it, and the verdict says how many that
    is. To make one rule reach one more consumer, use rules_widen instead.

    A managed scope — a consumer's singleton, or _ALL_ — is refused: its
    membership is fixed by construction, and the refusal comes from the
    database."""
    _admin(project, key)
    return registry.edit_scope(project, name, add, remove)


# =====================================================================
# The task log — the project code, and the name of whoever is acting
# =====================================================================
#
# Operating on tasks costs the PROJECT CODE and nothing else, plus the name of
# whoever is acting, declared. It is the same trade rules_propose makes and
# for the same reason turned inside out: a proposal is ungated because it
# reaches nobody, a task because it IS the work — asking a working chat for
# the architect key to write down what it has just finished would put the
# maintenance credential in every chat in the project, which is the one thing
# the credential model exists to prevent.
#
# The declared identity is not PROVED, and that is true of the whole registry
# today. The one reading that IS maintenance is tasks_overview: across every
# consumer at once is the maintainer's view, not a worker's.


@tool
def tasks_add(project: str, consumer: str, title: str, body: str, created_by: str,
              urgent: bool = False, idem_key: str = "") -> dict:
    """Open a task for a consumer. The ID comes back as TK-NNNN and is cited
    like a rule, in round brackets: (TK-0012).

    ANYBODY in the project may open one for ANYBODY — that is how a coherence
    audit hands each correction to the role that owns it, instead of writing a
    report somebody has to redistribute by hand. `created_by` is your own
    consumer name and it is MANDATORY: a task nobody signed is a task nobody
    can be asked about.

    `urgent` is set by whoever CREATES the task and can never be changed
    afterwards, by anyone. Urgency is born from a condition only the creator
    knows, and letting the receiver clear the flag would put the lever in the
    hand of whoever has an interest in postponing. There are no levels: a
    scale of five inflates until it stops ordering anything. What guards
    against inflation is that tasks_overview counts urgent tasks BY CREATOR.

    `idem_key` is your own handle for a job you may report more than once —
    the recurring audit that finds the same discrepancy three weeks running.
    While a task with that key is still open on that consumer you get THAT
    task back, not a second one; once it closes, the same key opens a new one,
    because finding it again is a new report.

    The body is Markdown and may cite rules: `(VA-0002)` comes back with that
    rule's current title when the body is read. Nothing is refused here — a
    task is prose about work, so a pointer that does not resolve is reported
    in the text rather than blocking the task."""
    return registry.task_add(project, consumer, title, body, created_by,
                             urgent, idem_key)


@tool
def tasks_list(project: str, consumer: str) -> dict:
    """WHAT IS OPEN FOR YOU, in one call — and what you closed lately, which is
    the same question with the other filter. This is what replaces the
    "pending" section a role memory used to carry, and the per-role changelog:
    every completion cost an outcome, so the closed half reads as a record of
    what was done.

    The SHORT form: id, title, urgent, age, status. The bodies are read
    separately with tasks_get, by code — a list that carried the bodies would
    make the cheapest question in a chat the most expensive one.

    THE SERVER ORDERS: urgent first, then oldest first. That matters when the
    ceiling bites, because then the order decides what is lost — and what must
    survive is the work that has been waiting, not the work you filed this
    morning and still remember.

    A truncated list SAYS SO, with the real total behind it.

    A task open past the staleness threshold comes back MARKED. It has not
    expired and it never will: a task that ages does not become false, it
    stays work nobody did, and an automatic expiry would be a drop with no
    reason, written by the clock. Closing it is your decision, with the reason
    written."""
    return registry.task_list(project, consumer)


@tool
def tasks_search(project: str, consumer: str, query: str) -> dict:
    """Search your tasks, every state included — finding what you already did
    is the same question as finding what is open.

    Every hit carries THE FRAGMENT THAT MATCHED, next to the code: a list of
    codes with no fragments tells you that something matched and not what, and
    the only way on would be one more call per hit. It searches the title, the
    outcome, the reason and the body, and shows the text as it is STORED."""
    return registry.task_search(project, consumer, query)


@tool
def tasks_range(project: str, consumer: str, since: str, until: str,
                on: str) -> dict:
    """The tasks of a stretch of days, `since` and `until` inclusive
    (YYYY-MM-DD). This is where a closed task goes on living once it has left
    the recent window of tasks_list — it is not gone, it is asked for by date.

    `on` says WHICH DATE it filters — `created_at` or `closed_at` — and there
    is NO DEFAULT, deliberately. "Opened in July" and "closed in July" are two
    different questions, a changelog wants the second, and a default would
    answer one of them while you believed the other."""
    return registry.task_range(project, consumer, since, until, on)


@tool
def tasks_get(project: str, ids: list[str]) -> dict:
    """The BODIES, by code, in a batch — e.g. ["TK-0003","TK-0011"]. Up to ten
    per call, and asking for more is REFUSED rather than trimmed: a caller who
    asked for fifteen and quietly got ten would act on the ten.

    A second ceiling, in bytes, is what actually bounds the answer, and when
    it bites the truncation is DECLARED with how many came back. Bodies arrive
    with their citations expanded — `(VA-0002 — its title)` — and one pointing
    nowhere arrives marked in the text.

    An ID that is not a task says so by name: a rule read here is not a
    missing task, it is the wrong tool, and rules_get is the right one."""
    return registry.task_get(project, ids)


@tool
def tasks_complete(project: str, id: str, outcome: str, by: str) -> dict:
    """Close a task WITH ITS OUTCOME, which is mandatory. That is the whole
    design of this log: the completed tasks with their outcomes ARE the
    changelog of a consumer, and one closed in silence is an entry nobody can
    read back.

    Keep the outcome short and queryable — one or two sentences, what came of
    it. The long story goes in the project's own history, written by the same
    hand in the same moment: two gestures, one moment. `by` is your own
    consumer name.

    A closed task is closed: not amended, not reopened, not re-closed."""
    return registry.task_complete(project, id, outcome, by)


@tool
def tasks_drop(project: str, id: str, reason: str, by: str) -> dict:
    """Close a task WITHOUT doing it, with the reason why — the twin of denying
    a proposal. Deciding not to do something is a decision, and one that
    leaves no reason gets taken again from scratch the next time somebody
    reads the same request.

    There is no delete. A dropped task keeps its number, and the number is
    never handed out again."""
    return registry.task_drop(project, id, reason, by)


@tool
def tasks_amend(project: str, id: str, by: str, title: str = "", body: str = "",
                consumer: str = "") -> dict:
    """Amend a task that is still OPEN: its title, its body, or its OWNER.

    Reassigning is here because a misdirected task is an ordinary event, not an
    incident — without it the only way out would be dropping and recreating,
    which breaks the thread between the work and the request that started it.
    The history keeps both owners.

    `urgent` is not here and cannot be reached from anywhere: it belongs to
    whoever created the task. A closed task is not amended at all."""
    return registry.task_amend(project, id, by, title, body, consumer)


@tool
def tasks_overview(project: str, key: str) -> dict:
    """MAINTENANCE. The log across every consumer at once — open, closed,
    dropped, urgent and stale per consumer, the oldest still waiting, and the
    urgent tasks counted BY CREATOR.

    That last count is the guard against urgency inflation, and it is why the
    view is cross-consumer: if one creator's column is all urgent, what gets
    corrected is that skill, not the tasks it filed. It also DECLARES the
    ceilings in force, so the day one of them is exported to the template
    there is a single place that says which value is commanding."""
    _admin(project, key)
    return registry.task_overview(project)


# =====================================================================
# Service
# =====================================================================

# The UI binds 0.0.0.0 and the MCP does not, and the asymmetry is not an
# oversight. The MCP's legitimate traffic arrives from the Funnel, which runs
# alongside inside the container, so 127.0.0.1 is the whole world it needs. The
# UI is reached from the LAN through a PUBLISHED port, and Docker's bridge
# forwards to the container's own address, never to its loopback: bound to
# BIND_HOST the page would be unreachable from the browser it exists for. The
# perimeter that follows is the LAN — known, accepted, and written down in
# `Decisioni aperte.md`; what defends the UI is the master and the session's
# expiry, not the interface it listens on.
WEB_BIND_HOST = "0.0.0.0"


async def _serve() -> None:
    """The two servers, on ONE loop.

    `mcp.run(...)` used to be here, and it cannot stay: it builds the loop and
    owns it, so anything else that had to be served would never be started. In
    its place the app itself — `mcp.http_app()`, the same surface, which is
    the reason this delivery needs no reconnection — plus the UI's, each on a
    `uvicorn.Server`, both awaited together. Serving one and then the other is
    serving one.

    The access log stays off on both, for the reason it is off on the MCP's:
    one line per request slowly becomes a record of who asked what and when."""
    def cfg(app, host: str, port: int) -> uvicorn.Config:
        return uvicorn.Config(app, host=host, port=port, log_level="warning",
                              access_log=False, lifespan="on")

    servers = (
        uvicorn.Server(cfg(mcp.http_app(), BIND_HOST, PORT)),
        uvicorn.Server(cfg(web.build(registry=registry, log=log, master=WEB_MASTER,
                                     action_cap=WEB_ACTION_CAP, refusal=RulesError,
                                     backup_dir=BACKUP_DIR),
                           WEB_BIND_HOST, WEB_PORT)),
    )
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    # This line is what you look at to confirm an update took, so every field
    # on it is READ and not spelled out a second time — the host, because
    # 0.0.0.0 exposes the service to the LAN and a line that kept saying
    # 127.0.0.1 would be lying about exactly that, and the UI's port, because
    # it is what a person needs in order to reach the page at all.
    log.info("codifier-mcp %s — engine %s — starting on %s:%s — base_url %s — allowed user: %s "
             "— IP filter: %s — token store: %s — db: %s (process uid %s) — web UI: http://%s:%s",
             VERSION, ENGINE_VERSION, BIND_HOST, PORT, BASE_URL, ALLOWED_LOGIN,
             describe_cidrs(ALLOWED_CIDRS),
             os.environ.get("FASTMCP_HOME", "(default — NOT persistent!)"),
             DB_PATH, os.geteuid(), WEB_BIND_HOST, WEB_PORT)
    asyncio.run(_serve())
