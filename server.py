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
- writing is a two-step affair. A chat PROPOSES; the batch is approved with an
  ed25519 signature over its digest. The registry holds only the public half.

Every tool that acts on a rule is prefixed `rules_`: the vault's tools (status,
history, diff, search...) live in the same chat, and two namesakes get confused.
The prefix stays `rules_` even though the repository is called codifier-mcp —
inside this project "rules" is the only subject there is. The two manuals —
`reference_guide` and `legislator_guide` — carry no prefix, because they touch
no rule: they serve a file.

Configuration, all through environment variables:
  DB_PATH                 the single database (default /db/rules.db)
  BACKUP_DIR              VACUUM INTO copies (default: <db dir>/backup)
  ADMIN_ACCESS_CODE       the maintenance code: it travels on every call
  APPROVAL_PUBKEY         ed25519 PUBLIC key, raw base64. Never the private half
  APPROVAL_GRACE_UNTIL    YYYY-MM-DD: until then a batch passes unsigned
  PROVISIONAL_DAYS        how long an approved rule lives (default 90)
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
  WEB_PORT                reserved for the read-only web UI, not built yet. Inert here,
                          and declared in the template already because Unraid
                          does not propagate new variables to installed containers
"""
from __future__ import annotations

import logging
import os
import secrets
import sys
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.auth.providers.github import GitHubProvider

# The common engine: the gate, the refusal conversion and the config helpers
# live there since the adoption, pinned to a TAG in requirements.txt. The
# reasoning each piece carries stays in its module's docstring, next to the
# code it describes. What stays HERE is everything the engine cannot know:
# which login, which filter, which error class is a refusal and which a fault.
from mcp_common_engine import (VERSION as ENGINE_VERSION, cidrs_from_env,
                               describe_cidrs, log_level_from_env)
from mcp_common_engine.gate import Gate
from mcp_common_engine.refusals import make_tool
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
ADMIN_CODE = env("ADMIN_ACCESS_CODE")
PORT = int(env("PORT", "3001"))
BACKUP_DIR = os.environ.get("BACKUP_DIR") or os.path.join(os.path.dirname(DB_PATH), "backup")
# Resolved in the engine's cidrs_from_env so the service and the preflight can
# never disagree about what the filter is.
ALLOWED_CIDRS = cidrs_from_env()

registry = Registry(DB_PATH,
                    public_key=os.environ.get("APPROVAL_PUBKEY", ""),
                    grace_until=os.environ.get("APPROVAL_GRACE_UNTIL", ""),
                    provisional_days=int(os.environ.get("PROVISIONAL_DAYS") or 90))
if registry.repaired:
    log.warning("schema rebuilt at open: %s — somebody had removed these objects",
                ", ".join(registry.repaired))
# A schema change on a database in service happens once and cannot be undone, so
# the boot that does it says so. It is deliberately a short list: the migration
# adds a column and converts NOTHING — the rules go back in by hand, one at a
# time, and an engine that rewrote them behind the author's back would be moving
# the very pointers that pass exists to re-decide.
if registry.migrated:
    log.warning("schema migrated at open: %s", ", ".join(registry.migrated))
log.info("registry %s — %s — %s projects — approval: %s",
         VERSION, DB_PATH, registry.projects()["count"],
         "grace open until " + registry.grace_until if registry.in_grace() else "signature required")

auth = GitHubProvider(
    client_id=env("GITHUB_CLIENT_ID"),
    client_secret=env("GITHUB_CLIENT_SECRET"),
    base_url=BASE_URL,
    jwt_signing_key=env("JWT_SIGNING_KEY"),
    require_authorization_consent=True,
)

mcp = FastMCP("codifier-mcp", auth=auth)


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

# The two manuals. Both ship inside the image, which is the whole reason they
# are files here and not documents in the vault: a manual that travels with the
# code cannot describe a version that is not running, and a static check can
# hold it against the code it ships with. A copy anywhere else is verified by
# nobody.
_GUIDE = Path(__file__).with_name("reference-guide.md")
_LEGISLATOR = Path(__file__).with_name("legislator-guide.md")


def _admin(code: str) -> None:
    """The MAINTENANCE gate: writing, the reads that step outside the caller's
    own perimeter (status, audit, history, export, the registry index), and the
    manual that is for whoever maintains the corpus rather than for whoever
    works under it.

    It is not session state: the code travels on every call, so there is no
    "mode" left open by accident."""
    if not secrets.compare_digest((code or "").strip(), ADMIN_CODE):
        # No log line here, and that is a change from v1.2. It used to warn,
        # on the argument that a run of wrong codes is the only signal that
        # somebody is trying — but since v1.2 the Gate covers the handshake, so
        # nobody who is not Alfredo's own GitHub login, from an allowed range,
        # ever reaches a tool at all. A wrong code can now only come from one
        # of his own chats, which makes it an ordinary refusal and not a
        # security signal. The decorator logs it once, at INFO, with the tool
        # name and the reason — more than this line ever said. Logging here as
        # well put the same event at two heights, one of them the Gate's.
        raise RulesError("admin code missing or wrong: this is done only by the chat that "
                         "MAINTAINS the registry, with the code Alfredo gives it. Do not try "
                         "to guess it: ask.")


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
    """The manual for this registry: the model, what a consumer and a scope are,
    the life of a rule from proposal to retirement, which tool for which job, and
    what the errors mean. Read it before your first write."""
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
    whole project, which is the maintainer's view."""
    return registry.pending(project, consumer)


# =====================================================================
# Proposing — no admin code: a proposal reaches nobody
# =====================================================================

@tool
def rules_propose(project: str, domain: str, type: str, title: str, body: str,
                  scopes: list[str], reason: str, proposed_by: str = "",
                  changelog: str = "", source: str = "", legacy_id: str = "") -> dict:
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
    `proposed_by` is your own consumer name — it is what makes rules_pending
    able to show you your own. `legacy_id` is the old markdown identifier, if
    this rule had one: it is recorded so the citations can be mapped afterwards,
    and no two rules may claim the same one.

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
                            proposed_by, changelog, source, legacy_id)


# =====================================================================
# Approving — the admin code, and a signature over the batch
# =====================================================================

@tool
def rules_batch(project: str, code: str) -> dict:
    """MAINTENANCE. The pending proposals, whole, plus the DIGEST of the batch.

    You sign the BATCH, never the single rule. Two reasons, and both were paid
    for: at the twelfth signature in a row a person signs without reading; and
    seen side by side, three proposals that say the same thing become visible as
    what they are.

    Sign the digest string on your own machine and pass the base64 signature to
    rules_approve. The private key never enters this conversation, and the
    registry holds only the public half. If a proposal arrives in between, the
    digest changes and the old signature is refused — that is on purpose.

    Each proposal carries its `reason`: the why you are letting in is on the
    table where the decision happens, not a history call away."""
    _admin(code)
    return registry.batch(project)


@tool
def rules_approve(project: str, digest: str, code: str, signature: str = "") -> dict:
    """MAINTENANCE. Approve the whole batch: the proposals become ACTIVE and
    PROVISIONAL, with an expiry date.

    Provisional is the point of the whole mechanism. Rules did not pile up
    because somebody wrote them without permission — they piled up because
    adding one costs a call and removing one costs a decision nobody takes.
    Expiry inverts that: staying costs a decision, going is free.

    `digest` must be the current one from rules_batch. Inside the grace window
    the signature may be omitted, and the approval is recorded AS UNSIGNED."""
    _admin(code)
    return registry.approve(project, digest, signature)


@tool
def rules_deny(project: str, ids: list[str], reason: str, code: str) -> dict:
    """MAINTENANCE. Refuse one or more proposals, with a reason. No signature is
    asked for: refusing cannot do harm.

    The row STAYS and the ID is burnt. It no longer BLOCKS a re-proposal: since
    the counter assigns the number, the same text filed again simply takes a new
    one. What the refusal buys is the REASON — rules_pending shows it to whoever
    proposed it, so silence becomes an answer and they learn something instead of
    guessing. Reading your own refusals is a habit now, not a guard rail."""
    _admin(code)
    return registry.deny(project, ids, reason)


@tool
def rules_renew(project: str, ids: list[str], code: str,
                signature: str = "", days: int = 0) -> dict:
    """MAINTENANCE. Push the expiry of provisional rules forward. Signed, because
    keeping a rule alive is letting it in again.

    The digest to sign is returned by the error when the signature is missing or
    wrong, and in the verdict when it succeeds."""
    _admin(code)
    return registry.renew(project, ids, signature, days)


@tool
def rules_promote(project: str, ids: list[str], code: str, signature: str = "") -> dict:
    """MAINTENANCE. From provisional to PERMANENT: no expiry, it never leaves on
    its own again. Rare, deliberate, and signed.

    Think twice: a permanent rule is one you are promising to notice when it
    goes stale, because nothing else will notice for you."""
    _admin(code)
    return registry.promote(project, ids, signature)


# =====================================================================
# Maintaining rules
# =====================================================================

@tool
def rules_fix(project: str, id: str, expected_version: int, reason: str, code: str,
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
    _admin(code)
    return registry.amend(project, id, expected_version, reason,
                          title or None, body or None, type or None, changelog or None)


@tool
def rules_widen(project: str, id: str, scopes: list[str], code: str, reason: str = "") -> dict:
    """MAINTENANCE. Make a rule ALSO reach somebody else: one more row, and the
    scope it already belonged to is not touched — that scope has other tenants
    who have nothing to do with this rule.

    This is the difference between moving a rule and widening a group, and they
    are two different things. To change who is in a GROUP, use rules_scope_edit,
    and know that it changes the perimeter of every rule pointing at it."""
    _admin(code)
    return registry.widen(project, id, scopes, reason)


@tool
def rules_narrow(project: str, id: str, scopes: list[str], code: str) -> dict:
    """MAINTENANCE. Stop a rule reaching a scope. Symmetric to rules_widen: one
    row less. If it ends up with no scope at all the verdict says so — a rule
    that reaches nobody is not retired, it is invisible, which is worse."""
    _admin(code)
    return registry.narrow(project, id, scopes)


@tool
def rules_retire(project: str, id: str, reason: str, code: str,
                 superseded_by: str = "", changelog: str = "") -> dict:
    """MAINTENANCE. Retire a rule: it leaves the consumers' lists, but the row
    STAYS. The ID is never reused and citations must keep resolving. There is no
    deletion.

    `superseded_by` when a new rule takes its place (create it first). The
    verdict lists the active rules that still cite this one: those need
    fixing."""
    _admin(code)
    return registry.retire(project, id, reason, superseded_by, changelog)


# =====================================================================
# Projects, consumers, scopes
# =====================================================================

@tool
def rules_registry(code: str) -> dict:
    """MAINTENANCE. The COMPLETE list of projects in the registry, CODES
    INCLUDED. This is the only door codes come out of, which is why it wants the
    admin code. It is for Alfredo, to recover a code he has mislaid — working
    chats already have theirs, at the top of their instructions."""
    _admin(code)
    return registry.projects()


@tool
def rules_status(project: str, code: str) -> dict:
    """MAINTENANCE. The verdict on the registry: database integrity, journal
    mode, file permissions, counts by domain and by consumer, how many rules
    have expired without being retired, how many batches were approved. The
    counts cover every perimeter, which is why it wants the admin code."""
    _admin(code)
    return registry.status(project)


@tool
def rules_check(project: str, code: str) -> dict:
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
    _admin(code)
    return registry.check(project)


@tool
def rules_history(project: str, id: str, code: str) -> dict:
    """MAINTENANCE. How that rule changed over time: one row per version, with
    date, action and REASON, plus the perimeter in two columns — `scopes` what
    was declared, `consumers` who was actually reached that day.

    History is written by the database TRIGGERS, not by these tools, so a change
    made by hand with sqlite3 is in here too. It serves whoever MAINTAINS the
    rule, not whoever applies it: the latter only needs the text in force."""
    _admin(code)
    return registry.history(project, id)


@tool
def rules_diff(project: str, id: str, version_a: int, version_b: int, code: str) -> dict:
    """MAINTENANCE. What changed between two versions of ONE rule (the numbers
    come from rules_history). Whole versions are kept, not diffs: the comparison
    is computed on the fly between any two, however far apart."""
    _admin(code)
    return registry.compare(project, id, version_a, version_b)


@tool
def rules_export(project: str, code: str, consumer: str = "", expand: bool = False) -> dict:
    """MAINTENANCE. A Markdown snapshot, to be written into the vault with the
    archivist's write_file. Two uses:
      with `consumer`     only that perimeter, rules in force, widest first
      without `consumer`  the whole project, retired rules included — the
                          maintenance document, and the copy that goes into git

    Every rule carries its `reason`, and the whole-project export the last
    `event` too: this is the maintenance reading, where the why is on the page.

    `expand` decides how citations read: compact `(VA-0002)` by default, or
    carrying the current title of what they point at. This is the only reader
    offered the choice, because it is read by a person — rules_list and rules_get
    always expand, since a chat is not given an option it can get wrong.

    It is a DERIVATIVE: the truth stays in the database and this regenerates. Do
    not edit it and expect the registry to notice."""
    _admin(code)
    return registry.export(project, consumer, expand)


@tool
def rules_project_create(project_code: str, name: str, consumers: list, domains: dict,
                         code: str, description: str = "") -> dict:
    """MAINTENANCE. Create a new project. Needed before any rule.

    `project_code`: the handle that project will be addressed by forever — 8 to
    32 alphanumeric characters, generated by Alfredo, to be put at the top of
    the project's instructions. It is not the name and cannot be derived from it.
    `consumers`: [["architect","chat"], ["update-tax","skill"]] — whoever
    downloads rules. A person is not a consumer: a rule that binds a person says
    so in its body.
    `domains`: {"VA":"vault and files", "ST":"structure"} — two uppercase
    letters each.

    Every consumer is given a scope of its own by the database. A new project
    does not need a new container: consumers and domains are data."""
    _admin(code)
    return registry.create_project(project_code, name, consumers, domains, description)


@tool
def rules_project_rekey(project: str, new_project_code: str, code: str) -> dict:
    """MAINTENANCE. Change a project's access code (if it ended up somewhere it
    should not have). The rules are untouched: inside the registry a project is
    addressed by name, and the code is only the door.

    Update the project's instructions BEFORE closing the chat: the old code no
    longer reaches anything."""
    _admin(code)
    return registry.rekey_project(project, new_project_code)


@tool
def rules_consumers_add(project: str, consumers: list, code: str) -> dict:
    """MAINTENANCE. Add consumers to a project — chats or skills. Each one gets
    a scope of its own name, made by the database.

    Only adding: removing a consumer would orphan the rules aimed at it. And a
    consumer is never RENAMED — a renamed consumer is a different consumer, and
    the rules that reached it need reviewing, not dragging along behind a name.
    Create the new one and retire the old."""
    _admin(code)
    return registry.add_consumers(project, consumers)


@tool
def rules_domains_add(project: str, domains: dict, code: str) -> dict:
    """MAINTENANCE. Add ID domains to a project: {"LQ":"liquidity"}. Two
    uppercase letters each. Only adding, for the same reason."""
    _admin(code)
    return registry.add_domains(project, domains)


@tool
def rules_scope_create(project: str, name: str, members: list[str], code: str) -> dict:
    """MAINTENANCE. Create a named group of consumers, e.g. "deliberativi" over
    the four chats that deliberate.

    At least two members: every consumer already has a singleton scope of its
    own name, made by the database, so a one-member group would add nothing but
    a second name for the same set. A group cannot take a consumer's name —
    consumers and scopes share one namespace, and that is the right
    constraint."""
    _admin(code)
    return registry.create_scope(project, name, members)


@tool
def rules_scope_edit(project: str, name: str, code: str,
                     add: list[str] = None, remove: list[str] = None) -> dict:
    """MAINTENANCE. Change who is in a GROUP scope. Careful: this changes the
    perimeter of EVERY rule pointing at it, and the verdict says how many that
    is. To make one rule reach one more consumer, use rules_widen instead.

    A managed scope — a consumer's singleton, or _ALL_ — is refused: its
    membership is fixed by construction, and the refusal comes from the
    database."""
    _admin(code)
    return registry.edit_scope(project, name, add, remove)


# =====================================================================
# Migration and service
# =====================================================================

@tool
def rules_import(project: str, rules: list[dict], reason: str, code: str,
                 permanent: bool = True) -> dict:
    """MAINTENANCE. Bulk import for the MIGRATION from the Markdown files. Only
    on an EMPTY project: a migration happens once, on a clean table.

    Each item: {"id","type","title","body","scopes",["changelog"],["source"]}.
    Rejected rules are listed with the reason and the others go through; fix
    them and file them with rules_propose. rules_check runs in its wake, and the
    broken pointers it finds were already in the Markdown — they were just not
    visible."""
    _admin(code)
    return registry.import_rules(project, rules, reason, permanent)


@tool
def rules_backup(code: str) -> dict:
    """MAINTENANCE. A quiescent copy of the WHOLE database (VACUUM INTO) into
    the backup directory: it opens without recovery, and it is the one to take
    off-site. Safe on a live database.

    In WAL the database is THREE files, so copying one by hand is a corrupt
    backup. ZFS snapshots stay the main net."""
    _admin(code)
    return registry.backup(BACKUP_DIR)


# =====================================================================
# The legislator's manual
# =====================================================================
#
# THE NAME, because this is a decision that will be read back. `rules_*` is
# reserved for the tools that act on rules, and this one does not touch the
# registry at all: it opens a file, exactly like `reference_guide`, which is
# also prefixless. So the two manuals are named as a pair — reference_guide and
# legislator_guide — and the prefix keeps meaning what it means. "Legislator"
# is not decoration either: the whole point of the split is that redacting and
# promulgating are two jobs, and this is the manual of the first one.
#
# THE CODE. Not secrecy — every word in that file could be published without
# harm, and its existence is on the surface by construction: this docstring
# travels in tools/list to anyone the Gate lets in, so what the code protects is
# the TEXT and never the news that the text exists. It is role hygiene: the
# manual that teaches how rules are decided has one audience, and the damage is
# not that somebody learns it exists — it is that a chat which has just READ how
# rules are written is one step from writing its own, which is the single thing
# the whole registry exists to prevent.
#
# And nobody arrives here by browsing. Whoever administers the corpus is given
# the maintenance code in the project's own instructions, and reading this
# manual is the step after that. The manual of USE does not mention this tool at
# all, deliberately: it is not the road, and pointing at a door somebody cannot
# open is an invitation and not a service. The road is the sequence.

@tool
def legislator_guide(code: str) -> dict:
    """MAINTENANCE. The manual for deciding WHAT DESERVES TO BE A RULE: the
    tests that tell a rule from a step, from a missing manual and from a
    reminder; who a rule has to reach; what the `reason` field must carry; and
    what to ask at renewal.

    This is not the manual for using the registry. That one is
    `reference_guide`, it needs no code, and it is the one a working chat
    wants. Do not read this one to learn how to call a tool."""
    _admin(code)
    try:
        return {"version": VERSION, "guide": _LEGISLATOR.read_text(encoding="utf-8")}
    except OSError as e:
        # Same reasoning as reference_guide: a missing file in the image is
        # ours, not the caller's.
        raise RulesFault(f"legislator guide not available in the image: {e}") from e


if __name__ == "__main__":
    # The host is READ, not spelled out again: this line is what you look at to
    # confirm an update took, and BIND_HOST is the one field on it where being
    # wrong matters — 0.0.0.0 exposes the service to the LAN, and a startup line
    # that keeps saying 127.0.0.1 would be lying about exactly that.
    _HOST = os.environ.get("BIND_HOST", "127.0.0.1")
    log.info("codifier-mcp %s — engine %s — starting on %s:%s — base_url %s — allowed user: %s "
             "— IP filter: %s — token store: %s — db: %s (process uid %s) — web UI: %s",
             VERSION, ENGINE_VERSION, _HOST, PORT, BASE_URL, ALLOWED_LOGIN, describe_cidrs(ALLOWED_CIDRS),
             os.environ.get("FASTMCP_HOME", "(default — NOT persistent!)"),
             DB_PATH, os.geteuid(),
             os.environ.get("WEB_PORT") or "off (not built yet)")
    mcp.run(transport="http", host=_HOST, port=PORT)
