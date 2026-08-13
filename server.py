"""
server.py — self-hosted MCP server for a RULES REGISTRY. ONE FILE PER PROJECT.

Twin of archivist-mcp: same architecture, same OAuth gate, same blocking
preflight. Three deliberate differences:

- it runs as ROOT and the database files are 0644: whoever mounts the share
  READS them and does not touch them. Writing by hand bypasses the triggers and
  breaks history in silence;
- there is no container per project: the project is an ARGUMENT — and not its
  NAME but an opaque alphanumeric REFERENCE CODE that lives at the top of that
  project's instructions. No read tool lists projects and no error names one:
  whoever lacks the code cannot find the door. Since v4.0.0 the isolation is a
  DATABASE each, and the registry that says which ones are served is a text
  file, `projects.txt`, that only Unraid writes;
- writing is a two-step affair. A chat PROPOSES; the batch is approved in the
  administration UI, behind its password, against the batch's DIGEST — you
  approve the batch you read. Approving, denying, renewing, promoting, minting
  the one-time codes and taking a backup are NOT tools and never will be:
  what is catastrophic has no tool, so the UI password never travels in a
  conversation.

SIXTEEN TOOLS, and the number is the point: the catalogue sits in the context
of every session of the project, so a tool nobody uses is paid for by every
chat that never calls it. Ten of them work — reading, proposing, the task log —
and six administer. The scale is FLAT and fits in a line: CREATING takes the
admin code, MODIFYING anything that already exists takes the admin code AND a
one-time `auth_code` minted on the page, PROPOSING takes the reference code
because it goes past a person anyway. The ladder itself is written in exactly
one place — `Project.port_for` in the engine — and this file ASKS it rather
than repeating it: a rule spelled out at each door is a rule with one door out
of step.

Every tool that acts on a rule is prefixed `rules_`, and the ones that act on
the project or its structure `project_`: the vault's tools (status, history,
search...) live in the same chat, and two namesakes get confused. The prefix
also says which LEVEL a call works on — rules and tasks are the project's
objects, the profile and the anagrafica are the project itself.
`reference_guide` carries no prefix, because it touches neither: it serves a
file.

Configuration, all through environment variables:
  DB_DIR                  the folder the container sees (default /db). Inside
                          it: `projects.txt`, the registry, and one folder per
                          project holding that project's database. Born
                          optional with a working default in the code
  BACKUP_DIR              VACUUM INTO copies (default: <db dir>/backup)
  PROVISIONAL_DAYS        how long an approved rule lives (default 90)
  ADMIN_AUTH_CODE_DURATION  how long a one-time auth code lives, in minutes
                          (default 5). Born optional with a working default in
                          the code: Unraid does not propagate new variables to
                          containers already installed. The proposal ceiling is
                          NOT here any more — it is `queue_cap`, policy of each
                          project, because this container is multi-tenant
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
  WEB_UI_PASSWORD         the password of the administration UI: it opens
                          every page, approves, mints the one-time auth codes,
                          backs up. It never leaves the browser — NO TOOL
                          carries it, and that is the shape of "what is
                          catastrophic has no tool". It was WEB_MASTER_CODE
                          until v4.0.0: the name said a level, and the level
                          is gone

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
import rules
from rules import (Project, Registry, RulesError, RulesFault, VERSION,
                   check_admin)

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


# The FOLDER, not a file: one project is one database under it, and the
# registry that says which ones are served is the text file inside. Optional
# with a working default in the code, because Unraid does not propagate a new
# variable to containers already installed — and the default is the mapping
# every deployment already has.
DB_DIR = env("DB_DIR", rules.DB_ROOT)
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
WEB_UI_PASSWORD = env("WEB_UI_PASSWORD")
# How long a one-time auth code lives when the maintenance page mints one.
# Optional, with the working default in the engine — and validated at the edge
# by the preflight, because a bad number found here is one line with a name and
# found at the first minting is a traceback in a browser.
ADMIN_AUTH_CODE_DURATION = int(os.environ.get("ADMIN_AUTH_CODE_DURATION")
                               or rules.DEFAULT_AUTH_CODE_MINUTES)
BACKUP_DIR = os.environ.get("BACKUP_DIR") or os.path.join(DB_DIR, "backup")
# Resolved in the engine's cidrs_from_env so the service and the preflight can
# never disagree about what the filter is.
ALLOWED_CIDRS = cidrs_from_env()

registry = Registry(DB_DIR,
                    provisional_days=int(os.environ.get("PROVISIONAL_DAYS") or 90),
                    auth_code_minutes=ADMIN_AUTH_CODE_DURATION)
for _name, _objects in registry.repaired().items():
    log.warning("schema rebuilt at open for %s: %s — somebody had removed these objects",
                _name, ", ".join(_objects))
# The line that catches the half-done rename. A database created empty is
# normal exactly once — the day a project is added — and suspicious every
# other time, because the registry line and the folder on disk are two gestures
# and only one of them was made. The engine has already said it per project,
# with the path; this is the roll-up on the boot line's own level.
if registry.born_empty():
    log.warning("created empty for: %s — expected on a new project, and the mark of a "
                "folder not renamed on any other day", ", ".join(registry.born_empty()))
log.info("registry %s — %s — %s projects — provisional %s days",
         VERSION, registry.file, registry.projects()["count"], registry.provisional_days)

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

# The manual, and since v4.0.0 it is TWO FILES rather than one text cut at a
# marker. They ship inside the image, which is the whole reason they are files
# here and not documents in the vault: a manual that travels with the code
# cannot describe a version that is not running, and a static check can hold it
# against the code it ships with. A copy anywhere else is verified by nobody.
#
# Two files and not a runtime truncation, because the defect the marker existed
# to prevent — the administration text served to somebody without the key —
# stops being a thing to test and becomes a thing that cannot happen: the
# branch without the key never opens the second file. The price is that two
# texts can drift where one could not, which is why the static check walks the
# REAL list of tools and their gates instead of the prose.
_GUIDE = Path(__file__).with_name("reference-guide.md")
_GUIDE_ADMIN = Path(__file__).with_name("reference-guide-admin.md")

# Who signs a gesture that arrived through the tools. Not a person and not a
# guess: the admin code is a CREDENTIAL, not a name, and the surface has no
# other. The page signs 'web ui' for the same reason, and the day either grows
# a login with a name this constant is the one place that changes.
ADMIN_ACTOR = "admin"


def _project(project: str):
    """The reference code, and the whole of what a working chat needs.

    It opens every READ of the project — rules, tasks, anagrafica, for any
    consumer — because inside a project there are no secrets between consumers:
    the isolation is BETWEEN projects, and it is a database each. The refusal
    for a missing code and a wrong one is the same sentence: telling them apart
    would confirm half a code to whoever holds half of one."""
    return registry.project(project)


def _admin(project: str, key: str):
    """The ADMINISTRATION gate: the pair (reference code, admin code), on every
    call. Elevation is per call — MCP has no session, so there is no `su` that
    stays open, and being admin is knowing the code and passing it again.

    The check lives in the engine (`check_admin`), because the admin code is
    per-project DATA: a line in `projects.txt`, not a variable of the
    container. One refusal for both halves, on purpose.

    No log line here: the Gate covers the handshake, so a wrong pair can only
    come from one of Alfredo's own chats — an ordinary refusal, logged once by
    the decorator."""
    return check_admin(registry, project, key)


# =====================================================================
# The manual — the one call that takes nothing
# =====================================================================

@tool
def reference_guide(project: str = "", key: str = "") -> dict:
    """The manual — one of two, and which one depends on what you bring.

    Called BARE it returns the WORK manual: everything a chat holding a project
    code can do, and nothing it cannot. That call is meant to work before you
    know anything else — a chat's bootstrap is kept to the bone, so the first
    call it makes must not be a wall.

    With `project` and the admin code in `key` it returns the OTHER HALF:
    administration, and only that. Not the whole text — the work manual is
    already in the caller's context, and sending it twice is context paid for
    twice. The response says which half it served (`level: work | admin`).

    They are two FILES, not one text cut at a marker: the half you cannot read
    is a file this call never opens. A wrong pair is refused the way every
    administration call is refused — one answer for both halves of it."""
    if (key or "").strip():
        _admin(project, key)
        level, page = "admin", _GUIDE_ADMIN
    else:
        level, page = "work", _GUIDE
    try:
        # Read HERE and not in a helper: a module function that reads a served
        # file is a door — an extra tool calling it serves the same file with
        # no gate, and every check that looks for a read INSIDE a tool goes
        # blind. Measured on this repo, on the two-manual shape.
        return {"version": VERSION, "level": level,
                "guide": page.read_text(encoding="utf-8")}
    except OSError as e:
        # A FAULT, not a refusal: the caller did nothing wrong, the image is
        # incomplete. As a refusal it would leave one quiet line beginning with
        # the word `refused` — a broken image wearing the face of a normal
        # answer, which is the exact inversion the decorator exists to prevent.
        raise RulesFault(f"guide not available in the image: {e}") from e


# =====================================================================
# Reading and working — the reference code
# =====================================================================

@tool
def project_info(project: str) -> dict:
    """The technical structure of the project, and only what is ALIVE in it:
    the DOMAINS with their gloss, the CONSUMERS with kind, brief and specs, the
    GROUPS with their live members, and three counts. The first call of a new
    chat — and if it answers at all, the registry parsed and the database
    opened, so this is also the health check.

    ⚠ FIND YOUR OWN CONSUMER IN THAT LIST, spelled exactly, before you go any
    further: everything here is live, so a name that is missing means the role
    is retired or misspelt. The names read here are the names every other tool
    expects — do not guess a consumer or a group, read it.

    No brief and no specs of the PROJECT: those open `rules_list`, and a
    session start calls both. `project` is the alphanumeric CODE at the top of
    the project's instructions, never its name: no tool lists projects, and no
    error will ever hint at another one."""
    return _project(project).project_info()


@tool
def rules_list(project: str, consumer: str, query: str = "",
               pending: bool = False) -> dict:
    """SESSION START, in one call. The PROJECT first — its brief and its specs,
    identity then the living facts — then YOUR brief and specs, the legend, and
    the rules in force for you: universal, then groups from the widest, then
    exceptions. Every line shows `reach` and the names it reaches. It closes
    with YOUR OPEN TASKS in short form — id, title, urgent, age, urgent first
    then the oldest. The bodies are NOT here: `tasks_get` carries those, and
    the ceiling on it is why.

    `query` filters on title and body and hands back the matching fragment.
    `pending=True` shows the proposal QUEUE instead, with the reasons and the
    proposers — look there before you propose, or you will file a twin.
    Truncation is always declared with the real total.

    This replaces opening the rule files: there is nothing else to read. An
    empty set, or a registry that does not answer, is something to say out
    loud and stop on — not to work around from memory."""
    return _project(project).list_rules(consumer, query, pending)


@tool
def rules_get(project: str, ids: list[str], consumer: str,
              history: bool = False) -> dict:
    """Full detail for the rules you name: body, `reach` with the names,
    permanence, expiry, supersede links both ways, citations expanded. Short
    forms are forgiven on READ — `VA-02` resolves — because there the ID
    identifies a row that exists.

    `history=True` adds the rule's story as DATED GESTURES: timestamp, verb,
    actor, and only the fields that differ from the version before, computed on
    read. A rule's story is the why of what binds you, so it is at this gate
    and not behind the admin code.

    Two ceilings of different natures: more IDs than the cap are REFUSED, not
    trimmed — whoever asked for thirty wanted thirty — and past the byte
    ceiling the text truncates and says so."""
    return _project(project).get_rules(ids, consumer, history)


@tool
def rules_propose(project: str, domain: str, type: str, title: str, body: str,
                  reason: str, reach: str, proposed_by: str,
                  groups: list[str] | None = None,
                  exceptions: list[str] | None = None,
                  supersedes: str = "", source: str = "",
                  consumer_key: str = "") -> dict:
    """The only way a chat writes a rule, and it needs only the reference code:
    a proposal reaches NOBODY until a person approves it on the page, so it
    cannot do harm — and asking a working chat for the admin code just to file
    one would put that code in every chat.

    THERE IS NO `id` PARAMETER: you give the `domain`, two uppercase letters
    the project has declared, and the registry assigns the next number in it.
    `type` is one of R (binding) · M (method) · F (technical fact); retirement
    is a STATE, not a type. `proposed_by` is REQUIRED — an unsigned proposal is
    an orphan, and the door refuses it rather than filing it in your name by
    guesswork.

    THE AUDIENCE IS MIXED. `reach` declares 'all' — no audience at all, never
    deduced — or 'targeted', and then the audience is `groups` UNION
    `exceptions`. Groups are the normal case; exceptions are single consumers
    standing NEXT TO the groups, and they only ever ADD, never subtract.
    Checked at write time: group-with-group overlap is allowed, because the
    anagrafica moves on its own; an exception already inside THIS rule's groups
    is refused — it is either a mistake or a tie that survives in silence the
    day that consumer leaves the group; an exception that belongs to OTHER
    groups is its own business. An overlap that forms LATER blocks nothing: the
    next write on this rule refuses it, and `project_status` reports it.

    `supersedes` names the rule this one REPLACES — a field, never a citation
    in the body: changing a decision is ONE gesture, and the approval retires
    the named rule inside the same decision. Declare the heir's audience
    yourself: a supersede is where the perimeter is re-decided, not inherited.

    CITATIONS in prose are an ID in round brackets, four digits, `(VA-0002)`.
    Anything else is refused naming the field and the token — nothing is
    corrected, no number is spent and no queue slot is taken.

    `consumer_key` is only for a consumer that has been given a secret; where
    none is set, the name is enough."""
    return _project(project).propose(domain, type, title, body, reason, reach,
                                     proposed_by, groups=groups,
                                     exceptions=exceptions, supersedes=supersedes,
                                     source=source, consumer_key=consumer_key)


@tool
def tasks_add(project: str, consumer: str, title: str, body: str,
              created_by: str, urgent: bool = False, idem_key: str = "",
              consumer_key: str = "") -> dict:
    """Open a task on a desk — yours or anybody's. Opening for others is the
    POINT of the log: an audit that finds something routes it to the owner who
    can fix it instead of carrying it. `created_by` is the signature, humans
    included; `urgent` belongs to whoever creates the task and never changes
    afterwards, because a door that let the receiver clear it would put the
    lever in the hand of the one with an interest in clearing it.

    ⚠ Opening a task for a HUMAN consumer does NOT notify them: a human calls
    no tool, and their mail is seen by whoever reads `tasks_overview` or the
    administration page. Tasks are not a notification channel to the owner.

    `idem_key` makes the call safe to repeat: the same key on the same desk
    hands back the pending task instead of a twin."""
    return _project(project).task_add(consumer, title, body, created_by,
                                      urgent=urgent, idem_key=idem_key,
                                      consumer_key=consumer_key)


@tool
def tasks_list(project: str, consumer: str, query: str = "", since: str = "",
               until: str = "", authored: bool = False) -> dict:
    """One desk, short form, ordered by the server: urgent first, then oldest —
    so when the cap cuts, it cuts the FRESH work and never the thing that has
    been waiting. Recently closed tasks trail the list; `since`/`until` open
    the window on the older closed ones. `query` filters and returns the
    fragment.

    `authored=True` turns the view round: the tasks THIS consumer opened on
    other desks, with status and outcome. A task for somebody else is also a
    message, and a sender who cannot see it close sends it again.

    A task pending for more than the staleness window comes out MARKED, and
    that is all: tasks do not expire. Truncation is always declared with the
    real total."""
    return _project(project).task_list(consumer, query, since, until, authored)


@tool
def tasks_get(project: str, ids: list[str]) -> dict:
    """Full bodies for the tasks you name, citations expanded — a broken
    pointer is named inside the text, never refused.

    It reads ANY task of the project, deliberately: reads are project-wide and
    the boundary is the reference code. That is what makes the `authored` view
    and an audit possible at all. The two ceilings are `rules_get`'s: too many
    IDs is refused, too many bytes truncates and says so."""
    return _project(project).task_get(ids)


@tool
def tasks_close(project: str, id: str, by: str, outcome: str = "",
                reason: str = "", consumer_key: str = "", key: str = "") -> dict:
    """Close a task: ONE gesture, two verdicts. `outcome` completes it,
    `reason` drops it — exactly one of the two, and the guarantee is the
    schema's CHECK, not this docstring. Closed is closed: no amend, no reopen.

    Closing a task you do not own takes the ADMIN CODE in `key`, and the
    refusal names the owner and the gate. It is the one declared exception to
    the flat ladder — one factor, not two — because a task closed wrong reopens
    as a new task, while a rule retired wrong loses its ID and its
    continuity."""
    admin = bool((key or "").strip())
    prj = _admin(project, key) if admin else _project(project)
    return prj.task_close(id, by, outcome=outcome, reason=reason,
                          consumer_key=consumer_key, admin=admin)


@tool
def tasks_amend(project: str, id: str, by: str, title: str = "", body: str = "",
                consumer: str = "", consumer_key: str = "", key: str = "") -> dict:
    """Amend an OPEN task: title, body, or `consumer` to hand it to the right
    desk — the reassignment is named in the story, which keeps both owners.
    Only what you pass changes.

    `urgent` has no parameter here, and that is not an oversight: it belongs to
    whoever created the task, and that door does not exist. Amending a task you
    do not own takes the admin code in `key`, like closing one."""
    admin = bool((key or "").strip())
    prj = _admin(project, key) if admin else _project(project)
    return prj.task_amend(id, by, title=title, body=body, consumer=consumer,
                          consumer_key=consumer_key, admin=admin)


# =====================================================================
# Administration — the admin code, and a one-time code on top of it for
# anything that changes something that already exists
# =====================================================================

@tool
def project_amend(project: str, entity: str, name: str, action: str,
                  fields: dict | None = None, reason: str = "",
                  auth_code: str = "", key: str = "") -> dict:
    """Amend the project itself or its STRUCTURE — profile, domains, consumers,
    groups. Rules and tasks are the project's OBJECTS and have tools of their
    own; the prefix says which level a call works on.

    `entity`: project | domain | consumer | group. `action`: create | amend |
    retire | revive. `fields` carries only what changes.

    THE LADDER IS FLAT: `create` passes on the admin code — a created thing is
    attached to nothing, and the only door that ties a rule to an audience is
    `rules_propose`, which goes past a person. Every `amend`, `retire` and
    `revive` — renames, briefs, group membership, `queue_cap` — asks for a
    one-time `auth_code` AS WELL, minted on the administration page: it lives
    minutes and it is burned in the same transaction as the SUCCEEDED gesture,
    so a refusal rolls it back and a typo costs nothing. Spent or expired it is
    nothing, and alone it elevates nobody. ONE exception downward, declared:
    `specs` alone — the project's or a consumer's — travels on the reference
    code, because that is operational data and not identity.

    A MIXED `fields` presented with the lower credential is refused WHOLE,
    naming the field that needs the higher port. The authorised subset is never
    written and the rest dropped: one gesture, one door, no silent halves.

    The guarantees live in the schema and this door repeats them speaking: a
    domain's code is immutable; retiring a domain with rules in force is
    refused naming them; retiring anything costs a reason; creating a group
    that mirrors a rule's exceptions is refused naming the rule, while ADDING a
    member passes even when it covers one — that overlap is repairable, so it
    goes to `project_status` and is refused at the next write on that rule. A
    group edit or a consumer retire that would leave a rule in force with ZERO
    consumers is refused naming the rules: that damage is silent. Names of
    consumers and groups are amendable, and the OLD NAME STOPS RESOLVING — the
    verdict lists what must be updated outside the registry: skill files, chat
    instructions, scheduled prompts."""
    fields = dict(fields or {})
    port = Project.port_for(entity, action, fields)
    if port == "project":
        prj = _project(project)
    else:
        if not (key or "").strip():
            # The MIXED call, refused whole and naming the field that costs
            # more — and refused in the ENGINE, where a suite can exercise it
            # without a server. Only when no pair was presented: with a key in
            # hand the ordinary refusals speak for themselves.
            Project.refuse_mixed(entity, action, fields)
        prj = _admin(project, key)
    return prj.amend_project(entity, name, action, fields, reason=reason,
                             actor=ADMIN_ACTOR, auth_code=auth_code)


@tool
def rules_amend(project: str, id: str, reach: str, groups: list[str],
                exceptions: list[str], expected_version: int, reason: str,
                auth_code: str, key: str) -> dict:
    """The PERIMETER of a rule in force, NARROWED, as one atomic gesture — and
    at two factors, because it is a modification like any other.

    The new effective set of consumers — the UNION of groups and exceptions —
    must be CONTAINED in the old one. The engine computes both and compares: it
    does not trust the shape, because two shapes can describe the same people
    and one shape can describe different people a week later. And never to
    ZERO: a narrowing that leaves nobody is a retirement in disguise, and that
    gesture goes through `rules_retire`, where it costs a reason.

    WIDENING binds somebody new, which is promulgation, and promulgation goes
    past a person: propose a supersede carrying the wider audience and let the
    approval retire this one in the same decision. The CONTENT is not touched
    from here either — a rule that must SAY something else is a new decision.

    `expected_version` guards against writing over something you did not read:
    it is the version `rules_get(history=True)` last showed you."""
    return _admin(project, key).amend_rule(id, reach, groups, exceptions,
                                           expected_version, reason,
                                           actor=ADMIN_ACTOR, auth_code=auth_code)


@tool
def rules_retire(project: str, id: str, reason: str, auth_code: str,
                 key: str) -> dict:
    """End a rule without an heir. It is the least reversible gesture on this
    surface — the way back is a proposal and a human approval — so it takes two
    factors: the admin code in `key` and a one-time `auth_code`.

    The reason is the price: a rule that disappears without one comes back as
    an argument. With an heir do not come here — propose the heir with
    `supersedes`, and the approval retires the victim inside the same
    decision."""
    return _admin(project, key).retire(id, reason, actor=ADMIN_ACTOR,
                                       auth_code=auth_code)


@tool
def project_status(project: str, key: str) -> dict:
    """The project's health in one report: counts computed on read and never
    stored, rules expiring with their reasons, the pending queue, prose
    citations pointing at retired or missing rules, and the overlaps that FORMED
    after the fact — an exception a group has since swallowed, a domain or a
    consumer nothing reaches any more.

    It is also the ONE place the RETIRED are readable — domains, consumers and
    groups, with the date and the reason. `project_info` shows the live alone,
    and a retired name is still a name TAKEN: a create on it is refused, so
    the way past that refusal is `revive`, and reviving needs a target you can
    see.

    Structural pointers are not checked because they are impossible: the schema
    refuses them at write time. It REPORTS and does not correct: what it finds
    is sorted out by whoever has the context, not by whoever happens to be
    running an audit. It declares what it counted."""
    return _admin(project, key).status()


@tool
def rules_export(project: str, key: str, consumer: str = "",
                 expand: bool = False) -> dict:
    """The full corpus in one call, for a migration or for reading off-site.
    `consumer` narrows it to one perimeter; `expand` inlines the titles of the
    cited rules.

    Mind your client's result ceiling: this is the tool that meets it first,
    and the answer says how many bytes it is so the next call can be aimed."""
    return _admin(project, key).export(consumer, expand)


@tool
def tasks_overview(project: str, key: str) -> dict:
    """Every desk at once, short form, ceilings declared — the cross-view that
    lets an audit route work to the owner who can do it.

    It also counts the urgent tasks BY CREATOR, and that is the guard against
    urgency inflation: if one creator's column is all urgent, what gets
    corrected is that skill, not the tasks it filed. Reading it moves nothing:
    no counter, no timestamp."""
    return _admin(project, key).task_overview()


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
        uvicorn.Server(cfg(web.build(registry=registry, log=log,
                                     master=WEB_UI_PASSWORD, refusal=RulesError,
                                     fault=RulesFault,
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
             DB_DIR, os.geteuid(), WEB_BIND_HOST, WEB_PORT)
    asyncio.run(_serve())
