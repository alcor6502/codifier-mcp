#!/usr/bin/env python3
"""
test_surface.py — the static checks, the ones that cover the hole the runtime
tests cannot see.

test_collaudo.py exercises the ENGINE. Nothing there ever imports server.py,
because importing it would drag in FastMCP, a GitHub OAuth provider and a
listening socket. So the seam between the two files — the one place where a
renamed parameter goes unnoticed until a chat calls the tool — is checked here,
by reading the source instead of running it:

  1. every registry.<method>(...) in server.py exists on Registry, and the call
     is compatible with its signature;
  2. every tool that reaches a MUTATING method of the engine goes through
     _admin first. The set of mutating methods is DERIVED from rules.py, not
     typed out here: a list copied into a second file drifts, and this one would
     drift towards "unguarded";
  3. no docstring names a tool that does not exist. The v4.0 docstring of
     rules_list pointed at `rules_projects`, which was never a tool — a broken
     pointer inside the very text that enters every chat's context.

No network, no FastMCP, no Docker: this parses the files.
Run it with `python3 test_surface.py`. Exit code 0 means green.
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "server.py")
ENGINE = os.path.join(HERE, "rules.py")

OK = FAIL = 0


def ok(cond, label: str, extra="") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {extra}")


def parse(path: str) -> ast.Module:
    with open(path, encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def source(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


SERVER_TREE = parse(SERVER)
ENGINE_TREE = parse(ENGINE)
SERVER_SRC = source(SERVER)
RULES_SRC = source(ENGINE)
PREFLIGHT_SRC = source(os.path.join(HERE, "preflight.py"))
GUIDE_SRC = source(os.path.join(HERE, "reference-guide.md"))

# =====================================================================
# The engine: signatures, and which methods write
# =====================================================================

REGISTRY = next(n for n in ENGINE_TREE.body
                if isinstance(n, ast.ClassDef) and n.name == "Registry")

METHODS: dict[str, ast.FunctionDef] = {
    n.name: n for n in REGISTRY.body if isinstance(n, ast.FunctionDef)}

_WRITES = re.compile(r"\b(INSERT|UPDATE|DELETE)\b", re.IGNORECASE)


def _writes_directly(fn: ast.FunctionDef) -> bool:
    return any(isinstance(n, ast.Constant) and isinstance(n.value, str) and _WRITES.search(n.value)
               for n in ast.walk(fn))


def _calls_on_self(fn: ast.FunctionDef) -> set[str]:
    out = set()
    for n in ast.walk(fn):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "self"):
            out.add(n.func.attr)
    return out


def mutating_methods() -> set[str]:
    """A method mutates if it carries a write statement, or reaches one through
    another method of the class. Transitive, because propose() writes through
    _write_refs() and approve() through _require_signature()."""
    direct = {name for name, fn in METHODS.items() if _writes_directly(fn)}
    edges = {name: _calls_on_self(fn) for name, fn in METHODS.items()}
    changed = True
    while changed:
        changed = False
        for name, called in edges.items():
            if name not in direct and called & direct:
                direct.add(name)
                changed = True
    return {m for m in direct if not m.startswith("_")}


MUTATING = mutating_methods()

# Deliberate exceptions, each with the reason it is one. If an exception ever
# loses its reason it stops being a decision and becomes an oversight, so the
# reason is data here and the test reads it.
UNGATED_ON_PURPOSE = {
    "rules_propose": "a proposal reaches nobody until its batch is approved, so it "
                     "cannot do harm — and asking a working chat for the maintenance "
                     "code just to file one would put that code in every chat",
}

print("\n== the engine, as the seam sees it ==")
ok(len(METHODS) > 30, f"Registry parsed: {len(METHODS)} methods")
ok({"propose", "approve", "retire", "import_rules"} <= MUTATING,
   f"the mutating set is derived, not typed: {len(MUTATING)} methods")
ok("list_rules" not in MUTATING and "check" not in MUTATING,
   "and it does not sweep the read-only ones in with them")

# =====================================================================
# 1 · every call into the engine exists, with a compatible signature
# =====================================================================

print("\n== server.py -> rules.py: every call lands ==")


def signature(fn: ast.FunctionDef):
    pos = [a.arg for a in fn.args.posonlyargs + fn.args.args][1:]     # drop self
    kwonly = [a.arg for a in fn.args.kwonlyargs]
    n_defaults = len(fn.args.defaults)
    required = pos[:len(pos) - n_defaults] if n_defaults else pos
    required += [a.arg for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults) if d is None]
    return pos, kwonly, set(required)


CALLS = [n for n in ast.walk(SERVER_TREE)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
         and isinstance(n.func.value, ast.Name) and n.func.value.id == "registry"]

ok(len(CALLS) >= 25, f"{len(CALLS)} calls into the engine found in server.py")

for call in CALLS:
    name = call.func.attr
    where = f"line {call.lineno}"
    if name not in METHODS:
        ok(False, f"registry.{name} exists", where)
        continue
    pos, kwonly, required = signature(METHODS[name])
    given_pos = len(call.args)
    given_kw = {k.arg for k in call.keywords if k.arg}
    problems = []
    if given_pos > len(pos):
        problems.append(f"{given_pos} positional arguments for {len(pos)} parameters")
    unknown = given_kw - set(pos) - set(kwonly)
    if unknown:
        problems.append(f"unknown keywords: {', '.join(sorted(unknown))}")
    covered = set(pos[:given_pos]) | given_kw
    missing = required - covered
    if missing:
        problems.append(f"missing required: {', '.join(sorted(missing))}")
    ok(not problems, f"registry.{name}(...) matches its signature",
       f"{where}: {'; '.join(problems)}")

# =====================================================================
# 2 · every tool that writes goes through _admin
# =====================================================================

print("\n== every write passes the maintenance gate ==")


def is_tool(fn: ast.FunctionDef) -> bool:
    """Registered either as @mcp.tool or through server.py's own @tool wrapper —
    which is the one they all use, and the check below enforces it."""
    for d in fn.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        if isinstance(target, ast.Attribute) and target.attr == "tool":
            return True
        if isinstance(target, ast.Name) and target.id == "tool":
            return True
    return False


# ast.walk, not SERVER_TREE.body. The census used to look at module level only,
# and that is not where a tool has to be: `if os.environ.get("FEATURE"): @tool
# def rules_wipe(...)` is nested, so it escaped the census, escaped the count,
# escaped the async ban AND escaped the check that every write passes _admin —
# an ungated mutating tool on the surface with the suite at 262 passed, 0
# failed. Demonstrated, not imagined.
TOOLS = [n for n in ast.walk(SERVER_TREE)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and is_tool(n)]
TOOL_NAMES = {t.name for t in TOOLS}

# The other door, and it carries no decorator line at all: `tool(rules_purge)`
# as a plain statement registers the function just as well, and every check
# that looks for a decorator is blind to it. Same for handing it to anything
# else. `tool` may be USED only as a decorator.
_TOOL_CALLS = [n for n in ast.walk(SERVER_TREE)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "tool"]
ok(not _TOOL_CALLS, "`tool` is only ever used as a decorator, never called",
   [ast.unparse(n)[:40] for n in _TOOL_CALLS])

# An EQUALITY against what the file says, never a threshold. `>= 25` against
# thirty tools tolerates five escapees, and one escapee is the realistic
# mistake — you forget a line, not five. Worse, the one way a tool escapes
# quietly is `@tool()` with the brackets: that is an ast.Call and not an
# ast.Name, so it slips the list AND satisfies the threshold, while at boot it
# raises TypeError because the decorator takes the function itself. The suites
# never import server.py, so nothing else would ever see it.
#
# Three counts that have to agree, because they fall in different ways. The AST
# list and the bare-line count BOTH lose a tool written `@tool()` — it matches
# neither — so on their own the two stay in step while a tool escapes. The
# wider line count is the third leg: it sees the brackets.
def _names_tool(d) -> bool:
    node = d.func if isinstance(d, ast.Call) else d
    return isinstance(node, ast.Name) and node.id == "tool"


_DECORATED = [n for n in ast.walk(SERVER_TREE)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and any(_names_tool(d) for d in n.decorator_list)]
_WRAPPED = [n.name for n in _DECORATED
            if any(isinstance(d, ast.Name) and d.id == "tool"
                   for d in n.decorator_list)]
_CALLED = [n.name for n in _DECORATED if n.name not in _WRAPPED]
ok(not _CALLED, "every @tool is written bare: `@tool()` calls the decorator with "
                "no function and dies at import", _CALLED)

ok(len(_WRAPPED) == len(_DECORATED) == len(TOOLS),
   f"all {len(_WRAPPED)} tools go through the decorator, in the bare form",
   f"{len(_WRAPPED)} bare, {len(_DECORATED)} named tool, {len(TOOLS)} counted")

# `mcp.tool` is reached ONE way, from the AST and not by counting the string —
# a comment saying "the mcp.tool door" satisfied the count, which is the third
# time this file has paid for a textual check. Everywhere except inside the
# decorator, calling it is a tool that converts nothing.
_MCP_TOOL = [n for n in ast.walk(SERVER_TREE) if isinstance(n, ast.Call)
             and ast.unparse(n.func) == "mcp.tool"]
ok(len(_MCP_TOOL) == 1, "`mcp.tool` is called exactly once, inside the decorator",
   len(_MCP_TOOL))

# add_tool() is the other door into the surface, and it carries no decorator at
# all: a tool entering that way would convert nothing AND go missing from no
# manual, because there is nothing for either check to recognise. From the AST:
# the textual version refused to let this repo write the word in a comment,
# which in a project that explains itself in comments is a check that will be
# deleted rather than obeyed.
ok(not [n for n in ast.walk(SERVER_TREE) if isinstance(n, ast.Call)
        and ast.unparse(n.func).endswith("add_tool")],
   "tools enter through the decorator and nowhere else")

# The other way of falling out of the surface, and the counts above cannot see
# it: a tool that loses its decorator ALTOGETHER stops being a tool, so all
# three counts drop together and stay in step. Nothing at runtime complains
# either — the tool simply is not there any more, and the chat that needed it
# gets "no such tool" weeks later. The twin catches this with the signature
# block in its manual; this project has no such block yet (see the two-manuals
# decision), so the manual's PROSE is the witness: every rules_* it names must
# still be a tool. Injected and confirmed: removing @tool from rules_search
# left the whole suite green before this existed.
_NAMED_IN_GUIDE = set(re.findall(r"\brules_[a-z_]+\b", GUIDE_SRC))
_VANISHED = sorted(n for n in _NAMED_IN_GUIDE if n not in TOOL_NAMES)
ok(not _VANISHED,
   "every tool the manual names is still registered as one", _VANISHED)

# The manual covers 26 of the 30, and it is PROSE — rewrite the sentence and
# the witness is gone. So the real witness is the engine: a function in
# server.py that reaches `registry.<something>` is a tool by definition, and if
# it is not one any more it has fallen off the surface while still looking like
# a tool. This covers all thirty and cannot be talked out of it.
_TOUCHES_REGISTRY = []
for _fn in ast.walk(SERVER_TREE):
    if not isinstance(_fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    if is_tool(_fn) or _fn.name in ("tool", "guarded", "env", "_admin"):
        continue
    if any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
           and isinstance(c.func.value, ast.Name) and c.func.value.id == "registry"
           for c in ast.walk(_fn)):
        _TOUCHES_REGISTRY.append(_fn.name)
ok(not _TOUCHES_REGISTRY,
   "nothing reaches the registry except a registered tool — a tool that lost "
   "its decorator still looks like one from in here", _TOUCHES_REGISTRY)

# `guarded` is synchronous and RETURNS what fn returns. Handed a coroutine
# function it would hand back the coroutine unawaited, FastMCP would await it
# further out, the tool would work — and the RulesError would surface with the
# try/except never entered. The conversion would silently not happen. Today
# every tool is sync; the day one is not, this says so instead of blessing it.
_ASYNC_TOOLS = [n.name for n in ast.walk(SERVER_TREE)
                if isinstance(n, ast.AsyncFunctionDef)
                and any(_names_tool(d) for d in n.decorator_list)]
ok(not _ASYNC_TOOLS,
   "no tool is async: `guarded` would return the coroutine without ever seeing "
   "its refusals", _ASYNC_TOOLS)

for tool in TOOLS:
    reached = {n.func.attr for n in ast.walk(tool)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and isinstance(n.func.value, ast.Name) and n.func.value.id == "registry"}
    writes = reached & MUTATING
    if not writes:
        continue
    gated = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_admin"
                for n in ast.walk(tool))
    if tool.name in UNGATED_ON_PURPOSE:
        ok(not gated,
           f"{tool.name} is ungated ON PURPOSE — {UNGATED_ON_PURPOSE[tool.name][:60]}...",
           "it now calls _admin: if that is the new decision, drop the exception")
        continue
    ok(gated, f"{tool.name} calls _admin before {', '.join(sorted(writes))}")

ok(set(UNGATED_ON_PURPOSE) <= TOOL_NAMES,
   "every documented exception names a tool that exists",
   sorted(set(UNGATED_ON_PURPOSE) - TOOL_NAMES))

# =====================================================================
# 2b · the number is not on the surface
# =====================================================================

print("\n== the counter is a structural guarantee, so it is checked statically ==")

# The whole point of the counter is that whoever files a rule CANNOT pick the
# number. That guarantee lives in one place — the absence of a parameter — and
# an absence is exactly the kind of thing that comes back by accident.
_PROPOSE = next((t for t in TOOLS if t.name == "rules_propose"), None)
ok(_PROPOSE is not None, "rules_propose is exposed")
if _PROPOSE is not None:
    params = [a.arg for a in _PROPOSE.args.posonlyargs + _PROPOSE.args.args]
    ok("id" not in params,
       "rules_propose takes NO id: the number is not a choice, it is a position",
       f"parameters: {params}")
    ok("domain" in params, "rules_propose takes the domain instead", f"parameters: {params}")
    ok("legacy_id" in params,
       "rules_propose takes legacy_id: the mapping is built while the work happens")

_ENGINE_PROPOSE = METHODS.get("propose")
if _ENGINE_PROPOSE is not None:
    epar = [a.arg for a in _ENGINE_PROPOSE.args.posonlyargs + _ENGINE_PROPOSE.args.args]
    ok("rid" not in epar and "domain" in epar,
       "the engine's propose() agrees: domain in, no ID", f"parameters: {epar}")

# There is exactly one place that hands out an ID, and it reads the database.
ok("_next_seq" in METHODS, "the counter has a name, and it is a method of the engine")
# And it reads it under a WRITE lock. A deferred BEGIN would upgrade from read
# to write halfway through, which in WAL cannot wait: two connections asking the
# counter at once and one dies with a raw "database is locked". Checked here
# because the suite that proves it needs four connections to catch it.
ok("BEGIN IMMEDIATE" in RULES_SRC,
   "the counter is read inside an IMMEDIATE transaction, not a deferred one")
# The unique index under legacy_id is a CONSTRAINT, so the preflight has to see
# it. A guarantee nothing checks is a guarantee that is not there.
ok("INDEXES" in RULES_SRC and "INDEXES" in PREFLIGHT_SRC,
   "the preflight verifies the unique indexes the engine declares")
ok("numbering_gaps" not in RULES_SRC,
   "numbering_gaps is GONE from the engine — with the counter a gap cannot happen")
ok("numbering_gaps" not in SERVER_SRC,
   "and gone from the docstrings too: a report that no longer exists must not be promised")

# The manual travels inside the image, so it can be checked against the code
# that ships with it — which is the whole reason it lives there and not in the
# vault. A manual that promises a parameter the tool has not got is the defect
# this project already paid for once.
ok("(VA-0002)" in GUIDE_SRC, "the manual teaches the citation format")
ok("already approved" in GUIDE_SRC,
   "the manual says a citation may only point at a rule already approved")
# The migration was DELETED on purpose: a migration is not code, it is the work,
# and a regex sweep over prose invents citations that were never citations. The
# engine owes the seeding pass one column and nothing else.
ok("_widen_ids" not in RULES_SRC and "_widen_bodies" not in RULES_SRC,
   "no ID or body conversion survives in the engine")
ok("no `id` parameter" in GUIDE_SRC,
   "the manual says the number is not a parameter")
ok("legacy_id" in GUIDE_SRC, "the manual documents legacy_id")

# =====================================================================
# 3 · no docstring points at a tool that is not there
# =====================================================================

print("\n== the docstrings point at things that exist ==")

MENTION = re.compile(r"\b(rules_[a-z_]+|reference_guide)\b")
# Names that read like tools but are not, and are allowed to appear in prose.
NOT_TOOLS = {"rules_mcp"}

for node in ast.walk(SERVER_TREE):
    if not isinstance(node, (ast.FunctionDef, ast.Module, ast.ClassDef)):
        continue
    doc = ast.get_docstring(node) or ""
    if not doc:
        continue
    where = getattr(node, "name", "module")
    bad = {m for m in MENTION.findall(doc) if m not in TOOL_NAMES and m not in NOT_TOOLS}
    ok(not bad, f"{where}: every tool it names exists", ", ".join(sorted(bad)))

print("\n== the docstrings earn their place in the context ==")
for tool in TOOLS:
    doc = ast.get_docstring(tool) or ""
    ok(len(doc.strip()) >= 40, f"{tool.name} has a docstring worth reading", f"{len(doc)} chars")

print("\n== the Gate is wired to the hook it says it is ==")

# The Gate is wired by NAMING a hook, and that is the whole danger: the
# Middleware base class ships a pass-through default for every hook it knows,
# so `on_requst` — one letter short — is not an error. It is a method nobody
# ever calls, and the gate is OFF. Nothing fails, nothing logs, and the server
# answers a stranger. No runtime test would notice, because the suites never
# build a FastMCP server — which is exactly the property that lets them run
# without network, Docker or OAuth, and it is not one to give up. So this reads
# the source.
#
# Two different things are pinned here. HOOK pins the DECISION: `on_request`,
# chosen in v1.2 over the narrower `on_call_tool` (which left the handshake
# open, so a stranger with their own GitHub account could enumerate the tools)
# and over the wider `on_message` (which also covers notifications, where
# raising has no channel to answer on). The method set pins the WIRING, and it
# is the one that catches the typo. Changing the decision means changing this
# test too, deliberately — which is the point.
GATE = next((n for n in SERVER_TREE.body
             if isinstance(n, ast.ClassDef) and n.name == "Gate"), None)
ok(GATE is not None, "server.py defines the Gate middleware")

if GATE is not None:
    # Without the base class there is no __call__ and no dispatch, so no hook
    # is ever invoked whatever it is called. The failure is loud rather than
    # silent, but it is one line of AST away from being caught here.
    ok(any(ast.unparse(b) == "Middleware" for b in GATE.bases),
       "the Gate subclasses Middleware, which is what makes a hook a hook",
       [ast.unparse(b) for b in GATE.bases])

    # ALL the assignments, not the first one. A second `HOOK = ...` underneath
    # is what wins at runtime, and reading only the first would report the one
    # that does not.
    _assigned = [s.value.value for s in GATE.body
                 if isinstance(s, ast.Assign)
                 and any(getattr(t, "id", "") == "HOOK" for t in s.targets)
                 and isinstance(s.value, ast.Constant)]
    _declared = _assigned[-1] if _assigned else None
    ok(_assigned == ["on_request"],
       "Gate.HOOK pins the decision: on_request, assigned exactly once", _assigned)

    _hooks = {n.name for n in GATE.body
              if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
              and n.name.startswith("on_")}
    ok(_hooks == {_declared}, "the Gate hooks exactly what HOOK names", sorted(_hooks))

    # The wiring can be perfect and the gate still open: one `return await
    # call_next(ctx)` moved to the top of the hook lets everything through and
    # leaves the rest of the body unreachable. That is precisely the shape of
    # an edit made while chasing something else. So: call_next appears once,
    # and it is the LAST statement of the hook.
    _hook_fn = next((n for n in GATE.body
                     if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                     and n.name == _declared), None)
    if _hook_fn is None:
        ok(False, "the hook the Gate names is a method of the Gate", _declared)
    else:
        _passes = [n for n in ast.walk(_hook_fn) if isinstance(n, ast.Call)
                   and ast.unparse(n.func) == "call_next"]
        ok(len(_passes) == 1, "the hook lets a request through in ONE place",
           len(_passes))
        _last = _hook_fn.body[-1]
        ok(isinstance(_last, ast.Return) and any(
            isinstance(n, ast.Call) and ast.unparse(n.func) == "call_next"
            for n in ast.walk(_last)),
           "and it is the last thing it does — an early return is an open gate",
           ast.unparse(_last)[:60])

    # Read from the AST, not by searching the text. A substring search is
    # satisfied by `#mcp.add_middleware(Gate())` — the check would go on passing
    # over a gate that somebody had commented out while chasing something else,
    # which is the single most likely way for this line to disappear. The AST
    # does not see comments at all. (Found by injecting exactly that defect: the
    # first version of this check, copied from the twin, stayed green.)
    ok(any(isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
           and ast.unparse(n.value.func) == "mcp.add_middleware"
           and any(isinstance(a, ast.Call) and ast.unparse(a.func) == "Gate"
                   for a in n.value.args)
           for n in SERVER_TREE.body),
       "the Gate is actually registered, at module level")

    # A refused stranger and a broken deployment look identical at the client:
    # "the connector will not connect". The log line is the only thing that
    # tells them apart, so it is part of the contract, not of the comfort — and
    # the method is what says which message was turned away.
    #
    # From the AST, for the third time in this section and for the same reason.
    # Counting "log.warning" in the source text is satisfied by a comment
    # saying `# TODO restore the log.warning lines`: both calls can be deleted
    # and the check stays green. That was demonstrated, not imagined.
    _warns = [n for n in ast.walk(GATE) if isinstance(n, ast.Call)
              and ast.unparse(n.func) == "log.warning"]
    ok(len(_warns) >= 2, "both refusals are logged, identity and origin", len(_warns))
    _named = [w for w in _warns
              if any(ast.unparse(a) == "ctx.method" for a in w.args)]
    ok(len(_named) == len(_warns) and _named,
       "and each refusal names the method it turned away",
       f"{len(_named)} of {len(_warns)}")

print("\n== the Dockerfile carries the cures that live in the environment ==")

# These four settings are read when fastmcp is IMPORTED, so they cannot live in
# server.py: anything set after the import arrives too late. That makes them a
# cure with no home in the code, and a cure with no home is one that goes
# missing quietly. This is its home.
DOCKERFILE = open(os.path.join(HERE, "Dockerfile"), encoding="utf-8").read()

for var, value, why in [
    ("FASTMCP_SHOW_SERVER_BANNER", "false", "the banner"),
    ("FASTMCP_ENABLE_RICH_LOGGING", "false", "the boxed log lines"),
    ("FASTMCP_CHECK_FOR_UPDATES", "off", "an OUTBOUND call at every boot"),
    ("FASTMCP_LOG_LEVEL", "WARNING", "fastmcp's own logger"),
    ("PYTHONUNBUFFERED", "1", "log lines arriving when they happen"),
    ("FASTMCP_HOME", "/data/fastmcp", "tokens on a persistent volume"),
]:
    ok(re.search(rf"^ENV {var}={re.escape(value)}\s*$", DOCKERFILE, re.MULTILINE) is not None,
       f"Dockerfile: ENV {var}={value} — {why}")

DOCKER_COPIES = [l for l in DOCKERFILE.splitlines() if l.startswith("COPY ")]
ok(not any("*" in l for l in DOCKER_COPIES),
   "Dockerfile: no wildcard COPY — the test files do not belong in the image",
   [l for l in DOCKER_COPIES if "*" in l])
for f in ("rules.py", "server.py", "preflight.py", "entrypoint.sh", "reference-guide.md"):
    ok(any(re.search(rf"\b{re.escape(f)}\b", l) for l in DOCKER_COPIES),
       f"Dockerfile: {f} is copied in")
ok(not any("test_" in l for l in DOCKER_COPIES), "Dockerfile: no test file is copied in")

# What starts the container, and it is checked in two files at once because it
# only works if the two agree. The Dockerfile has a CMD and no ENTRYPOINT; the
# template's Post Arguments field is EMPTY, because Unraid appends that field
# after the image name — as the command — and it would replace the CMD with a
# bare `entrypoint.sh` that PATH cannot resolve, since /app is WORKDIR and not
# PATH. The template carried `entrypoint.sh` there until v1.2. The one road
# that would have met it is a fresh install from the template, which is the
# least travelled road there is, and the one you take on the day you are
# rebuilding after losing something.
ok(re.search(r"^CMD \[", DOCKERFILE, re.MULTILINE) is not None,
   "Dockerfile: a CMD is what starts the container")
ok(re.search(r"^ENTRYPOINT", DOCKERFILE, re.MULTILINE) is None,
   "Dockerfile: and no ENTRYPOINT, so the CMD is the whole command line")

# reference_guide is a tool that reads a file. Without the file it would answer
# with an error, and the failure would surface in a chat rather than here.
ok(os.path.exists(os.path.join(HERE, "reference-guide.md")),
   "the file reference_guide serves actually exists")

print("\n== a designed refusal does not look like a fault in the log ==")

# Without this, every wrong project code prints a thirty-line traceback at ERROR,
# shaped exactly like a real bug. After a week of those nobody reads them, and
# the next genuine fault arrives disguised as routine.
#
# It has to be the DECORATOR and not a middleware: call_tool applies middleware
# outside and logs inside, so a middleware sees the exception after
# logger.exception has already run. That cost an hour, and it is the kind of
# thing that gets undone by somebody tidying up — hence this check.
# EXACTLY one `def tool`. A second one further down wins for every tool defined
# after it — and since all thirty are defined after the Gate, a three-line
# `def tool(fn): return fn` left there while debugging empties the entire MCP
# surface with the suite at 261 passed, 0 failed. Demonstrated.
_converters = [n for n in ast.walk(SERVER_TREE)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "tool"]
ok(len(_converters) == 1, "server.py defines its own `tool` decorator, once",
   len(_converters))
_converter = _converters[0] if len(_converters) == 1 else None

# And the name RulesFault must mean in the engine what this file assumes it
# means. `RulesFault = RulesError` in rules.py — one line, plausible as a
# simplification — turns the fault branch into the FIRST branch for every
# refusal: no log line, no ToolError, every wrong project code back to thirty
# lines of traceback, and the whole of v1.3 undone with every suite green.
# Also demonstrated. So: it exists, it is a class, it subclasses RulesError,
# and the engine actually raises it somewhere.
_ENGINE_FAULT = [n for n in ENGINE_TREE.body
                 if isinstance(n, ast.ClassDef) and n.name == "RulesFault"]
ok(len(_ENGINE_FAULT) == 1, "rules.py defines RulesFault as a class",
   len(_ENGINE_FAULT))
ok(_ENGINE_FAULT and [ast.unparse(b) for b in _ENGINE_FAULT[0].bases] == ["RulesError"],
   "and it SUBCLASSES RulesError, so everything that already catches RulesError "
   "keeps catching it and the text still reaches the caller",
   [ast.unparse(b) for b in _ENGINE_FAULT[0].bases] if _ENGINE_FAULT else "absent")
_FAULT_RAISES = [n for n in ast.walk(ENGINE_TREE) if isinstance(n, ast.Raise)
                 and isinstance(n.exc, ast.Call)
                 and getattr(n.exc.func, "id", "") == "RulesFault"]
ok(_FAULT_RAISES, "and the engine raises it: a branch nothing can enter is a "
                  "branch whose order the tests below certify for nothing",
   len(_FAULT_RAISES))

# Every name server.py imports from the engine must exist there. Removing
# RulesFault from rules.py while server.py still imports it kills the container
# at boot, and nothing in this suite noticed: the seam check above only follows
# `registry.<method>(...)`.
_ENGINE_NAMES = set()
for _n in ENGINE_TREE.body:
    if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        _ENGINE_NAMES.add(_n.name)
    elif isinstance(_n, ast.Assign):
        _ENGINE_NAMES |= {t.id for t in _n.targets if isinstance(t, ast.Name)}
_IMPORTED = [a.name for n in SERVER_TREE.body if isinstance(n, ast.ImportFrom)
             and n.module == "rules" for a in n.names]
_MISSING = [n for n in _IMPORTED if n not in _ENGINE_NAMES]
ok(not _MISSING, "every name server.py imports from rules.py exists there — "
                 "otherwise the container dies at import", _MISSING)

if _converter is not None:
    # THE WRAPPER, not the decorator. Everything below used to be gathered with
    # ast.walk over the whole of `def tool`, which asks only that the pieces be
    # WRITTEN somewhere inside it. Three mutations exploited that and stayed
    # green: the try/except moved into a `_convert()` nobody calls, with
    # `guarded` reduced to `return fn(...)`; a `_rethrow` helper carrying a
    # decoy `except RulesFault` above a `guarded` whose two branches were
    # swapped; a dead handler holding the INFO level while the live ToolError
    # went to ERROR. So: find the function that is actually returned, and read
    # only that.
    _WRAPPER = "guarded"
    _guarded = next((n for n in ast.walk(_converter)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name == _WRAPPER), None)
    ok(_guarded is not None, f"the decorator defines its wrapper, `{_WRAPPER}`")

    # ...and that the name has not been quietly pointed at something else.
    # `guarded = fn` one line before the return is the same defect as
    # `mcp.tool(fn)`, wearing the name the check looks for.
    _REBOUND = [n for n in ast.walk(_converter) if isinstance(n, ast.Assign)
                and any(getattr(t, "id", "") == _WRAPPER for t in n.targets)]
    ok(not _REBOUND, f"`{_WRAPPER}` is only ever the def, never reassigned",
       [ast.unparse(n) for n in _REBOUND])

if _converter is not None and _guarded is not None:
    handlers = [h for t in ast.walk(_guarded) if isinstance(t, ast.Try)
                for h in t.handlers]
    caught = [ast.unparse(h.type) for h in handlers if h.type is not None]
    ok("RulesError" in caught, "the wrapper catches RulesError", caught)

    # The ORDER, which is the whole distinction and is invisible once written:
    # RulesFault SUBCLASSES RulesError, so `except RulesError` placed first
    # would swallow every fault into the quiet path and nothing would look
    # wrong. Python has no warning for this; this is the warning.
    ok(caught[:1] == ["RulesFault"],
       "and it catches RulesFault FIRST, or the subclass never gets its turn",
       caught)
    _fault_h = [h for h in handlers
                if h.type is not None and ast.unparse(h.type) == "RulesFault"]
    ok(_fault_h and all(isinstance(s, ast.Raise) and s.exc is None
                        for h in _fault_h for s in h.body),
       "and it lets a fault rise untouched: traceback at ERROR, as before")

    # The refusal must leave a line of OUR own. FastMCP's never reaches the
    # container's log: the Dockerfile pins FASTMCP_LOG_LEVEL=WARNING, so an
    # INFO record from fastmcp.server.server is dropped before it is printed.
    # Without this line the conversion trades thirty lines for none — which is
    # what this service did in v1.2 and earlier, measured on the twin.
    _logged = [n for h in handlers for n in ast.walk(h)
               if isinstance(n, ast.Call) and ast.unparse(n.func).startswith("log.")]
    ok(_logged, "the refusal leaves a line of our own, or the conversion trades "
                "thirty lines for none")
    ok(all(ast.unparse(n.func) == "log.info" for n in _logged),
       "at INFO: WARNING is the Gate's height, and a wrong project code is not "
       "a warning", sorted({ast.unparse(n.func) for n in _logged}))
    # WHAT it says. `log.info("refused")` satisfies everything above and is
    # useless: the line exists to name the tool and the reason.
    ok(all(any(ast.unparse(a) == "fn.__name__" for a in n.args) for n in _logged)
       and all(any(ast.unparse(a) == "e" for a in n.args) for n in _logged),
       "and it names the tool and carries the reason",
       [ast.unparse(n)[:60] for n in _logged])
    raised = [n for h in handlers for n in ast.walk(h)
              if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "ToolError"]
    ok(raised, "and re-raises ToolError")
    # Pinned to INFO, not merely "below ERROR". `"ERROR" not in level` is a
    # substring search: CRITICAL satisfies it and is HIGHER, and so does
    # WARNING — the one value there is a paragraph explaining we must not use,
    # because WARNING is the Gate's height and a refused project code must not
    # sit where a refused stranger sits. A test that asserts something looser
    # than its own docstring promises is worse than no test.
    # EVERY ToolError in the wrapper, not the first one found: reading only
    # levels[0] let a dead handler hold the INFO while the live one went to
    # ERROR.
    levels = [ast.unparse(k.value) for r in raised for k in r.keywords
              if k.arg == "log_level"]
    ok(levels and set(levels) == {"logging.INFO"},
       "at logging.INFO exactly, which is the decision, and nowhere else",
       levels or "log_level not set")
    # `raise X from None` parses as cause=Constant(None) — not as no cause at
    # all, which is what a bare `raise X` gives you.
    ok(any(isinstance(n, ast.Raise) and isinstance(n.cause, ast.Constant)
           and n.cause.value is None for n in ast.walk(_guarded)),
       "with `from None`: the chained traceback is what we are removing")
    # functools.wraps is what keeps the MCP schema intact: FastMCP reads the
    # name, docstring and signature, and follows __wrapped__ to find them.
    ok(any(ast.unparse(d) == "functools.wraps(fn)" for d in _guarded.decorator_list),
       "and functools.wraps ON THE WRAPPER, or every tool loses its schema",
       [ast.unparse(d) for d in _guarded.decorator_list])
    # WHAT it registers, not merely that it registers something. Changing the
    # last line to `return mcp.tool(fn)` defines `guarded` and throws it away:
    # no conversion, no log line, tracebacks back — and every check above still
    # passes, because every piece they look for is still written down. That is
    # the exact shape of a check that filters out the case it was written for.
    _registers = [n for n in ast.walk(_converter) if isinstance(n, ast.Call)
                  and ast.unparse(n.func) == "mcp.tool"]
    ok(_registers and _registers[0].args
       and ast.unparse(_registers[0].args[0]) == "guarded",
       "and it registers the WRAPPED function, not the bare one",
       ast.unparse(_registers[0].args[0]) if _registers and _registers[0].args
       else "no argument")

# Every tool must go through it. One @mcp.tool left behind is one tool whose
# refusals still print a traceback — and it would be the one you never test.
BARE = [n.name for n in SERVER_TREE.body if isinstance(n, ast.FunctionDef)
        and any(ast.unparse(d) == "mcp.tool" for d in n.decorator_list)]
ok(not BARE, "no tool is registered with a bare @mcp.tool", BARE)

# The engine must not IMPORT FastMCP: that is what lets the suites run without
# a server, and it is why the conversion lives in server.py. Naming it in a
# comment is fine — explaining why the lock exists requires naming it.
ENGINE_IMPORTS = set()
for n in ast.walk(ENGINE_TREE):
    if isinstance(n, ast.Import):
        ENGINE_IMPORTS |= {a.name.split(".")[0] for a in n.names}
    elif isinstance(n, ast.ImportFrom) and n.module:
        ENGINE_IMPORTS.add(n.module.split(".")[0])
ok("fastmcp" not in ENGINE_IMPORTS and "mcp" not in ENGINE_IMPORTS,
   f"rules.py imports no server framework: {sorted(ENGINE_IMPORTS)}")

print("\n== the signer exists, works, and stays OUT of the image ==")

# rules_batch tells the caller to run `python3 sign.py <digest>`. For a while it
# said that about a file nobody had written — same class of defect as the
# reference guide, and it would have surfaced at the first approval.
SIGNER = os.path.join(HERE, "sign.py")
ok(os.path.exists(SIGNER), "sign.py exists — the batch note points at something real")
ok(not any("sign.py" in l for l in DOCKER_COPIES),
   "sign.py is NOT in the image: it belongs on Alfredo's machine, with the key")

import subprocess                                               # noqa: E402
import tempfile                                                 # noqa: E402

def signer(args, key):
    env = dict(os.environ, CODIFIER_KEY=key)
    return subprocess.run([sys.executable, SIGNER] + args, capture_output=True,
                          text=True, env=env)

_d = tempfile.mkdtemp(prefix="signer-")
_key = os.path.join(_d, "approval.key")
_gen = signer(["--keygen"], _key)
ok(_gen.returncode == 0 and os.path.exists(_key), "--keygen writes a key", _gen.stderr[:120])
ok(oct(os.stat(_key).st_mode)[-3:] == "600", "the private key is born 0600")
ok(signer(["--keygen"], _key).returncode == 2,
   "generating over an existing key is refused — it would orphan every approval")

_pub = signer(["--pubkey"], _key).stdout.strip()
_digest = hashlib.sha256(b"a batch").hexdigest()
_sig = signer([_digest], _key).stdout.strip()

# The round trip is the point: a signer whose output the engine rejects is worse
# than no signer, because the error would send you looking at the key.
try:
    from rules import verify_signature                          # noqa: E402
    verify_signature(_pub, _digest, _sig)
    ok(True, "a signature from sign.py verifies against the engine")
except Exception as e:
    ok(False, "a signature from sign.py verifies against the engine", str(e)[:120])

try:
    verify_signature(_pub, _digest.replace("a", "b", 1), _sig)
    ok(False, "and it does NOT verify against another digest", "it was accepted")
except Exception:
    ok(True, "and it does NOT verify against another digest")

ok(signer(["not-a-digest"], _key).returncode == 2,
   "something that is not a digest is refused before it is signed")

# What happens on a machine WITHOUT cryptography — which is every fresh macOS,
# since the system Python refuses a plain pip install. Simulated by shadowing
# the module with one that raises on import.
_shadow = os.path.join(_d, "shadow")
os.makedirs(_shadow, exist_ok=True)
with open(os.path.join(_shadow, "cryptography.py"), "w") as f:
    f.write("raise ImportError('not installed')\n")

_env = dict(os.environ, CODIFIER_KEY=_key, PYTHONPATH=_shadow,
            HOME=os.path.join(_d, "nohome"))
try:
    _r = subprocess.run([sys.executable, SIGNER, "--pubkey"], capture_output=True,
                        text=True, env=_env, timeout=20)
    ok(_r.returncode == 2 and "venv" in _r.stderr,
       "without cryptography it says how to fix it, in two commands", _r.stderr[:100])
except subprocess.TimeoutExpired:
    ok(False, "without cryptography it says how to fix it, in two commands",
       "it hung — the re-exec is looping")

print("\n== the version is written in one place and copied nowhere ==")

# VERSION lives in rules.py and the CI compares it with the tag. The badges in
# the two READMEs are hand copies of it, and nothing tied them to anything:
# setting VERSION to 9.9.9 left the whole suite green. The number that appears
# in the startup line is now what the Log Level field points at as the proof
# that an update took, so a README claiming a different one is a second answer
# to a question that must have one.
from rules import VERSION as _V                                 # noqa: E402

for _readme, _label in (("README.md", "version"), ("README.it.md", "versione")):
    _txt = source(os.path.join(HERE, _readme))
    _badges = re.findall(rf"badge/{_label}-([0-9]+\.[0-9]+\.[0-9]+)-", _txt)
    ok(_badges == [_V], f"{_readme}: the version badge says {_V}", _badges)

_STRAY = [f for f in ("server.py", "preflight.py", "sign.py", "Dockerfile",
                      "entrypoint.sh", "codifier-mcp.xml")
          if re.search(r"^VERSION\s*=", source(os.path.join(HERE, f)), re.MULTILINE)]
ok(not _STRAY, "no second file declares a VERSION of its own", _STRAY)

print("\n== the template is publishable ==")

TEMPLATE_PATH = os.path.join(HERE, "codifier-mcp.xml")
ok(os.path.exists(TEMPLATE_PATH), "the template is named after the repository")
TEMPLATE = open(TEMPLATE_PATH, encoding="utf-8").read()

# The template published in the repository IS the configuration, so it must
# carry no trace of the machine it was written on.
for residue in ("marlin-kelvin", "svc-a2", "/mnt/cache/Claude", "160.79.104.0/21 #  "):
    ok(residue not in TEMPLATE, f"no personal residue: {residue!r}")

ok("<Repository>ghcr.io/" in TEMPLATE,
   "the template points at ghcr, not at a local image")
ok("<Icon>" in TEMPLATE and os.path.exists(os.path.join(HERE, "codifier-icon.png")),
   "the icon the template links to is IN the repository — a raw URL that 404s "
   "is the twin's oldest open item")
ok("<WebUI/>" in TEMPLATE,
   "no WebUI: the service listens on 127.0.0.1 and has no web interface")

# The empty ELEMENT, not the deleted element: <PostArgs/> is what Unraid writes
# by itself for a field nobody filled in, every container in service has it,
# and keeping the same shape stops the published template and one passed through
# the interface from drifting apart. See the Dockerfile section for why the
# field must stay empty.
ok("<PostArgs/>" in TEMPLATE,
   "Post Arguments is empty, and present: the CMD is enough on its own")

# Unraid does not propagate new variables to containers already installed, so a
# variable introduced later means editing every existing install by hand. These
# go in now, inert or not.
for var in ("APPROVAL_PUBKEY", "APPROVAL_GRACE_UNTIL", "PROVISIONAL_DAYS", "WEB_PORT",
            "LOG_LEVEL", "ALLOWED_CIDRS", "DB_PATH", "BACKUP_DIR", "ADMIN_ACCESS_CODE"):
    ok(f'Target="{var}"' in TEMPLATE, f"template declares {var}")

# Every variable the service reads through env() without a default is one the
# service cannot start without.
for var in ("BASE_URL", "ALLOWED_GITHUB_LOGIN", "ADMIN_ACCESS_CODE",
            "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "JWT_SIGNING_KEY"):
    ok(f'Target="{var}"' in TEMPLATE, f"template declares the mandatory {var}")

ok('Target="VAULT_UID"' not in TEMPLATE and 'Target="VAULT_GID"' not in TEMPLATE,
   "no VAULT_UID/VAULT_GID: those belong to the twin, which drops privileges")

import xml.etree.ElementTree as ET                              # noqa: E402
try:
    root = ET.parse(TEMPLATE_PATH).getroot()
    ok(root.tag == "Container", f"the template parses as XML ({len(root.findall('Config'))} fields)")
except ET.ParseError as e:
    ok(False, "the template parses as XML", str(e))

print("\n== the preflight helpers ==")

# preflight imports rules only inside a check, so this costs nothing.
import preflight as pf                                          # noqa: E402

for value in ("CHANGEME", "CHANGE_ME", "change-me", "change me", "CAMBIAMI",
              "https://CHANGEME.your-tailnet.ts.net"):
    ok(pf.is_placeholder(value), f"placeholder caught: {value!r}")

# The other half, and the one that actually matters: a check that refuses to
# start the service on a real value is worse than the hole it closes.
for value in ("https://exchange.me.ts.net", "exchange mechanism", "svc-a2",
              "a3f9c2e1b8d7", "interchange.me"):
    ok(not pf.is_placeholder(value), f"real value let through: {value!r}")

ok(pf.parse_cidrs("") == [], "an empty list means NO filter")
ok(pf.parse_cidrs("160.79.104.0/21 # egress ; 100.64.0.0/10 # tailnet") ==
   [("160.79.104.0/21", "egress"), ("100.64.0.0/10", "tailnet")],
   "';' separates and '#' opens a description")
ok(pf.parse_cidrs("160.79.104.0/21 # egress, and nothing else ;") ==
   [("160.79.104.0/21", "egress, and nothing else")],
   "a description may contain a comma, and a trailing ';' changes nothing")

for bad in ("160.79.104.0/21 ; not-a-network", "160.79.104.1/21", "# only a description"):
    try:
        pf.parse_cidrs(bad)
        ok(False, f"a malformed entry BLOCKS: {bad!r}", "it was accepted")
    except ValueError as e:
        ok(True, f"a malformed entry BLOCKS: {bad!r}  ({str(e)[:40]})")

ok(pf.describe_cidrs([]) == "OFF (no IP filter)", "describe_cidrs says when the filter is off")
ok(pf.describe_cidrs(pf.parse_cidrs("10.0.0.0/8 # a ; 10.1.0.0/16 # b")).startswith("2 ranges"),
   "it counts what was UNDERSTOOD — a comma for a semicolon would show up as 1")

os.environ["ALLOWED_CIDRS"] = ""
ok(pf.cidrs_from_env() == [], "ALLOWED_CIDRS defined and EMPTY means the filter is off")
os.environ.pop("ALLOWED_CIDRS")
os.environ["ANTHROPIC_CIDR"] = "10.0.0.0/8"
ok(pf.cidrs_from_env() == [("10.0.0.0/8", "")],
   "the deprecated ANTHROPIC_CIDR is still honoured: a new variable is born optional")
os.environ.pop("ANTHROPIC_CIDR")
ok(pf.cidrs_from_env() == pf.parse_cidrs(pf.DEFAULT_CIDRS),
   "neither defined: the documented egress range")

print("\n== LOG_LEVEL: a closed list, and it says what it rejected ==")

# setLevel() raises on an unknown level, and it runs at IMPORT — after the
# preflight has printed a clean sheet. So the one way to get a container that
# dies in a loop with no useful message was to leave the optional LOG_LEVEL
# field empty, which the dropdown does not prevent and a hand-built container
# has no dropdown for at all.
#
# Both directions. The two real levels survive untouched, and everything else
# falls back to INFO while REPORTING what it rejected. The reporting is the
# half worth testing: a knob that ignores you in silence is how you get told
# the feature is broken.
for value, expect_level, expect_rejected in (
        (None, "INFO", None),          # not defined at all
        ("", "INFO", None),            # defined and empty: the crash case
        ("   ", "INFO", None),         # whitespace only
        ("INFO", "INFO", None),
        ("WARNING", "WARNING", None),
        ("warning", "WARNING", None),  # case is typography, not intent
        (" info ", "INFO", None),
        ("DEBUG", "INFO", "DEBUG"),    # inert here: there are no debug lines
        ("ERROR", "INFO", "ERROR"),    # would silence the gate's refusals
        ("CRITICAL", "INFO", "CRITICAL"),
        ("NOTSET", "INFO", "NOTSET"),  # a real level, and it means "ask my parent"
        ("50", "INFO", "50"),          # setLevel accepts ints, not their strings
        ("WARN", "WARNING", None),     # Python's own alias, not a typo: honoured
        ("warn", "WARNING", None),
        ("INF0", "INFO", "INF0")):
    _old = os.environ.pop("LOG_LEVEL", None)
    try:
        if value is not None:
            os.environ["LOG_LEVEL"] = value
        got = pf.log_level_from_env()
        ok(got == (expect_level, expect_rejected),
           f"LOG_LEVEL={value!r} -> {expect_level}"
           + (f", rejecting {expect_rejected!r}" if expect_rejected else ""), got)
    finally:
        os.environ.pop("LOG_LEVEL", None)
        if _old is not None:
            os.environ["LOG_LEVEL"] = _old

# DEBUG is listed as inert above, and that claim has to stay true: the day
# somebody adds a .debug() line, the closed list silently starts hiding output
# instead of merely not producing any.
for _f in ("server.py", "rules.py", "preflight.py", "sign.py"):
    _src = source(os.path.join(HERE, _f))
    ok(".debug(" not in _src,
       f"{_f} contains no .debug() — which is why DEBUG is inert, not offered")

# The service must go through the helper and not read the variable itself: two
# expressions that agree today are two expressions, and the one that used to be
# here — setLevel(os.environ.get("LOG_LEVEL", "INFO").upper()) — is the crash
# this version removes. From the AST, because a substring search for
# 'os.environ.get("LOG_LEVEL"' is walked around by os.getenv, by single quotes,
# or by a subscript. All three were tried, and all three stayed green.
_READS = [n for n in ast.walk(SERVER_TREE)
          if (isinstance(n, ast.Call)
              and ast.unparse(n.func) in ("os.environ.get", "os.getenv")
              and n.args and isinstance(n.args[0], ast.Constant)
              and n.args[0].value == "LOG_LEVEL")
          or (isinstance(n, ast.Subscript) and ast.unparse(n.value) == "os.environ"
              and isinstance(n.slice, ast.Constant) and n.slice.value == "LOG_LEVEL")]
ok(not _READS, "server.py does not read LOG_LEVEL on its own — it comes from the "
   "helper", [ast.unparse(n) for n in _READS])

# And it must USE what the helper returned. Both halves: the level, and the
# report of what was rejected — the report is the half worth testing, because a
# knob that ignores you in silence is how you get accused of having broken it.
# And `log` must be the service's logger, bound once. Pointing the name at
# `logging.getLogger("codifier-mcp.quiet")` — with a NullHandler and
# propagate=False, which is two more plausible lines — takes the refusal line
# out of the log entirely while every check on it stays green: they all read
# `log.info`, and `log` is whatever the last assignment says.
_LOG_BINDS = [n for n in ast.walk(SERVER_TREE) if isinstance(n, ast.Assign)
              and any(getattr(t, "id", "") == "log" for t in n.targets)]
def _is_our_logger(v) -> bool:
    # Structurally, not by comparing rendered source: ast.unparse normalises
    # quotes, so a string comparison here fails on the correct code.
    return (isinstance(v, ast.Call) and ast.unparse(v.func) == "logging.getLogger"
            and len(v.args) == 1 and isinstance(v.args[0], ast.Constant)
            and v.args[0].value == "codifier-mcp")


ok(len(_LOG_BINDS) == 1 and _is_our_logger(_LOG_BINDS[0].value),
   "`log` is the service's own logger, bound once and never repointed",
   [ast.unparse(n) for n in _LOG_BINDS])
ok(not [n for n in ast.walk(SERVER_TREE)
        if isinstance(n, ast.Attribute) and n.attr == "propagate"],
   "and nothing switches propagation off under it")

_SETS = [n for n in ast.walk(SERVER_TREE) if isinstance(n, ast.Call)
         and ast.unparse(n.func) == "log.setLevel"]
ok(len(_SETS) == 1 and ast.unparse(_SETS[0]) == "log.setLevel(_LEVEL)",
   "the level set on our logger is the one the helper resolved",
   [ast.unparse(n) for n in _SETS])
ok(any(isinstance(n, ast.If) and ast.unparse(n.test) == "_REJECTED"
       and any(isinstance(c, ast.Call) and ast.unparse(c.func) == "log.warning"
               for c in ast.walk(n))
       for n in SERVER_TREE.body),
   "and a rejected value is said out loud, at WARNING so it survives itself")

# The dropdown is the other half of closing the list, and it is the half a
# person actually sees. Unraid renders a pipe-separated Default as a menu.
ok('Target="LOG_LEVEL"' in TEMPLATE and 'Default="INFO|WARNING"' in TEMPLATE,
   "the template offers the closed list as a dropdown, not free text")
for _dead in ("DEBUG, INFO, WARNING, ERROR", "DEBUG, INFO, WARNING and ERROR"):
    ok(_dead not in TEMPLATE,
       f"the template no longer offers the inert value: {_dead!r}")

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
