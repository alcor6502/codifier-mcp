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

# The common engine, located where it is INSTALLED. The checks that used to
# read the local Gate and the local decorator now read the engine's files: the
# pin in requirements.txt decides what those files say, so a stale or doctored
# engine goes red here instead of misbehaving in a chat.
import importlib.util as _ilu                                   # noqa: E402

_ENG_SPEC = _ilu.find_spec("mcp_common_engine")
ENGINE_PKG = (os.path.dirname(_ENG_SPEC.origin)
              if _ENG_SPEC and _ENG_SPEC.origin else None)


def source_or_none(path: str):
    """For the files a check is ABOUT. Reading one of those at import time with
    source() turns a missing file into a traceback on line sixty, and then not
    one of the three hundred checks below runs — including the ones written to
    notice exactly that. A missing file has to be a red line with a name on
    it."""
    try:
        return source(path)
    except OSError:
        return None


GUIDE_SRC = source_or_none(os.path.join(HERE, "reference-guide.md")) or ""

# WHERE server.py keeps the files it serves: the module-level constants, with
# the file each one names, read off the source and not listed by hand. It is
# the mapping and not the list that matters — a list says two manuals ship, the
# mapping says WHICH TOOL SERVES WHICH, and swapping the two constants is a
# one-word edit that puts the maintenance manual behind the open door.
PATH_CONSTS = {}
for _n in SERVER_TREE.body:
    if not isinstance(_n, ast.Assign) or len(_n.targets) != 1:
        continue
    if not isinstance(_n.targets[0], ast.Name):
        continue
    for _c in ast.walk(_n.value):
        if (isinstance(_c, ast.Call) and ast.unparse(_c.func).endswith(".with_name")
                and _c.args and isinstance(_c.args[0], ast.Constant)):
            PATH_CONSTS[_n.targets[0].id] = _c.args[0].value

def sole_binding(name, kinds, why: str):
    """The name means what it looks like: ONE definition, at module level,
    undecorated, and never bound to anything else afterwards.

    Every check in this file that reaches for `Gate`, `tool` or `_admin` finds
    it by NAME, and Python gives the name to whatever was bound last without
    saying so. A second `class Gate(Middleware)` further down, or
    `Gate = _NoGate` on the line above the registration, leaves the real
    definition in place for the AST to find and hands the running server the
    other one. Both were executed: the suite stayed green with the gate off.

    So the three load-bearing names are pinned here, once, in one place —
    rather than three copies that drift — and the node this returns is the one
    the checks below read.
    """
    defs = [n for n in ast.walk(SERVER_TREE) if isinstance(n, kinds) and n.name == name]
    ok(len(defs) == 1, f"server.py defines `{name}` exactly once", len(defs))
    ok(bool(defs) and defs[0] in SERVER_TREE.body,
       f"and `{name}` is at module level, not nested where a flag could skip it")
    ok(bool(defs) and not defs[0].decorator_list,
       f"and `{name}` carries no decorator that could stand in for it",
       [ast.unparse(d) for d in defs[0].decorator_list] if defs else "")
    rebound = []
    for n in ast.walk(SERVER_TREE):
        hit = False
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            hit = any((a.asname or a.name.split(".")[0]) == name for a in n.names)
        elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr, ast.For)):
            hit = any(isinstance(x, ast.Name) and x.id == name
                      and isinstance(x.ctx, ast.Store) for x in ast.walk(n))
        elif isinstance(n, ast.Global):
            hit = name in n.names
        if hit:
            rebound.append(ast.unparse(n)[:50])
    ok(not rebound, f"and the name `{name}` is never bound to anything else — {why}",
       rebound)
    return defs[0] if len(defs) == 1 else None


def sole_import(name: str, module: str):
    """The name is bound exactly once, by ONE module-level `from <module>
    import <name>`, and never bound to anything else afterwards.

    Same danger as sole_binding, for a name that now ARRIVES instead of being
    defined here: Python gives the name to whatever was bound last, in silence.
    `Gate = _NoGate` above the registration, a second import, a def wearing the
    same name — each leaves the import in place for a reader to find and hands
    the running server something else."""
    imports = [n for n in SERVER_TREE.body if isinstance(n, ast.ImportFrom)
               and n.module == module
               and any((a.asname or a.name) == name for a in n.names)]
    ok(len(imports) == 1,
       f"server.py imports `{name}` from {module}, exactly once, at module level",
       len(imports))
    other = []
    for n in ast.walk(SERVER_TREE):
        hit = False
        if isinstance(n, (ast.Import, ast.ImportFrom)) and n not in imports:
            hit = any((a.asname or a.name.split(".")[0]) == name for a in n.names)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            hit = n.name == name
        elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign,
                            ast.NamedExpr, ast.For)):
            hit = any(isinstance(x, ast.Name) and x.id == name
                      and isinstance(x.ctx, ast.Store) for x in ast.walk(n))
        elif isinstance(n, ast.Global):
            hit = name in n.names
        if hit:
            other.append(ast.unparse(n)[:50])
    ok(not other, f"and the name `{name}` is never bound to anything else", other)


SERVED_FILES = sorted(set(PATH_CONSTS.values()))
MANUALS = {f: source_or_none(os.path.join(HERE, f))
           for f in SERVED_FILES if f.endswith(".md")}

# =====================================================================
# The engine: signatures, and which methods write
# =====================================================================

# TWO classes since v4.0.0, and they are not interchangeable: `Registry` is the
# ROUTER — `projects.txt` in, one `Project` out — and `Project` is the engine,
# one folder, one file, one connection. Everything a tool does happens on a
# Project; the Registry only says which one.
ROUTER = next(n for n in ENGINE_TREE.body
              if isinstance(n, ast.ClassDef) and n.name == "Registry")
PROJECT = next(n for n in ENGINE_TREE.body
               if isinstance(n, ast.ClassDef) and n.name == "Project")

METHODS: dict[str, ast.FunctionDef] = {
    n.name: n for n in PROJECT.body if isinstance(n, ast.FunctionDef)}
ROUTES: dict[str, ast.FunctionDef] = {
    n.name: n for n in ROUTER.body if isinstance(n, ast.FunctionDef)}

_WRITES = re.compile(r"\b(INSERT|UPDATE|DELETE)\b", re.IGNORECASE)


def _writes_directly(fn: ast.FunctionDef) -> bool:
    return any(isinstance(n, ast.Constant) and isinstance(n.value, str) and _WRITES.search(n.value)
               for n in ast.walk(fn))


def _calls_on_self(fn: ast.FunctionDef) -> set[str]:
    """Every method this one reaches on self — INCLUDING the ones it reaches by
    name rather than by attribute.

    `amend_project` dispatches with `getattr(self, f"_amend_{entity}")`, and an
    edge the AST cannot follow is an edge that is not there: without this, the
    method that writes every domain, consumer and group in the project came out
    of the derivation READ-ONLY, and the gate check below would have had
    nothing to say about the tool that calls it. The literal head of the
    f-string is enough — every private method that starts with it is a
    candidate, and over-reaching here costs nothing while under-reaching costs
    a hole."""
    out = set()
    for n in ast.walk(fn):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "self"):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "getattr" and len(n.args) >= 2
                    and isinstance(n.args[0], ast.Name) and n.args[0].id == "self"):
                head = ""
                target = n.args[1]
                if isinstance(target, ast.JoinedStr) and target.values \
                        and isinstance(target.values[0], ast.Constant):
                    head = target.values[0].value
                elif isinstance(target, ast.Constant) and isinstance(target.value, str):
                    head = target.value
                if head:
                    out |= {m for m in METHODS if m.startswith(head)}
            continue
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
    # The task log, and the reason is rules_propose's turned inside out: a
    # proposal is ungated because it reaches nobody, a task because it IS the
    # work. Asking a working chat for the architect key to write down what it
    # has just finished would put the maintenance credential in every chat of
    # the project — the one thing the per-project credential model exists to
    # prevent. What stays gated is the CROSS-CONSUMER view, tasks_overview,
    # because that is the maintainer's reading and not a worker's.
    "tasks_add": "opening a task is the work asking for itself: it binds nobody and "
                 "reaches one consumer, and the key would have to live in every chat",
    "tasks_close": "closing your OWN task is the work reporting itself, whichever of "
                   "the two verdicts it carries — and the outcome is what the log is "
                   "read for. Somebody else's takes the admin code, and that half is "
                   "checked as a gate, not as an exception",
    "tasks_amend": "amending an OPEN task, including handing it to the right owner, "
                   "is routine traffic between roles — the closed ones are frozen by "
                   "the database, not by a credential",
}

# The two tools whose gate depends on what is passed rather than on which tool
# it is: with `key` they are administration, without it they are the work.
# Written as data, with the reason, for the same purpose as the set above — an
# exception with no reason next to it stops being a decision.
ADMIN_IF_KEY = {
    "tasks_close": "closing someone else's task takes the admin code, and it is the "
                   "one declared exception to the flat ladder: one factor, because a "
                   "task closed wrong reopens as a new task",
    "tasks_amend": "amending someone else's task, same reason as closing it",
    "reference_guide": "the pair asks for the OTHER HALF of the manual; bare, it is "
                       "the work manual and refusing there would be a wall at the "
                       "front door",
    "project_amend": "`specs` alone travels on the reference code — operational data, "
                     "not identity — and everything else on the pair",
}

print("\n== the engine, as the seam sees it ==")
ok(len(METHODS) > 30, f"Project parsed: {len(METHODS)} methods")
ok(len(ROUTES) >= 5, f"and the router with it: {len(ROUTES)} methods")
ok({"propose", "decide", "retire", "amend_rule", "amend_project",
    "task_add", "task_close", "task_amend"} <= MUTATING,
   f"the mutating set is derived, not typed: {len(MUTATING)} methods",
   sorted(MUTATING))
ok("list_rules" not in MUTATING and "status" not in MUTATING,
   "and it does not sweep the read-only ones in with them")

# =====================================================================
# 1 · every call into the engine exists, with a compatible signature
# =====================================================================

def is_tool(fn: ast.FunctionDef) -> bool:
    """Registered either as @mcp.tool or through the @tool decorator — since
    the adoption that name is bound to the engine's make_tool, and the checks
    below pin both the binding and the fact that every tool uses it."""
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


print("\n== sixteen tools, and the ones that went stayed gone ==")

# The catalogue is paid by every session of the project, so the number is a
# decision and not an outcome. Written as an EQUALITY, both ways: nothing
# missing, and — the half a count cannot see — nothing extra. `>= 16` tolerates
# a seventeenth, and a seventeenth is the realistic mistake.
ALIVE = {
    # the reference code
    "reference_guide", "project_info", "rules_list", "rules_get", "rules_propose",
    "tasks_add", "tasks_list", "tasks_get", "tasks_close", "tasks_amend",
    # the admin code, and a one-time code on top for what already exists
    "project_amend", "rules_amend", "rules_retire", "project_status",
    "rules_export", "tasks_overview",
}
ok(TOOL_NAMES == ALIVE, f"the surface is exactly these {len(ALIVE)} tools",
   f"extra: {sorted(TOOL_NAMES - ALIVE)} · missing: {sorted(ALIVE - TOOL_NAMES)}")

# And the dead, BY NAME. The equality above already refuses them; this list
# makes a resurrection fail saying which name came back, which is the sentence
# somebody reads at 2am. Two generations of them: the seven that left in
# v3.0.0 for the UI, and the sixteen v4.0.0 folded into the tools above.
DEAD = {
    "rules_approve", "rules_renew", "rules_promote", "rules_registry",
    "rules_project_create", "rules_project_rekey", "rules_backup",
    "rules_project_info", "rules_search", "rules_pending", "rules_batch",
    "rules_deny", "rules_fix", "rules_widen", "rules_narrow", "rules_status",
    "rules_check", "rules_history", "rules_diff", "rules_consumers_add",
    "rules_consumer_retire", "rules_domains_add", "rules_scope_create",
    "rules_scope_edit", "tasks_search", "tasks_range", "tasks_complete",
    "tasks_drop", "legislator_guide",
}
ok(not (DEAD & TOOL_NAMES), "and not one of the tools that went is back",
   sorted(DEAD & TOOL_NAMES))

# WHO IS GATED, as an equality, read off the SIGNATURES rather than off a list:
# a tool takes `key` or it does not, and that is the whole of what a caller
# sees. A gate appearing on a working tool fails as loudly as one going missing
# from an administration tool — the first would put the admin code in every
# chat of the project, which is the one thing the credential model exists to
# stop.
WITH_KEY = {"reference_guide", "tasks_close", "tasks_amend", "project_amend",
            "rules_amend", "rules_retire", "project_status", "rules_export",
            "tasks_overview"}
_takes = {t.name for t in TOOLS
          if "key" in {a.arg for a in t.args.posonlyargs + t.args.args + t.args.kwonlyargs}}
ok(_takes == WITH_KEY, "the tools that take an admin code are exactly these",
   f"extra: {sorted(_takes - WITH_KEY)} · missing: {sorted(WITH_KEY - _takes)}")

# And the SECOND factor, which is narrower still: only what MODIFIES something
# that already exists. Three tools, and the ladder in `port_for` is what says
# so — this is the surface agreeing with it.
WITH_AUTH = {"project_amend", "rules_amend", "rules_retire"}
_second = {t.name for t in TOOLS
           if "auth_code" in {a.arg for a in t.args.posonlyargs + t.args.args
                              + t.args.kwonlyargs}}
ok(_second == WITH_AUTH, "and the one-time code is asked by exactly these three",
   f"extra: {sorted(_second - WITH_AUTH)} · missing: {sorted(WITH_AUTH - _second)}")
ok(_second < _takes, "each of which is behind the admin code as well: the "
   "one-time code elevates nobody on its own",
   sorted(_second - _takes))

# The UI's password has NO tool, and that is the shape of "what is catastrophic
# has no tool". Read off the signatures, so a parameter carrying it under any
# name that says what it is fails here.
_UI_WORDS = {"master", "web_ui_password", "ui_password", "password", "master_code"}
_carriers = sorted(f"{t.name}({a})" for t in TOOLS
                   for a in {p.arg for p in t.args.posonlyargs + t.args.args
                             + t.args.kwonlyargs} if a in _UI_WORDS)
ok(not _carriers, "no tool takes the administration UI's password", _carriers)

# THE LADDER IS ASKED, NEVER REPEATED. `port_for` is the one place it is
# written, so the surface may consult it and may not decide anything itself: a
# scale spelled out at each door is a scale with one door out of step, and the
# door that is out of step is the one nobody looks at. Both halves pinned —
# the surface calls it, and it does not carry a rule of its own about which
# entity or action needs what.
_ASKS = [n for n in ast.walk(SERVER_TREE) if isinstance(n, ast.Call)
         and ast.unparse(n.func) == "Project.port_for"]
ok(_ASKS, "server.py asks the engine which port a gesture needs")
_AMEND = next((t for t in TOOLS if t.name == "project_amend"), None)
if _AMEND is not None:
    _src = ast.unparse(_AMEND)
    ok("Project.port_for" in _src,
       "project_amend asks the ladder rather than deciding")
    ok("Project.refuse_mixed" in _src,
       "and the mixed call is refused by the ENGINE, where a suite can reach it")
    # And in the right order: refused BEFORE the gate is opened, so the caller
    # is told which field costs more instead of being told the pair is wrong.
    _refuse = [n.lineno for n in ast.walk(_AMEND) if isinstance(n, ast.Call)
               and ast.unparse(n.func) == "Project.refuse_mixed"]
    _gate = [n.lineno for n in ast.walk(_AMEND) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "_admin"]
    ok(_refuse and _gate and max(_refuse) < min(_gate),
       "and it is refused before the gate is opened, so the message names the "
       "field and not the credential", f"refuse {_refuse}, gate {_gate}")
# The entities and actions are the engine's vocabulary, and the surface must
# not carry a second copy of it: a list here would be a list to keep in step.
_VOCAB = {"domain", "consumer", "group", "create", "retire", "revive"}
_LITERALS = {n.value for n in ast.walk(_AMEND) if isinstance(n, ast.Constant)
             and isinstance(n.value, str) and n.value in _VOCAB} if _AMEND else set()
ok(not _LITERALS,
   "and project_amend spells no entity or action of its own: the vocabulary is "
   "the engine's", sorted(_LITERALS))

print("\n== server.py -> rules.py: every call lands ==")


def signature(fn: ast.FunctionDef):
    pos = [a.arg for a in fn.args.posonlyargs + fn.args.args][1:]     # drop self
    kwonly = [a.arg for a in fn.args.kwonlyargs]
    n_defaults = len(fn.args.defaults)
    required = pos[:len(pos) - n_defaults] if n_defaults else pos
    required += [a.arg for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults) if d is None]
    return pos, kwonly, set(required)


# Since v4.0.0 the engine is not reached through a module-level name any more:
# a tool opens a door first — `_project(project)` for the reference code,
# `_admin(project, key)` for the pair — and calls the engine on what comes
# back. So the seam is read from the DOOR: every engine call is a call on the
# result of one of the two, directly or through the local name it was bound to.
# A call on anything else is not a call this file can vouch for, and it says so
# rather than passing over it.
DOORS = ("_project", "_admin")


def _door_of(node) -> str | None:
    """Which door a receiver expression came out of, or None if it is neither."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in DOORS:
        return node.func.id
    return None


def _engine_calls(fn):
    """(door, method, node) for every engine call inside one function.

    A receiver that is a local NAME is resolved by looking at what that name
    was assigned in this same function — `prj = _admin(...) if admin else
    _project(...)` yields both doors, and the caller decides what that means.
    A name assigned from neither door is reported as an unknown door, because
    an engine call nobody can attribute to a gate is the whole thing this
    section exists to catch."""
    bound: dict[str, set] = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    doors = set()
                    for sub in ast.walk(n.value):
                        d = _door_of(sub)
                        if d:
                            doors.add(d)
                    bound.setdefault(t.id, set()).update(doors or {"?"})
    out = []
    for n in ast.walk(fn):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
            continue
        recv = n.func.value
        door = _door_of(recv)
        if door:
            out.append((door, n.func.attr, n))
        elif isinstance(recv, ast.Name) and recv.id in bound:
            for d in sorted(bound[recv.id]):
                out.append((d, n.func.attr, n))
    return out


CALLS = [(d, m, n) for t in TOOLS for (d, m, n) in _engine_calls(t)]
ok(len(CALLS) >= len(TOOLS),
   f"{len(CALLS)} calls into the engine found in server.py, one door each")
ok(not [1 for d, _, _ in CALLS if d == "?"],
   "and every one of them came out of a door this file knows",
   [m for d, m, _ in CALLS if d == "?"])

for _door, name, call in CALLS:
    where = f"line {call.lineno}"
    if name not in METHODS:
        ok(False, f"Project.{name} exists", where)
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
    ok(not problems, f"Project.{name}(...) matches its signature",
       f"{where}: {'; '.join(problems)}")

# The router's own calls, which are the boot's and the doors': same check, a
# different class.
ROUTER_CALLS = [n for n in ast.walk(SERVER_TREE)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "registry"]
ok(len(ROUTER_CALLS) >= 3, f"{len(ROUTER_CALLS)} calls into the router")
for call in ROUTER_CALLS:
    name = call.func.attr
    if name not in ROUTES:
        ok(False, f"Registry.{name} exists", f"line {call.lineno}")
        continue
    pos, kwonly, required = signature(ROUTES[name])
    covered = set(pos[:len(call.args)]) | {k.arg for k in call.keywords if k.arg}
    ok(not (required - covered), f"Registry.{name}(...) matches its signature",
       f"line {call.lineno}: missing {', '.join(sorted(required - covered))}")

# =====================================================================
# 2 · every tool that writes goes through _admin
# =====================================================================

print("\n== every write passes the maintenance gate ==")



# ---------------------------------------------------------------------
# No parameter dies at the seam. rules_propose accepted `supersedes` in
# its signature — so the schema advertised it and the door took the
# argument — and then never passed it to the engine: the value died
# between the two files, in silence. The GATE of 2026-08-10 found it from
# the connector, with the engine healthy and the manual right; the suites
# missed it because collaudo calls the engine directly and this file
# measured signatures, not the forwarding — the wiring is not the
# behaviour. A parameter the body never READS is a dropped argument by
# construction, so it fails here, by name.
# ---------------------------------------------------------------------

print("\n== no tool parameter dies at the seam ==")
for _t in TOOLS:
    _params = [a.arg for a in _t.args.posonlyargs + _t.args.args + _t.args.kwonlyargs]
    _read = {n.id for n in ast.walk(ast.Module(body=_t.body, type_ignores=[]))
             if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    _dead = [p for p in _params if p not in _read]
    ok(not _dead, f"{_t.name}: every parameter is read in the body",
       f"dropped at the seam: {', '.join(_dead)}")

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
# the whole surface tolerates five escapees, and one escapee is the realistic
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
# time this file has paid for a textual check. Since the adoption the one
# legitimate call lives in the ENGINE's make_tool: in server.py, calling it
# anywhere is a tool that converts nothing.
_MCP_TOOL = [n for n in ast.walk(SERVER_TREE) if isinstance(n, ast.Call)
             and ast.unparse(n.func) == "mcp.tool"]
ok(len(_MCP_TOOL) == 0, "`mcp.tool` is never called in server.py — the one "
                        "call lives inside the engine's make_tool",
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
# block in its manual; this project has no such block, so the manuals' PROSE is
# the witness: every tool either of them names must still be a tool. Injected
# and confirmed: removing @tool from rules_search left the whole suite green
# before this existed.
#
# BOTH manuals, one loop over MANUALS. Reading only the first would leave the
# second as prose nobody verifies — and the second is the one no other check in
# this file touches, so it is where a dead name would live longest.
#
# The pattern is a SHAPE, not a list of the tools that exist. Deriving it from
# TOOL_NAMES would be the trap: the whole point is to catch a name that is no
# longer a tool, and a pattern built from the tools that are left cannot match
# one that has gone. `_guide` is in there because the two manuals name each
# other. What still escapes: a prefixless tool that is not a manual — there is
# none today, and the engine witness below covers every tool anyway.
# `tasks_` joined the shape when the task log arrived. It has to be here and
# not in a list of the tools that exist: the whole job of this pattern is to
# catch a name that is no longer a tool, and a pattern built from the tools
# that are left cannot match one that has gone.
NAME_IN_PROSE = re.compile(r"\b((?:rules|tasks)_[a-z_]+|[a-z][a-z_]*_guide)\b")
# Names that read like tools and are not, allowed in prose anywhere. It has
# to be subtracted in BOTH places the shape is used: naming the server in a
# manual is a legitimate sentence, and a check that goes red on a legitimate
# sentence gets deleted rather than obeyed.
NOT_TOOLS = {"rules_mcp",
             # Field names in verdicts, which read like tools and are not.
             # They live in the same set as rules_mcp on purpose: one list of
             # what is allowed to look like a tool without being one, so the
             # exception is data a reader can see rather than a special case
             # buried in a check.
             "rules_affected", "rules_without_perimeter",
             # Counters in the verdicts, same case as the two above: they read
             # like tools because the prefix is the subject, not a namespace.
             "rules_in_force", "tasks_open", "tasks_urgent", "tasks_stale",
             # And the two manuals name each other by FILE, which the shape
             # below reads as a name ending in _guide.
             "reference_guide"}
for _manual, _text in MANUALS.items():
    ok(_text is not None, f"{_manual} is there to be read at all")
    _named = set(NAME_IN_PROSE.findall(_text or "")) - NOT_TOOLS
    ok(_named, f"{_manual} names tools at all — an empty witness is not one")
    _vanished = sorted(n for n in _named if n not in TOOL_NAMES)
    ok(not _vanished,
       f"every tool {_manual} names is still registered as one "
       f"({len(_named)} named)", _vanished)

# The manuals do not name every tool, and they are PROSE — rewrite a sentence
# and the witness is gone. So the real witness is the engine: a function in
# server.py that reaches `registry.<something>` is a tool by definition, and if
# it is not one any more it has fallen off the surface while still looking like
# a tool. This covers every one of them and cannot be talked out of it.
_TOUCHES_REGISTRY = []
for _fn in ast.walk(SERVER_TREE):
    if not isinstance(_fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    # The two DOORS are the exception, and they are the only one: their whole
    # job is to touch the router. Anything else that reaches it and is not a
    # tool has fallen off the surface while still looking like one.
    if is_tool(_fn) or _fn.name in ("tool", "guarded", "env", "_admin", "_project"):
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

# The gate is read off the RECEIVER now, and that is stricter than it was: a
# tool used to satisfy this by calling `_admin` anywhere in its body, which a
# gate opened and then not used would have satisfied too. Now the question is
# whose door the writing call came out of — `_admin(...)` or `_project(...)` —
# and a mutating call on the low door fails no matter how many gates the
# function opened elsewhere.
for tool in TOOLS:
    writes = {(d, m) for d, m, _ in _engine_calls(tool) if m in MUTATING}
    if not writes:
        continue
    low = sorted(m for d, m in writes if d != "_admin")
    named = ", ".join(sorted(m for _, m in writes))
    if tool.name in UNGATED_ON_PURPOSE:
        ok(all(d != "_admin" for d, _ in writes) or tool.name in ADMIN_IF_KEY,
           f"{tool.name} is ungated ON PURPOSE — {UNGATED_ON_PURPOSE[tool.name][:60]}...",
           "it now writes through _admin only: if that is the new decision, drop "
           "the exception")
        continue
    if tool.name in ADMIN_IF_KEY:
        ok("_admin" in {d for d, _ in writes},
           f"{tool.name} has BOTH doors — {ADMIN_IF_KEY[tool.name][:60]}...",
           "the admin door is not among them")
        continue
    ok(not low, f"{tool.name} writes through _admin only: {named}",
       f"through the reference code alone: {', '.join(low)}")

ok(set(UNGATED_ON_PURPOSE) <= TOOL_NAMES,
   "every documented exception names a tool that exists",
   sorted(set(UNGATED_ON_PURPOSE) - TOOL_NAMES))

# The gate is a NAME, and every check above is happy as long as the name is
# called. `_admin` redefined once more further down — under an `if
# os.environ.get("DEV")`, which is the shape this arrives in — leaves every one
# of them green with no gate anywhere. Python does not warn: the last
# definition wins. So the definition is pinned too: one, at module level, never
# reassigned.
_ADMIN = sole_binding("_admin", (ast.FunctionDef, ast.AsyncFunctionDef),
                      "the last binding wins and every gated tool calls the name")

# And the BODY of the gate, because everything else here pins the name, the
# count and the call site while leaving what it does unconstrained. Since
# v3.0.0 the gate DELEGATES to the engine — the key is per-project data, so
# the check lives where the data lives — and what is pinned here is the
# delegation, as written: one statement, straight through, nothing before it
# and nothing conditional around it. The comparison itself is pinned in the
# ENGINE below, same doctrine.
if _ADMIN is not None:
    _gbody = [s for s in _ADMIN.body
              if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    ok(len(_gbody) == 1
       and ast.unparse(_gbody[0]) == "return check_admin(registry, project, key)",
       "and its whole body is the delegation to the engine, as written",
       ast.unparse(_gbody[0])[:70] if _gbody else "(empty)")

# The LOW door, pinned the same way and for the same reason: it is the one
# every read comes out of, so a line slipped in front of it — a cache, a
# default project, a fallback — would be a line no other check in this file
# looks at.
_LOW = sole_binding("_project", (ast.FunctionDef, ast.AsyncFunctionDef),
                    "every read of the project comes out of this one name")
if _LOW is not None:
    _lbody = [s for s in _LOW.body
              if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    ok(len(_lbody) == 1
       and ast.unparse(_lbody[0]) == "return registry.project(project)",
       "and the reference-code door is the router call, and nothing else",
       ast.unparse(_lbody[0])[:70] if _lbody else "(empty)")

# The engine half: check_architect resolves the project FIRST (one message
# for every failure), compares with secrets.compare_digest — `==` on a secret
# is a different defect — and raises. Pinned on the source of rules.py.
_CHK = next((n for n in ast.walk(ENGINE_TREE)
             if isinstance(n, ast.FunctionDef) and n.name == "check_admin"), None)
ok(_CHK is not None, "check_admin exists in the engine")
if _CHK is not None:
    _chk_src = ast.unparse(_CHK)
    ok("secrets.compare_digest(" in _chk_src,
       "check_admin compares in constant time")
    ok(_chk_src.count("RulesError(ERR_MAINT)") >= 2,
       "and every failure raises the SAME message: code and key are not told apart")

# And WHERE it is called, which is the part a name-counting check cannot see.
# `if key: _admin(...)` reads like making an argument optional and is an open
# door; so does opening the gate and then calling the engine on the other one.
# Both are answered by the same question, asked of every administration tool:
# is the engine call made ON the gate?
#
# The four tools of ADMIN_IF_KEY choose their door from what was passed, so for
# them the requirement is weaker BY DESIGN and written down as such: the admin
# door must be one of the two, and the choice must be made on `key` and on
# nothing else.
for _t in TOOLS:
    _doors = {d for d, _, _ in _engine_calls(_t)}
    if not _doors:
        continue
    if _t.name in ADMIN_IF_KEY:
        _src = ast.unparse(_t)
        ok("_admin" in _doors and "key" in _src,
           f"{_t.name}: both doors, and the choice is made on `key`",
           f"doors: {sorted(_doors)}")
        continue
    _params = [a.arg for a in _t.args.posonlyargs + _t.args.args]
    if "key" in _params:
        ok(_doors == {"_admin"},
           f"{_t.name}: every engine call is made ON the gate",
           f"doors: {sorted(_doors)}")
    else:
        ok(_doors == {"_project"},
           f"{_t.name}: no key in the signature, and no gate opened either",
           f"doors: {sorted(_doors)}")

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

# The two readings. The consumer reading is the ID and the body; the why is
# readable where a person decides. Each sentence is pinned in the manual that
# carries it and COUNTED, not `in`-tested: a rewrite that leaves a second,
# contradicting copy behind is exactly what `in` cannot see.
ok(GUIDE_SRC.count("the ID and the body, and nothing else") == 1,
   "the user manual pins the consumer reading, exactly once",
   GUIDE_SRC.count("the ID and the body, and nothing else"))
ok(GUIDE_SRC.count("`reason` is immutable") == 1,
   "the manual pins the immutable reason, exactly once",
   GUIDE_SRC.count("`reason` is immutable"))
ok("does not keep it" not in GUIDE_SRC,
   "the manual no longer says the reason column loses the why")
# And the consumer-facing docstrings stopped promising the fields that left: a
# stale description does not fail, it advises badly, which is worse.
for _t in TOOLS:
    if _t.name in ("rules_list", "rules_get", "rules_search"):
        _doc = ast.get_docstring(_t) or ""
        for _tok in ("`via`", "`breadth`"):
            ok(_tok not in _doc,
               f"{_t.name} no longer promises {_tok} to a consumer")

# =====================================================================
# 2c · the ceilings in the manual are the ceilings in the engine
# =====================================================================

print("\n== the manual's ceilings are rendered from the engine's constants ==")

# A manual with no limits is useless to a caller; a manual with the wrong ones
# is worse, because the caller plans around them. The way out is not to drop
# them, it is to read the row back OUT of the manual and compare it with the
# constant — an `in` test would be satisfied while a second, stale row sat
# right above saying something else, which is the state a rewrite leaves
# behind. So: find every row with that label, and demand the list be exactly
# one, holding exactly the constant.
#
# `import rules` and getattr, not `from rules import ...`: a from-import of a
# constant that got renamed raises ImportError HERE, in the middle of the file,
# and everything below — the Dockerfile section, the badges, the whole surface
# — silently never runs.
import rules as _rules                                          # noqa: E402

for _label, _attr, _unit in (("IDs per `rules_get`", "GET_IDS", ""),
                             ("body of one rule", "MAX_BODY_BYTES", " bytes"),
                             ("numbers in one domain", "MAX_SEQ", "")):
    _v = getattr(_rules, _attr, None)
    ok(_v is not None, f"the engine still declares {_attr}")
    _found = re.findall(rf"^\|\s*{re.escape(_label)}\s*\|\s*([^|]*?)\s*\|",
                        GUIDE_SRC, re.MULTILINE)
    ok(_found == [f"{_v}{_unit}"],
       f"reference-guide.md states {_label} exactly once, as {_v}{_unit}", _found)

# =====================================================================
# 2d · one manual, whole, behind no door
# =====================================================================

print("\n== one manual, whole, and the stop line inside it ==")

# There is ONE way to reach a file from server.py — a module-level Path
# constant, then .read_text — and every half of that sentence is pinned,
# because each one on its own is a door left ajar. All three of these were
# TRIED as extra tools serving a file, and each slipped a version of this
# section that was missing one line:
#   `with open(path) as f` .............. caught by the ban on open, as a name
#   `_GUIDE.open()` / `io.open()` ....... caught by the ban on open, as an attribute
#   a module helper that reads it ....... caught by the LOCALITY check below
_OPENS = [ast.unparse(n)[:40] for n in ast.walk(SERVER_TREE) if isinstance(n, ast.Call)
          and ((isinstance(n.func, ast.Name) and n.func.id in ("open", "fdopen"))
               or (isinstance(n.func, ast.Attribute) and n.func.attr in ("open", "fdopen")))]
ok(not _OPENS, "server.py never opens a file by hand: it goes through a Path constant",
   _OPENS)
_READERS = [n for n in ast.walk(SERVER_TREE) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in ("read_text", "read_bytes")]
# A local name is allowed as the receiver, and only on one condition: every
# assignment to it inside that same function came from one of the constants.
# `page = _GUIDE_ADMIN if ... else _GUIDE` is the shape the two manuals need,
# and it is still a read that cannot reach a path from anywhere else — while
# `page = Path(whatever)` is not, and fails here.
_ENCLOSES = {}
for _fn in ast.walk(SERVER_TREE):
    if isinstance(_fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for _sub in ast.walk(_fn):
            _ENCLOSES.setdefault(id(_sub), _fn)


def _from_constant(recv, holder) -> bool:
    txt = ast.unparse(recv)
    if txt in PATH_CONSTS:
        return True
    if not (isinstance(recv, ast.Name) and holder is not None):
        return False
    seen = False
    for a in ast.walk(holder):
        targets = []
        if isinstance(a, ast.Assign):
            targets = a.targets
        elif isinstance(a, (ast.AnnAssign, ast.AugAssign)):
            targets = [a.target]
        else:
            continue
        # Tuple targets are matched ELEMENT BY ELEMENT: `level, page = "work",
        # _GUIDE` assigns a string and a path in one statement, and judging the
        # whole right-hand side would call that path a string.
        pairs = []
        for t in targets:
            if isinstance(t, (ast.Tuple, ast.List)) and isinstance(a.value, (ast.Tuple, ast.List)) \
                    and len(t.elts) == len(a.value.elts):
                pairs.extend(zip(t.elts, a.value.elts))
            elif isinstance(t, (ast.Tuple, ast.List)):
                pairs.extend((e, a.value) for e in t.elts)
            else:
                pairs.append((t, a.value))
        for target, value in pairs:
            if not (isinstance(target, ast.Name) and target.id == recv.id):
                continue
            seen = True
            sources = {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}
            strings = {n.value for n in ast.walk(value)
                       if isinstance(n, ast.Constant) and isinstance(n.value, str)}
            if not sources <= set(PATH_CONSTS) or strings or not sources:
                return False
    return seen


_LOOSE = sorted({ast.unparse(n.func.value) for n in _READERS
                 if not _from_constant(n.func.value, _ENCLOSES.get(id(n)))})
ok(not _LOOSE, "and every read goes through one of those constants", _LOOSE)

_REF = next((t for t in TOOLS if t.name == "reference_guide"), None)
ok(_REF is not None, "reference_guide is exposed")
ok("legislator_guide" not in TOOL_NAMES,
   "legislator_guide is gone: the separate door protected an hygiene with no "
   "readers, and the craft now lives past the stop line")

# LOCALITY: a constant may be named only inside the tool that serves it. Pull
# the read one function further out — `def _text(): return _GUIDE.read_text()`,
# called by a second tool — and every check that looks INSIDE a tool for a
# read goes blind, because there is no read in there any more. Measured: an
# ungated extra tool built that way passed everything, on the two-manual
# version of this section.
_ENCLOSING = {}
for _fn in ast.walk(SERVER_TREE):
    if isinstance(_fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for _sub in ast.walk(_fn):
            _ENCLOSING.setdefault(id(_sub), _fn.name)
_TOUCHES_CONST = {}
for _n in ast.walk(SERVER_TREE):
    if isinstance(_n, ast.Name) and _n.id in PATH_CONSTS and isinstance(_n.ctx, ast.Load):
        _TOUCHES_CONST.setdefault(_ENCLOSING.get(id(_n), "(module level)"), set()).add(_n.id)
ok(set(_TOUCHES_CONST) == {"reference_guide"},
   "only reference_guide ever names the manual's path — no helper in between",
   sorted(_TOUCHES_CONST))

# WHICH FILE EACH BRANCH OPENS, and it is the whole of the two-manual
# decision. The half you cannot read is a file this call never opens — so the
# guarantee is not "the text was cut correctly", it is "the branch without the
# key does not name that constant at all". Read off the AST: the admin
# constant may appear only where `_admin` has already been called on the way
# in, and the bare branch may not name it anywhere.
if _REF is not None:
    _admin_calls = [n for n in ast.walk(_REF)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "_admin"]
    ok(len(_admin_calls) == 1,
       "reference_guide opens the gate exactly once — the admin half has one door",
       len(_admin_calls))
    # The gate and the admin constant live in the SAME branch, and the work
    # constant does not: `ast.If` bodies, not line numbers, because a check on
    # line order is satisfied by a gate that guards nothing.
    _branch_ok = False
    for _if in [n for n in ast.walk(_REF) if isinstance(n, ast.If)]:
        _in_body = {n.id for b in _if.body for n in ast.walk(b) if isinstance(n, ast.Name)}
        _in_else = {n.id for b in _if.orelse for n in ast.walk(b) if isinstance(n, ast.Name)}
        if "_admin" in _in_body and "_GUIDE_ADMIN" in _in_body \
                and "_GUIDE_ADMIN" not in _in_else and "_GUIDE" in _in_else:
            _branch_ok = True
    ok(_branch_ok,
       "the admin manual is named ONLY where the gate has just been passed, and "
       "the bare branch never names it")
    # And the answer says which half it served, so a caller can tell a work
    # manual from a truncated admin one without reading the prose.
    _levels = {n.value for n in ast.walk(_REF)
               if isinstance(n, ast.Constant) and n.value in ("work", "admin")}
    ok(_levels == {"work", "admin"},
       "and it declares the level it served, both of them", sorted(_levels))
    _reads = {PATH_CONSTS.get(c) for c in _TOUCHES_CONST.get("reference_guide", set())}
    ok(_reads == {"reference-guide.md", "reference-guide-admin.md"},
       "reference_guide serves the two manuals, and nothing else",
       sorted(map(str, _reads)))

# A file missing from the image is OURS, not the caller's. Left as a RulesError
# it would leave one quiet INFO line starting with the word "refused" — a
# broken image wearing the face of a normal answer, which is the defect the
# decorator exists to close, inverted.
if _REF is not None:
    _raised = {ast.unparse(n.exc.func) for n in ast.walk(_REF)
               if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call)}
    ok(_raised == {"RulesFault"},
       "reference_guide raises RulesFault when the file is not there, never RulesError",
       sorted(_raised))

# Every file a tool serves has to exist, and be IN the image. The explicit list
# in the Dockerfile section is the other half; this half is derived, so a
# manual added tomorrow cannot be forgotten in a list nobody remembers to
# extend. The defect has been paid once already, with reference_guide pointing
# at a file that did not exist.
ok(SERVED_FILES == ["reference-guide-admin.md", "reference-guide.md"],
   "server.py serves exactly the two manuals", SERVED_FILES)
for _f in SERVED_FILES:
    ok(os.path.exists(os.path.join(HERE, _f)), f"{_f} exists in the repository")
# And the derived set is the same set the prose checks read. If a file is
# served but unreadable, MANUALS quietly drops it and the loop above turns into
# a loop over nothing — the shape of a check that filters out its own case.
_READABLE = {f for f, text in MANUALS.items() if text is not None}
ok(_READABLE == set(SERVED_FILES),
   "every served manual is one the prose checks actually read",
   sorted(set(SERVED_FILES) - _READABLE))

# The preflight refuses to start a container that is missing it, and that is
# the only place the question gets asked before a chat asks it. Its list is
# written by hand — preflight cannot import server.py, that would drag in
# FastMCP — so the two are held equal here.
_PRE = re.search(r"^MANUALS = \(([^)]*)\)", PREFLIGHT_SRC, re.MULTILINE)
ok(_PRE is not None, "preflight.py declares the manuals it expects in the image")
if _PRE:
    _declared = sorted(re.findall(r'"([^"]+)"', _PRE.group(1)))
    ok(_declared == SERVED_FILES,
       "and that list is exactly what server.py serves", _declared)
_CHECKS_LIST = re.search(r"^CHECKS = \[(.*?)\]", PREFLIGHT_SRC, re.MULTILINE | re.DOTALL)
ok(_CHECKS_LIST is not None and "c_manuals" in _CHECKS_LIST.group(1),
   "and the check is in CHECKS — one that is defined but not listed never runs")

# =====================================================================
# 3 · no docstring points at a tool that is not there
# =====================================================================

print("\n== the docstrings point at things that exist ==")

MENTION = NAME_IN_PROSE   # one shape, defined once: two copies would diverge

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

print("\n== the Gate arrives from the engine, and is wired here ==")

# Since the adoption the Gate LIVES in mcp_common_engine. What stays in this
# repository is the wiring — which class, with whose identity, behind which
# filter — and the wiring is where the silent failures are, so it is pinned
# with the same care the local class was.
sole_import("Gate", "mcp_common_engine.gate")

# Read from the AST, not by searching the text. A substring search is
# satisfied by `#mcp.add_middleware(...)` — the check would go on passing over
# a gate somebody had commented out while chasing something else, which is the
# single most likely way for this line to disappear. (Found by injecting
# exactly that defect, on the local class this section used to read.)
_REGS = [n for n in SERVER_TREE.body
         if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
         and ast.unparse(n.value.func) == "mcp.add_middleware"]
ok(len(_REGS) == 1, "the Gate is registered exactly once, at module level",
   len(_REGS))
if _REGS:
    _garg = _REGS[0].value.args[0] if _REGS[0].value.args else None
    ok(isinstance(_garg, ast.Call) and ast.unparse(_garg.func) == "Gate",
       "and what is registered is Gate(...) — the name the import pinned",
       ast.unparse(_garg)[:60] if _garg is not None else "no argument")
    _gkw = ({k.arg: ast.unparse(k.value) for k in _garg.keywords}
            if isinstance(_garg, ast.Call) else {})
    ok(_gkw == {"log": "log", "allowed_login": "ALLOWED_LOGIN",
                "allowed_cidrs": "ALLOWED_CIDRS"},
       "and it is handed OUR logger, OUR login and OUR parsed filter, by keyword "
       "— an empty Gate() would be a gate with nobody allowed and no line to "
       "say so", _gkw)

# The engine's own gate, read from the INSTALLED package. These are the checks
# that guarded the local class — hook pinned, call_next once and last, both
# refusals logged with the method — every one found by injection. They did not
# retire with the move: a stale engine, or a tag that drifted, fails here and
# not in a chat.
ok(ENGINE_PKG is not None, "mcp_common_engine is installed where the suite runs")
if ENGINE_PKG is not None:
    _GTREE = parse(os.path.join(ENGINE_PKG, "gate.py"))
    _EGATES = [n for n in _GTREE.body
               if isinstance(n, ast.ClassDef) and n.name == "Gate"]
    ok(len(_EGATES) == 1, "the engine defines Gate exactly once", len(_EGATES))
if ENGINE_PKG is not None and len(_EGATES) == 1:
    _EGATE = _EGATES[0]
    ok(any(ast.unparse(b) == "Middleware" for b in _EGATE.bases),
       "the engine's Gate subclasses Middleware, which is what makes a hook a hook",
       [ast.unparse(b) for b in _EGATE.bases])
    # ALL the assignments, not the first one: a second `HOOK = ...` underneath
    # is what wins at runtime.
    _assigned = [s.value.value for s in _EGATE.body
                 if isinstance(s, ast.Assign)
                 and any(getattr(t, "id", "") == "HOOK" for t in s.targets)
                 and isinstance(s.value, ast.Constant)]
    _declared = _assigned[-1] if _assigned else None
    ok(_assigned == ["on_request"],
       "Gate.HOOK pins the decision: on_request, assigned exactly once", _assigned)
    _hooks = {n.name for n in _EGATE.body
              if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
              and n.name.startswith("on_")}
    ok(_hooks == {_declared}, "the Gate hooks exactly what HOOK names",
       sorted(_hooks))
    _hook_fn = next((n for n in _EGATE.body
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
        _warns = [n for n in ast.walk(_EGATE) if isinstance(n, ast.Call)
                  and ast.unparse(n.func) == "self.log.warning"]
        ok(len(_warns) >= 2, "both refusals are logged, identity and origin",
           len(_warns))
        _named = [w for w in _warns
                  if any(ast.unparse(a) == "ctx.method" for a in w.args)]
        ok(len(_named) == len(_warns) and bool(_named),
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

# Backslash continuations joined first: a COPY spread over two lines is ONE
# instruction to docker, and reading it as two loses whatever is on the second.
DOCKER_COPIES = [l for l in DOCKERFILE.replace("\\\n", " ").splitlines()
                 if l.startswith("COPY ")]
ok(not any("*" in l for l in DOCKER_COPIES),
   "Dockerfile: no wildcard COPY — the test files do not belong in the image",
   [l for l in DOCKER_COPIES if "*" in l])
for f in ("rules.py", "server.py", "preflight.py", "entrypoint.sh",
          "reference-guide.md", "reference-guide-admin.md"):
    ok(any(re.search(rf"\b{re.escape(f)}\b", l) for l in DOCKER_COPIES),
       f"Dockerfile: {f} is copied in")

# And the same thing DERIVED, because the list above is written by hand and a
# module added later is a module nobody remembers to add to it. Every local
# module server.py imports must be in the image: left out, the container dies
# at import with a ModuleNotFoundError, the suites all pass, and the failure
# arrives at the first Apply — after the tag. web.py was exactly that, and
# this check is what found it.
_LOCAL = {a.name.split(".")[0] for n in SERVER_TREE.body if isinstance(n, ast.Import)
          for a in n.names} | {n.module.split(".")[0] for n in SERVER_TREE.body
                               if isinstance(n, ast.ImportFrom) and n.module}
_LOCAL = {m for m in _LOCAL if os.path.exists(os.path.join(HERE, f"{m}.py"))}
ok(len(_LOCAL) >= 2, f"server.py imports {len(_LOCAL)} modules of this repository",
   sorted(_LOCAL))
for _m in sorted(_LOCAL):
    ok(any(re.search(rf"\b{re.escape(_m)}\.py\b", l) for l in DOCKER_COPIES),
       f"Dockerfile: {_m}.py is copied in — server.py imports it", DOCKER_COPIES)

# And the same thing derived, so the list above is a second opinion and not the
# only one. A tool that serves a file the image does not carry answers with a
# fault in a chat instead of a red line here — this project has paid that once,
# with reference_guide pointing at a file nobody had written.
for _f in SERVED_FILES:
    ok(any(re.search(rf"\b{re.escape(_f)}\b", l) for l in DOCKER_COPIES),
       f"Dockerfile: {_f} is copied in — a tool serves it", DOCKER_COPIES)
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

# The manual is a tool that reads a file. Without the file the tool answers
# with a fault, and the failure surfaces in a chat rather than here.
ok(os.path.exists(os.path.join(HERE, "reference-guide.md")),
   "the file reference-guide.md actually exists")

print("\n== a designed refusal does not look like a fault in the log ==")

# Without this, every wrong project code prints a thirty-line traceback at ERROR,
# shaped exactly like a real bug. After a week of those nobody reads them, and
# the next genuine fault arrives disguised as routine.
#
# It has to be the DECORATOR and not a middleware: call_tool applies middleware
# outside and logs inside, so a middleware sees the exception after
# logger.exception has already run. That cost an hour, and since the adoption
# the decorator lives in mcp_common_engine.refusals. What stays here is the
# BINDING — which error class is a refusal and which is a fault is the one
# thing the engine cannot know.
#
# The check changed SUBJECT with the move, exactly as the engine's docstring
# demands, and it was rewritten BEFORE the move. The old law was "one `def
# tool`, never rebound", and `tool = make_tool(...)` is an ASSIGNMENT — the
# very shape that law forbade, because `tool = mcp.tool` registers every tool
# naked while every counter keeps agreeing. The new law: the name `tool` is
# bound exactly once, at module level, to a call of make_tool from the engine,
# with OUR classes, and to nothing else, ever.
sole_import("make_tool", "mcp_common_engine.refusals")

_TOOL_BINDS = []
for _n in ast.walk(SERVER_TREE):
    if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
            and _n.name == "tool":
        _TOOL_BINDS.append(_n)
    elif isinstance(_n, (ast.Import, ast.ImportFrom)):
        if any((a.asname or a.name.split(".")[0]) == "tool" for a in _n.names):
            _TOOL_BINDS.append(_n)
    elif isinstance(_n, (ast.Assign, ast.AnnAssign, ast.AugAssign,
                         ast.NamedExpr, ast.For)):
        if any(isinstance(x, ast.Name) and x.id == "tool"
               and isinstance(x.ctx, ast.Store) for x in ast.walk(_n)):
            _TOOL_BINDS.append(_n)
    elif isinstance(_n, ast.Global) and "tool" in _n.names:
        _TOOL_BINDS.append(_n)
ok(len(_TOOL_BINDS) == 1, "`tool` is bound exactly once in server.py",
   [ast.unparse(n)[:60] for n in _TOOL_BINDS])
_TB = _TOOL_BINDS[0] if len(_TOOL_BINDS) == 1 else None
ok(isinstance(_TB, ast.Assign) and _TB in SERVER_TREE.body,
   "and that binding is the one assignment, at module level",
   ast.unparse(_TB)[:60] if _TB is not None else "absent")
if isinstance(_TB, ast.Assign):
    _tv = _TB.value
    ok(isinstance(_tv, ast.Call) and isinstance(_tv.func, ast.Name)
       and _tv.func.id == "make_tool",
       "and it is a call of make_tool — `tool = mcp.tool` is the naked door",
       ast.unparse(_tv)[:60])
    if isinstance(_tv, ast.Call):
        _tpos = [ast.unparse(a) for a in _tv.args]
        _tkw = {k.arg: ast.unparse(k.value) for k in _tv.keywords}
        ok(_tpos == ["mcp", "log"]
           and _tkw == {"refusal": "RulesError", "fault": "RulesFault"},
           "with our server, our logger, RulesError as the refusal and "
           "RulesFault as the fault — swapped, every fault takes the quiet path",
           f"{_tpos} {_tkw}")

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

# And the other direction, which is the one that bites: an exception class
# NAMED in server.py but not imported is a NameError at the first refusal, and
# no suite would see it — none of the three imports server.py, on purpose. The
# import line is one line, it is edited by hand, and dropping RulesFault from
# it kills the decorator's first branch: every refusal in the service.
#
# Three forms, because two of them were missed on the first pass and both were
# measured green: `raise Foo(...)` is a Call, `raise Foo` is a bare Name, and
# `except (OSError, Foo):` is a Tuple that unparses to something that is not an
# identifier at all. So: walk the nodes and collect the NAMES.
_NAMED_EXC: set[str] = set()
for _n in ast.walk(SERVER_TREE):
    if isinstance(_n, ast.Raise) and _n.exc is not None:
        _target = _n.exc.func if isinstance(_n.exc, ast.Call) else _n.exc
        _NAMED_EXC |= {x.id for x in ast.walk(_target) if isinstance(x, ast.Name)}
    elif isinstance(_n, ast.ExceptHandler) and _n.type is not None:
        _NAMED_EXC |= {x.id for x in ast.walk(_n.type) if isinstance(x, ast.Name)}
# `dir(builtins)` and not `dir(__builtins__)`: run as a script __builtins__ is
# the module, imported it is a dict, and the check would silently change shape.
import builtins as _builtins                                    # noqa: E402

_BOUND = set(_IMPORTED) | set(dir(_builtins))
for _n in SERVER_TREE.body:
    if isinstance(_n, (ast.Import, ast.ImportFrom)):
        _BOUND |= {(a.asname or a.name.split(".")[0]) for a in _n.names}
    elif isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        _BOUND.add(_n.name)
    elif isinstance(_n, ast.Assign):
        _BOUND |= {t.id for t in _n.targets if isinstance(t, ast.Name)}
_UNDECLARED = sorted(e for e in _NAMED_EXC if e not in _BOUND)
ok(not _UNDECLARED,
   "every exception server.py raises or catches by name is imported or defined",
   _UNDECLARED)

# THE WRAPPER, read from the INSTALLED engine. These are the checks that
# guarded the local decorator — every one bought by a mutation that stayed
# green — pointed at where the code now lives. In the engine the two classes
# are the factory's PARAMETERS, `fault` and `refusal`; the binding above is
# what makes them RulesFault and RulesError here.
_guarded = None
if ENGINE_PKG is not None:
    _RTREE = parse(os.path.join(ENGINE_PKG, "refusals.py"))
    _MAKES = [n for n in _RTREE.body
              if isinstance(n, ast.FunctionDef) and n.name == "make_tool"]
    ok(len(_MAKES) == 1, "the engine defines make_tool exactly once", len(_MAKES))
    _converter = _MAKES[0] if _MAKES else None
    if _converter is not None:
        # THE WRAPPER, not the decorator: everything below reads the function
        # that is actually returned. Gathered with ast.walk over the whole
        # factory, the checks ask only that the pieces be WRITTEN somewhere
        # inside it — three mutations exploited that on the local copy and
        # stayed green (a dead `_convert()`, a decoy handler, a dead handler
        # holding the level).
        _WRAPPER = "guarded"
        _guarded = next((n for n in ast.walk(_converter)
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and n.name == _WRAPPER), None)
        ok(_guarded is not None, f"the engine's factory defines its wrapper, `{_WRAPPER}`")
        _REBOUND = [n for n in ast.walk(_converter) if isinstance(n, ast.Assign)
                    and any(getattr(t, "id", "") == _WRAPPER for t in n.targets)]
        ok(not _REBOUND, f"`{_WRAPPER}` is only ever the def, never reassigned",
           [ast.unparse(n) for n in _REBOUND])

if _guarded is not None:
    handlers = [h for t in ast.walk(_guarded) if isinstance(t, ast.Try)
                for h in t.handlers]
    caught = [ast.unparse(h.type) for h in handlers if h.type is not None]
    ok("refusal" in caught, "the wrapper catches the refusal class", caught)

    # The ORDER, which is the whole distinction and is invisible once written:
    # the fault SUBCLASSES the refusal, so the refusal caught first would
    # swallow every fault into the quiet path. Python has no warning for this;
    # this is the warning.
    ok(caught[:1] == ["fault"],
       "and it catches the fault FIRST, or the subclass never gets its turn",
       caught)
    _fault_h = [h for h in handlers
                if h.type is not None and ast.unparse(h.type) == "fault"]
    ok(bool(_fault_h) and all(isinstance(s, ast.Raise) and s.exc is None
                              for h in _fault_h for s in h.body),
       "and it lets a fault rise untouched: traceback at ERROR, as before")

    # The refusal must leave a line of OUR own: FASTMCP_LOG_LEVEL=WARNING
    # drops FastMCP's INFO record before it is printed, so converting alone
    # trades thirty lines for NONE. Measured on both twins.
    _logged = [n for h in handlers for n in ast.walk(h)
               if isinstance(n, ast.Call) and ast.unparse(n.func).startswith("log.")]
    ok(bool(_logged), "the refusal leaves a line of our own, or the conversion "
                      "trades thirty lines for none")
    ok(all(ast.unparse(n.func) == "log.info" for n in _logged),
       "at INFO: WARNING is the Gate's height, and a wrong project code is not "
       "a warning", sorted({ast.unparse(n.func) for n in _logged}))
    ok(all(any(ast.unparse(a) == "fn.__name__" for a in n.args) for n in _logged)
       and all(any(ast.unparse(a) == "e" for a in n.args) for n in _logged),
       "and it names the tool and carries the reason",
       [ast.unparse(n)[:60] for n in _logged])
    raised = [n for h in handlers for n in ast.walk(h)
              if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "ToolError"]
    ok(bool(raised), "and re-raises ToolError")
    levels = [ast.unparse(k.value) for r in raised for k in r.keywords
              if k.arg == "log_level"]
    ok(bool(levels) and set(levels) == {"logging.INFO"},
       "at logging.INFO exactly, which is the decision, and nowhere else",
       levels or "log_level not set")
    ok(any(isinstance(n, ast.Raise) and isinstance(n.cause, ast.Constant)
           and n.cause.value is None for n in ast.walk(_guarded)),
       "with `from None`: the chained traceback is what we are removing")
    ok(any(ast.unparse(d) == "functools.wraps(fn)" for d in _guarded.decorator_list),
       "and functools.wraps ON THE WRAPPER, or every tool loses its schema",
       [ast.unparse(d) for d in _guarded.decorator_list])
    # WHAT it registers: `return mcp.tool(fn)` defines `guarded` and throws it
    # away, with every piece above still written down.
    _registers = [n for n in ast.walk(_converter) if isinstance(n, ast.Call)
                  and ast.unparse(n.func) == "mcp.tool"]
    ok(bool(_registers) and bool(_registers[0].args)
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
    # The second hand-copied number on that line, and it had nothing tied to it
    # either: the badge said one number while the surface said another, and
    # a badge is the first thing anybody reads. The count comes from the AST,
    # so it moves the day a tool is added, which is the day it goes wrong.
    _counts = re.findall(r"badge/MCP-([0-9]+)%20tools?-", _txt)
    ok(_counts == [str(len(TOOLS))],
       f"{_readme}: the tool badge says {len(TOOLS)}", _counts)

_STRAY = [f for f in ("server.py", "preflight.py", "Dockerfile",
                      "entrypoint.sh", "codifier-mcp.xml")
          if re.search(r"^VERSION\s*=", source(os.path.join(HERE, f)), re.MULTILINE)]
ok(not _STRAY, "no second file declares a VERSION of its own", _STRAY)

print("\n== the engine is pinned to a tag, and the pin is what is installed ==")

# A tag is a number a check can compare — this is that check. The tarball
# form, not git+: the image carries no git, and a public tarball needs none.
_REQ = source(os.path.join(HERE, "requirements.txt"))
_PINS = re.findall(r"^mcp-common-engine @ https://github\.com/alcor6502/"
                   r"mcp-common-engine/archive/refs/tags/v(\d+\.\d+\.\d+)"
                   r"\.tar\.gz\s*$", _REQ, re.MULTILINE)
ok(len(_PINS) == 1,
   "requirements.txt pins the engine to ONE tag, in the tarball form", _PINS)

# Two repositories pinning a third can pin different tags, and then
# "identical" quietly becomes "identical if both of them updated". The cure is
# to compare the number where somebody already looks — here, and on the
# startup line below.
ok(_ENG_SPEC is not None, "mcp_common_engine imports where the suite runs")
if _ENG_SPEC is not None:
    import mcp_common_engine as _eng                            # noqa: E402
    ok(_PINS == [_eng.VERSION],
       f"and the engine installed here IS that tag: {_eng.VERSION}",
       f"pin {_PINS} vs installed {_eng.VERSION}")

# fastmcp's version is pinned in the ENGINE's pyproject, where the code that
# depends on its routing lives. A second pin here would be the same number in
# two places, with the expiry date that comes with it.
ok(not re.search(r"^fastmcp", _REQ, re.MULTILINE),
   "fastmcp is not pinned a second time in requirements.txt")

# The startup line prints the engine's version next to our own — the cure the
# engine's README names for the drift its pin makes possible.
_MAIN = next((n for n in SERVER_TREE.body if isinstance(n, ast.If)
              and ast.unparse(n.test) == "__name__ == '__main__'"), None)
ok(_MAIN is not None, "server.py has the __main__ block")
if _MAIN is not None:
    _INFOS = [c for c in ast.walk(_MAIN) if isinstance(c, ast.Call)
              and ast.unparse(c.func) == "log.info"]
    ok(any(any(isinstance(a, ast.Name) and a.id == "ENGINE_VERSION"
               for a in c.args) for c in _INFOS),
       "and the startup line carries ENGINE_VERSION next to VERSION")

# The CI test job installs what the suites now import, from the SAME pin:
# --no-deps, because the suites need no FastMCP — that property is what lets
# them run in under a minute, and it is not one to give up.
_WF = source(os.path.join(HERE, ".github", "workflows", "build.yml"))
ok("pip install --no-deps -r requirements.txt" in _WF,
   "build.yml installs the engine for the suites, --no-deps, from the one pin")

print("\n== a malformed call does not print what it carried ==")

# fastmcp logs invalid arguments ITSELF, at WARNING, with the arguments in the
# line — the record is born on `fastmcp.server.server`, before any tool of ours
# runs, and its handler has `propagate=False`, so it obeys nobody's LOG_LEVEL
# and leaves no `refused` line. For the server, nothing happened. For this
# service the payload is not a document body: it is the PROJECT CODE and the
# ADMIN CODE, which travel as arguments on every maintenance call. One
# forgotten parameter and the credentials are in the container's log.
#
# The cure lives in the engine, from v1.1.0, and is one call. It is pinned here
# because every way of getting it wrong is silent: not calling it, calling it
# before the server object exists (fastmcp has not configured its logging yet,
# so there is no handler to filter and the payload keeps printing), or
# swallowing the RuntimeError that says exactly that.
sole_import("arm_argument_redaction", "mcp_common_engine.logs")

_ARMS = [n for n in ast.walk(SERVER_TREE) if isinstance(n, ast.Call)
         and ast.unparse(n.func) == "arm_argument_redaction"]
ok(len(_ARMS) == 1, "server.py arms the argument redaction, exactly once",
   len(_ARMS))

# AFTER the server object. Creating it is what makes fastmcp configure its
# logging, so the order is not style: armed first, the call finds no handler
# and — by the engine's design — raises rather than reporting a comforting
# zero. Compared by LINE, because that is what "after" means here.
_MCP_ASSIGN = [n for n in SERVER_TREE.body if isinstance(n, ast.Assign)
               and any(getattr(t, "id", "") == "mcp" for t in n.targets)]
ok(len(_MCP_ASSIGN) == 1, "the server object is built exactly once",
   len(_MCP_ASSIGN))
if _ARMS and _MCP_ASSIGN:
    ok(_ARMS[0].lineno > _MCP_ASSIGN[0].lineno,
       "and the arming comes AFTER it — before, fastmcp has not configured its "
       "logging and there is nothing to filter",
       f"arm at line {_ARMS[0].lineno}, server at line {_MCP_ASSIGN[0].lineno}")

# At MODULE level, and not inside anything. Under `if __name__ == "__main__"`
# it would protect the service and leave every other importer — the preflight
# does not import server.py, but a probe or a future second entry point would —
# printing the payload.
_ARM_STMTS = [n for n in SERVER_TREE.body if isinstance(n, ast.Expr)
              and isinstance(n.value, ast.Call)
              and ast.unparse(n.value.func) == "arm_argument_redaction"]
ok(len(_ARM_STMTS) == 1,
   "the arming is a module-level statement, not tucked inside a branch",
   len(_ARM_STMTS))

# And its refusal is NOT swallowed. `try: arm...() except Exception: pass` is
# one line, reads like prudence, and turns the whole cure into a decoration:
# the boot survives and the payload keeps printing.
_SWALLOWED = [ast.unparse(t)[:60] for t in ast.walk(SERVER_TREE)
              if isinstance(t, ast.Try)
              and any(isinstance(n, ast.Call)
                      and ast.unparse(n.func) == "arm_argument_redaction"
                      for n in ast.walk(t))]
ok(not _SWALLOWED,
   "and it is not wrapped in a try: the raise is what stops a boot that would "
   "protect nothing", _SWALLOWED)

# ---------------------------------------------------------------------
# The sister cure: fastmcp's own lines get our line shape, so they carry a
# date. Same handlers, same moment, same reasons — so the same checks, plus
# the one that is only true of this one: the format must arrive as the NAME
# that basicConfig was handed, never as a second copy of the string.
# ---------------------------------------------------------------------
sole_import("arm_timestamps", "mcp_common_engine.logs")

_STAMP = [n for n in ast.walk(SERVER_TREE) if isinstance(n, ast.Call)
          and ast.unparse(n.func) == "arm_timestamps"]
ok(len(_STAMP) == 1, "server.py arms the timestamps, exactly once", len(_STAMP))
if _STAMP and _MCP_ASSIGN:
    ok(_STAMP[0].lineno > _MCP_ASSIGN[0].lineno,
       "and AFTER the server object, like its sister: earlier there is no "
       "handler to format",
       f"arm at line {_STAMP[0].lineno}, server at line {_MCP_ASSIGN[0].lineno}")

_STAMP_STMTS = [n for n in SERVER_TREE.body if isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Call)
                and ast.unparse(n.value.func) == "arm_timestamps"]
ok(len(_STAMP_STMTS) == 1,
   "and it is a module-level statement, not tucked inside a branch",
   len(_STAMP_STMTS))

ok(not [ast.unparse(t)[:60] for t in ast.walk(SERVER_TREE)
        if isinstance(t, ast.Try)
        and any(isinstance(n, ast.Call) and ast.unparse(n.func) == "arm_timestamps"
                for n in ast.walk(t))],
   "and its raise is not swallowed either: no handler means the redaction next "
   "door is protecting nothing")

# THE check that is this cure's own. The engine takes the format as an argument
# and carries no default, so that each server keeps its line shape in ONE
# place; hand it a literal here and there are two copies of the string, which
# agree exactly until somebody edits one. So: one Name, and the SAME name that
# reached basicConfig.
_BASIC = [n for n in ast.walk(SERVER_TREE) if isinstance(n, ast.Call)
          and ast.unparse(n.func) == "logging.basicConfig"]
ok(len(_BASIC) == 1, "logging.basicConfig is called once", len(_BASIC))
_basic_fmt = next((k.value for c in _BASIC for k in c.keywords if k.arg == "format"),
                  None)
ok(isinstance(_basic_fmt, ast.Name),
   "and it is handed a NAMED format, not a literal",
   ast.unparse(_basic_fmt) if _basic_fmt is not None else "absent")
_stamp_fmt = _STAMP[0].args[0] if _STAMP and _STAMP[0].args else None
ok(isinstance(_stamp_fmt, ast.Name),
   "arm_timestamps is handed a NAMED format too",
   ast.unparse(_stamp_fmt) if _stamp_fmt is not None else "absent")
ok(isinstance(_basic_fmt, ast.Name) and isinstance(_stamp_fmt, ast.Name)
   and _basic_fmt.id == _stamp_fmt.id,
   "and it is the SAME name: one line shape, in one place",
   f"{ast.unparse(_basic_fmt) if _basic_fmt else '?'} vs "
   f"{ast.unparse(_stamp_fmt) if _stamp_fmt else '?'}")

# And the string itself is written once. A second literal of the same shape
# somewhere else in the file would be the copy this whole arrangement exists to
# avoid, sitting there waiting for one of the two to be edited.
_FMT_LITERALS = [n for n in ast.walk(SERVER_TREE) if isinstance(n, ast.Constant)
                 and isinstance(n.value, str) and "%(asctime)s" in n.value]
ok(len(_FMT_LITERALS) == 1,
   "the line shape is spelled out exactly once in the file",
   len(_FMT_LITERALS))

# MEASURED, on the three secrets this surface actually carries. The static
# checks above pin that the cure is armed, once, in the right place — none of
# them can say that it WORKS, and the payload here is not a document body like
# the twin's: it is the admin code, the one-time code and a consumer's secret,
# which travel as arguments on the calls that change things. So the filter is
# run against a record shaped like the one fastmcp emits when it rejects a
# malformed call, and the values are looked for in what comes out.
try:
    import logging as _logging

    from mcp_common_engine.logs import _redacted as _redact_values   # noqa: WPS436

    # The shape pydantic hands fastmcp when it rejects a call: a list of error
    # entries, and the caller's arguments under `input`. Written out here
    # rather than mocked, because the whole cure is keyed to that one field
    # name — a probe with a shape of its own would prove something else.
    _PAYLOAD = {"project": "PROJECTCODE1234", "key": "ADMINCODE-TOPSECRET",
                "auth_code": "ONETIME-987654", "consumer_key": "CONSUMER-SECRET"}
    _out = repr(_redact_values(([{"type": "missing", "loc": ("reason",),
                                  "msg": "Field required",
                                  "input": _PAYLOAD}],)))
    _leaked = sorted(v for v in _PAYLOAD.values() if v in _out)
    ok(not _leaked, "and it redacts the whole payload: not one of the project "
       "code, the admin code, the one-time code or a consumer's secret survives "
       "into the line", _leaked)
    ok("<redacted>" in _out, "and what is printed says it was redacted", _out[:60])
except ImportError as _e:                                            # noqa: PERF203
    ok(False, "the engine's redaction can be measured here", str(_e))

print("\n== the template is publishable ==")

import web as _webmod                                           # noqa: E402

TEMPLATE_PATH = os.path.join(HERE, "codifier-mcp.xml")
ok(os.path.exists(TEMPLATE_PATH), "the template is named after the repository")
TEMPLATE = open(TEMPLATE_PATH, encoding="utf-8").read()

# The template published in the repository IS the configuration, so it must
# carry no trace of the machine it was written on.
for residue in ("marlin-kelvin", "svc-a2", "/mnt/cache/Claude", "160.79.104.0/21 #  "):
    ok(residue not in TEMPLATE, f"no personal residue: {residue!r}")

ok("<Repository>ghcr.io/" in TEMPLATE,
   "the template points at ghcr, not at a local image")
# The icon used to be checked here, against a filename written by hand. It is
# checked in its own section further down instead, where the template's URL,
# server.py's ICON_URL, the mimeType and the file on disk are held against
# EACH OTHER — there is no hand copy of the name left to go stale.
# There IS a web interface now, and the field that used to be empty because
# there was none has to point at it: the icon in Unraid's dashboard is how a
# person finds a page whose port they will not remember. It is the UI's port,
# never the MCP's — the MCP has no page, and an icon that opened it would open
# an OAuth challenge with no explanation.
ok("<WebUI/>" not in TEMPLATE,
   "the WebUI field is no longer empty: there is a page to point at")
ok(f"[PORT:{_webmod.DEFAULT_PORT}]" in re.search(r"<WebUI>(.*?)</WebUI>", TEMPLATE).group(1)
   if re.search(r"<WebUI>(.*?)</WebUI>", TEMPLATE) else False,
   "and it points at the UI's port, substituted by Unraid",
   re.search(r"<WebUI>(.*?)</WebUI>", TEMPLATE).group(1)
   if re.search(r"<WebUI>(.*?)</WebUI>", TEMPLATE) else "absent")
# And the port has to be PUBLISHED, or the sentence "you get there with
# IP:port" is false. On a bridge network Docker forwards a published port to
# the container's address; with no mapping in the template nothing is
# forwarded at all, the page answers only inside the container, and the
# failure looks exactly like a service that did not start.
_PORTS = re.findall(r'<Config[^>]*Type="Port"[^>]*Target="(\d+)"'
                    r'|<Config[^>]*Target="(\d+)"[^>]*Type="Port"', TEMPLATE)
_PORTS = sorted({a or b for a, b in _PORTS})
ok(_PORTS == [str(_webmod.DEFAULT_PORT)],
   f"the template publishes the UI's port, and only that one", _PORTS)

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
for var in ("PROVISIONAL_DAYS", "WEB_PORT", "WEB_UI_PASSWORD",
            "ADMIN_AUTH_CODE_DURATION",
            "LOG_LEVEL", "ALLOWED_CIDRS", "DB_DIR", "BACKUP_DIR"):
    ok(f'Target="{var}"' in TEMPLATE, f"template declares {var}")

# The other direction, and it is the one that had nobody watching it. The list
# above catches a variable the template FORGOT; nothing caught a variable the
# template kept after its last reader was deleted, and four of them survived
# that way into v4.0.0 — DB_PATH, WEB_MASTER_CODE, PENDING_CAP and
# WEB_ACTION_CAP, three grains after the code stopped reading them. A dead knob
# is worse than a missing one: it is a form field a person fills in with care,
# and the value goes nowhere.
#
# The set of readers comes from the AST of every module in the image plus the
# names the engine resolves on our behalf, so it moves the day a reader is
# added or removed. Type="Port" and Type="Path" targets are mappings, not
# variables, and are not in this question.
_ENV_READERS = set()
for _mod in ("server.py", "web.py", "rules.py", "preflight.py"):
    _t = parse(os.path.join(HERE, _mod))
    for _n in ast.walk(_t):
        if isinstance(_n, ast.Call) and ast.unparse(_n.func) in (
                "os.environ.get", "os.getenv", "env") \
                and _n.args and isinstance(_n.args[0], ast.Constant) \
                and isinstance(_n.args[0].value, str):
            _ENV_READERS.add(_n.args[0].value)
        elif isinstance(_n, ast.Subscript) and ast.unparse(_n.value) == "os.environ" \
                and isinstance(_n.slice, ast.Constant) \
                and isinstance(_n.slice.value, str):
            _ENV_READERS.add(_n.slice.value)
# Read inside the engine, on this service's behalf, through cidrs_from_env()
# and log_level_from_env(). Named here because the engine's source is not
# what this file parses — and named ONE BY ONE, because a blanket exemption
# would be the hole this check exists to close.
_ENGINE_READS = {"ALLOWED_CIDRS", "ANTHROPIC_CIDR", "LOG_LEVEL"}
# Target is the second attribute, so only the field's Name sits between the
# two — no description is crossed. A port maps to a number and a path to an
# absolute path, so neither can be mistaken for a variable by this pattern.
_DECLARED = {m.group(1) for m in
             re.finditer(r'<Config[^>]*Target="([A-Z][A-Z0-9_]*)"', TEMPLATE)}
_ORPHANS = sorted(_DECLARED - _ENV_READERS - _ENGINE_READS)
ok(not _ORPHANS,
   f"every variable the template declares has a reader ({len(_DECLARED)} declared)",
   _ORPHANS)
# And the four that died, by name: the check above would go green again the day
# somebody re-added a reader for one of them, which is not the same thing as
# these being gone.
for _dead_var in ("DB_PATH", "WEB_MASTER_CODE", "PENDING_CAP", "WEB_ACTION_CAP"):
    ok(f'Target="{_dead_var}"' not in TEMPLATE,
       f"and {_dead_var} is not declared: it has had no reader since v4.0.0")

# The UI's password: mandatory, masked, and blocked at boot while it is a
# placeholder. It is the one variable that cannot be "born optional with a
# working default in the code" — a default for it IS the placeholder the
# preflight refuses. Required in the template moves the failure from a
# container that will not boot to a form that will not save. (Its old sibling
# ADMIN_ACCESS_CODE died in v3.0.0, and the "master" in its own old name died
# in v4.0.0 with the level it claimed: what is behind this password is a UI.)
_MASTER_FIELD = re.search(r'<Config[^>]*Target="WEB_UI_PASSWORD"[^>]*>', TEMPLATE)
ok(_MASTER_FIELD is not None, "the UI's password is a field of the template")
if _MASTER_FIELD:
    _f = _MASTER_FIELD.group(0)
    # The NAME a person reads in Unraid, and it says what the password opens.
    # It is a RENAME on every install that already exists — Unraid keeps the
    # old field under its old target — which is why the description says to
    # carry the value across by hand.
    ok('Name="Web UI Password"' in _f,
       "and it is named after what it opens, not after a level", _f[:60])
    ok('Mask="true"' in _f, "and it is masked, as a secret is", _f[:80])
    ok('Required="true"' in _f,
       "and required: a password with a working default is the open door", _f[:80])
# The one-time code's life is the other kind of new variable: optional, with
# the default in the code, because Unraid does not propagate new variables to
# containers that are already installed.
_CAP_FIELD = re.search(r'<Config[^>]*Target="ADMIN_AUTH_CODE_DURATION"[^>]*>', TEMPLATE)
ok(_CAP_FIELD is not None and 'Required="false"' in _CAP_FIELD.group(0),
   "the one-time code's lifetime is optional, with its default in the code",
   _CAP_FIELD.group(0)[:80] if _CAP_FIELD else "absent")
# The proposal ceiling is not a variable at all any more, and the template must
# not describe one: it is `queue_cap`, policy of each project.
ok("PENDING_CAP" not in TEMPLATE and "WEB_ACTION_CAP" not in TEMPLATE,
   "and no field so much as mentions the two ceilings that became queue_cap")
# And the field that promised a read-only interface that would read a
# VACUUM INTO snapshot no longer says any of that: it was true of a design
# that was not built, and the one that was built writes.
for _dead in ("not built yet", "read-only web interface", "VACUUM INTO snapshot",
              "unpublishable by construction"):
    ok(_dead not in TEMPLATE,
       f"the template no longer promises: {_dead!r}")

# Every variable the service reads through env() without a default is one the
# service cannot start without.
for var in ("BASE_URL", "ALLOWED_GITHUB_LOGIN",
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

print("\n== the helpers come from the engine, once ==")

# The helpers moved to mcp-common-engine: they were the code the twins had
# already written twice. preflight.py must not keep a shadow — a local def
# wins over the import, and the service would run one version while the suite
# certifies another.
_PF_TREE = parse(os.path.join(HERE, "preflight.py"))
_SHARED = ("is_placeholder", "parse_cidrs", "cidrs_from_env", "describe_cidrs",
           "log_level_from_env", "check", "DEFAULT_CIDRS", "LOG_LEVELS",
           "SKIP", "RESULTS")
_SHADOWS = []
for _n in ast.walk(_PF_TREE):
    if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
            and _n.name in _SHARED:
        _SHADOWS.append(_n.name)
    elif isinstance(_n, ast.Assign):
        _SHADOWS += [t.id for t in _n.targets
                     if isinstance(t, ast.Name) and t.id in _SHARED]
ok(not _SHADOWS, "preflight.py defines none of the engine's helpers locally",
   _SHADOWS)
_PF_FROM_ENGINE = {a.asname or a.name
                   for n in _PF_TREE.body if isinstance(n, ast.ImportFrom)
                   and n.module == "mcp_common_engine" for a in n.names}
ok({"SKIP", "RESULTS", "check", "cidrs_from_env", "describe_cidrs",
    "is_placeholder"} <= _PF_FROM_ENGINE,
   "and it imports from the engine what its checks use",
   sorted(_PF_FROM_ENGINE))

# server.py reads the shared answers from the ENGINE, not through preflight:
# one origin, or the two files can disagree by import path alone.
_SRV_FROM = {}
for _n in SERVER_TREE.body:
    if isinstance(_n, ast.ImportFrom):
        _SRV_FROM.setdefault(_n.module, set()).update(
            a.asname or a.name for a in _n.names)
ok({"cidrs_from_env", "describe_cidrs", "log_level_from_env"}
   <= _SRV_FROM.get("mcp_common_engine", set()),
   "server.py takes the config helpers from mcp_common_engine",
   sorted(_SRV_FROM.get("mcp_common_engine", set())))
ok("preflight" not in _SRV_FROM,
   "and imports nothing from preflight any more",
   sorted(_SRV_FROM.get("preflight", set())))

print("\n== the engine's helpers behave as the twins measured ==")

# The tables below certify the INSTALLED engine: the behaviour was measured on
# the two services before the code moved, and the pin is what promises it has
# not changed shape on the way in.
import mcp_common_engine as pf                                  # noqa: E402

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
for _f in ("server.py", "rules.py", "preflight.py", "web.py"):
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

print("\n== the signature left, whole ==")

# Decided 2026-08-10, completed in v3.0.0: sign.py and the signature left
# with v2.0.0, and now the seven migrating tools left too — approve, renew
# and promote to the lot and pending pages, the master operations (create,
# registry, rekey, backup) to the UI behind the master. NOT EXPOSED AT ALL is
# the guarantee, written the way rules_propose documents its own exception:
# the engine methods stay (it is what lets the suites run without a server),
# and who exposes them changed. The digest stays with the lot page — it was
# never the signature's.
ok(not os.path.exists(os.path.join(HERE, "sign.py")),
   "sign.py is gone from the repository")
for _tok in ("APPROVAL_PUBKEY", "APPROVAL_GRACE_UNTIL"):
    for _fname, _src2 in (("codifier-mcp.xml", TEMPLATE), ("server.py", SERVER_SRC),
                          ("rules.py", RULES_SRC), ("preflight.py", PREFLIGHT_SRC)):
        ok(_tok not in _src2, f"{_fname} no longer knows {_tok}")
for _tname in ("rules_approve", "rules_renew", "rules_promote",
               "rules_registry", "rules_project_create", "rules_project_rekey",
               "rules_backup"):
    ok(next((t for t in TOOLS if t.name == _tname), None) is None,
       f"{_tname} is NOT a tool any more: it lives in the UI, behind the master")
# And the container-wide code died with them: no reader, no variable, no
# check. A template that still offered the field would grow a secret nobody
# consumes — the placeholder defect, inverted.
# The prose may still tell the story; what must be gone is the READER and
# the template FIELD — a field nobody consumes is the placeholder defect,
# inverted.
for _fname, _src2 in (("server.py", SERVER_SRC), ("preflight.py", PREFLIGHT_SRC)):
    ok('"ADMIN_ACCESS_CODE"' not in _src2,
       f"{_fname} no longer reads ADMIN_ACCESS_CODE")
ok('Target="ADMIN_ACCESS_CODE"' not in TEMPLATE and
   "ADMIN_ACCESS_CODE" not in TEMPLATE,
   "the template no longer offers the ADMIN_ACCESS_CODE field")
ok("cryptography" not in source(os.path.join(HERE, "requirements.txt"))
   and "cryptography" not in DOCKERFILE,
   "no cryptography dependency survives outside the engine's own")

print("\n== the import door is bricked, and legacy_id went with it ==")

# The seeding pass is not the price of the migration, it is its content: the
# rules go back in one at a time, through rules_propose, each one read and
# decided. The old->new mapping lives in the migration files, outside the
# registry — relics do not enter the clean system.
ok("rules_import" not in TOOL_NAMES, "rules_import is no longer a tool")
ok("import_rules" not in METHODS, "import_rules is no longer a method of the engine")
_MOD_CONSTS = {t.id for n in ENGINE_TREE.body if isinstance(n, ast.Assign)
               for t in n.targets if isinstance(t, ast.Name)}
ok("MAX_IMPORT" not in _MOD_CONSTS, "MAX_IMPORT is no longer declared")
ok("RE_BARE_LEGACY" not in _MOD_CONSTS and "RE_LEGACY" not in _MOD_CONSTS,
   "the legacy parser and validator are gone")
if _PROPOSE is not None:
    _pp = [a.arg for a in _PROPOSE.args.posonlyargs + _PROPOSE.args.args]
    ok("legacy_id" not in _pp, "rules_propose no longer takes legacy_id", _pp)
if _ENGINE_PROPOSE is not None:
    _ep = [a.arg for a in _ENGINE_PROPOSE.args.posonlyargs + _ENGINE_PROPOSE.args.args]
    ok("legacy_id" not in _ep, "the engine's propose() agrees: no legacy_id", _ep)
ok("ux_rules_legacy" not in _rules.INDEXES and "ux_rules_legacy" not in _rules.SCHEMA,
   "the legacy unique index is out of the schema and out of INDEXES")

print("\n== the supersede is a field, and its uniqueness is an index ==")

# F6: `supersedes` is a dedicated parameter — never a citation in the body —
# so the registry can impose atomicity, and "retired pointing at the
# successor" is born from the schema, not from prose.
if _PROPOSE is not None:
    _ps = [a.arg for a in _PROPOSE.args.posonlyargs + _PROPOSE.args.args]
    ok("supersedes" in _ps, "rules_propose takes supersedes", _ps)
if _ENGINE_PROPOSE is not None:
    _es = [a.arg for a in _ENGINE_PROPOSE.args.posonlyargs + _ENGINE_PROPOSE.args.args]
    ok("supersedes" in _es, "the engine's propose() agrees: supersedes is a field", _es)
ok("ux_rule_supersedes" in _rules.INDEXES,
   "the one-pending-heir guarantee is an INDEX the preflight verifies",
   _rules.INDEXES)
ok(GUIDE_SRC.count("`supersedes`") >= 1,
   "the manual documents the supersede field")

print("\n== the brief leads the list, and is versioned like everything else ==")

# F1: the mandate that used to live in a role's memory file. One round trip —
# who you are, then what binds you — and the history IS the protection.
for _t in TOOLS:
    if _t.name == "rules_list":
        ok("brief" in (ast.get_docstring(_t) or ""),
           "rules_list promises the brief at the head of the answer")
    if _t.name == "rules_consumers_add":
        ok("brief" in (ast.get_docstring(_t) or ""),
           "rules_consumers_add documents the brief it writes")
# Singular since v4.0.0, and not a matter of taste: a table is a row, and the
# name says what one row is. All of them, so a table that stops being declared
# stops being counted by the preflight — which is how a guarantee goes missing
# without a line of the schema changing.
_VERSIONED = ("project_profile_version", "domain_version", "consumer_version",
              "consumer_group_version", "rule_version", "task_version")
ok(set(_VERSIONED) <= set(_rules.TABLES)
   and any(t.startswith("trg_consumer") for t in _rules.TRIGGERS),
   "every versions table and its triggers are declared, so the preflight sees them",
   sorted(set(_VERSIONED) - set(_rules.TABLES)))
ok(GUIDE_SRC.count("your **brief**") == 1,
   "the manual pins the brief at the head of the list, exactly once",
   GUIDE_SRC.count("your **brief**"))

print("\n== proposed_by is a door, and the queue has a ceiling ==")

# F4: the owner's reading rhythm as a number that refuses, moved out of the
# dead AM domain and into the tool, where a machine-checkable constraint
# belongs. Born optional with a working default in the code, because Unraid
# does not propagate new variables to installed containers.
# The ceiling stopped being a variable of the CONTAINER in v4.0.0: this image
# is multi-tenant, so a number set once for the box was a number set for
# somebody else's project. It is `queue_cap`, policy of the project, read from
# the row — and the two constants that used to carry it are pinned GONE, which
# is the half a rename leaves behind.
ok("queue_cap" in METHODS, "the ceiling is a method of the project, read per project")
for _dead in ("DEFAULT_PENDING_CAP", "PENDING_CAP", "WEB_ACTION_CAP"):
    ok(getattr(_rules, _dead, None) is None,
       f"and the engine no longer carries {_dead}", getattr(_rules, _dead, None))
for _t in TOOLS:
    if _t.name == "rules_propose":
        _doc = ast.get_docstring(_t) or ""
        ok("`proposed_by` is REQUIRED" in _doc,
           "rules_propose says proposed_by is required")
        ok("queue" in _doc and "reference code" in _doc,
           "and says what the queue costs, and what opens it")

print("\n== the renewal reads the why, and the lists carry the legend ==")

# F5 and F7: the reason where the deciding happens, the glosses where the IDs
# are listed in bulk.
# `rules_renew` and `rules_pending` are not tools any more — renewing is the
# page's and the queue is `rules_list(pending=True)` — so what is pinned is
# where their promises WENT, on the tools that are left. A check kept pointing
# at a tool that no longer exists is a check that passes by never running.
ok(not ({"rules_renew", "rules_pending"} & TOOL_NAMES),
   "renewing and the pending queue left the surface, and stayed gone",
   sorted({"rules_renew", "rules_pending"} & TOOL_NAMES))
for _t in TOOLS:
    _doc = ast.get_docstring(_t) or ""
    if _t.name == "rules_list":
        ok("legend" in _doc, "rules_list promises the domain legend")
        ok("pending=True" in _doc and "reason" in _doc,
           "and that the queue it now carries comes with the reasons")
    if _t.name == "project_status":
        ok("expiring" in _doc and "reason" in _doc,
           "project_status says the expiring rules carry their reason")
ok(GUIDE_SRC.count("legend of the domains present") == 1,
   "the manual pins the legend, exactly once",
   GUIDE_SRC.count("legend of the domains present"))

print("\n== a verdict's note is spoken surface, and goes stale like a docstring ==")

# `rules_batch` shipped 3.0.0 telling every caller to "pass this digest to
# approve" — a call that had just left the surface. Nothing caught it: the
# check that refuses a docstring naming a tool that is gone looks for the
# NAME, and that sentence named no tool. A dry run went looking for the tool
# and found the hole instead.
#
# Two checks, and they cover different halves. The general one: the RUNTIME
# strings of the engine are held against the tool list exactly as the
# docstrings are — a note that names a tool which no longer exists now fails
# here. The specific one: the sentence that was wrong is pinned gone, and the
# note is required to say where approval actually happens, because the general
# check cannot see a sentence that names nothing.
_ENGINE_DOCNODES = set()
for _n in ast.walk(ENGINE_TREE):
    if isinstance(_n, (ast.Module, ast.ClassDef, ast.FunctionDef)) and _n.body:
        _s = _n.body[0]
        if isinstance(_s, ast.Expr) and isinstance(_s.value, ast.Constant):
            _ENGINE_DOCNODES.add(id(_s.value))
_NOTE_NAMES = {}
for _n in ast.walk(ENGINE_TREE):
    if (isinstance(_n, ast.Constant) and isinstance(_n.value, str)
            and id(_n) not in _ENGINE_DOCNODES):
        for _m in NAME_IN_PROSE.findall(_n.value):
            _NOTE_NAMES.setdefault(_m, _n.lineno)
_DEAD = sorted(n for n in _NOTE_NAMES if n not in TOOL_NAMES and n not in NOT_TOOLS)
ok(not _DEAD,
   f"every tool a runtime string in rules.py names is still one "
   f"({len(_NOTE_NAMES)} named)",
   ", ".join(f"{n} (line {_NOTE_NAMES[n]})" for n in _DEAD))

_BATCH = METHODS.get("batch")
# Unparsed, so COMMENTS are out of it: the comment above the note quotes the
# dead sentence to explain what went wrong, and a check that matched the
# explanation would go red on the very paragraph that stops it recurring.
_BATCH_SRC = ast.unparse(_BATCH) if _BATCH is not None else ""
ok("pass this digest to approve" not in _BATCH_SRC,
   "the batch note no longer describes a call that does not exist")
ok("page" in _BATCH_SRC.lower(),
   "and it says where the approval actually happens, in the browser")

print("\n== the sanitisation runs before the database, in every writing method ==")

# THE ORDER IS THE GUARANTEE. The sanitisation corrects nothing and stores
# nothing — it refuses — so it must also SPEND nothing: a refusal that had
# already drawn a number would make a typo permanent, because IDs are never
# reused, and one that had already taken a slot in the pending queue would let
# a bad reference cost a good proposal.
#
# The behaviour is measured in collaudo. What is pinned HERE is the order in
# the source, because that is the thing a refactor moves without meaning to:
# in every method that both sanitises and writes, the LAST sanitising call
# must come before the FIRST write and before the counter is read.
_SANITISERS = {"self._prose", "self._cites", "self._relics", "self._task_prose"}
_COUNTERS = {"self._next_seq", "self._next_task_seq"}
_WRITE_SQL = re.compile(r"\b(BEGIN|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)


def _first_write_line(fn):
    lines = []
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        called = ast.unparse(n.func)
        if called in _COUNTERS:
            lines.append(n.lineno)
        elif called == "self.cx.execute" and n.args:
            sql = ast.unparse(n.args[0])
            if _WRITE_SQL.search(sql):
                lines.append(n.lineno)
    return min(lines) if lines else None


# Line order alone stopped being the right instrument in v4.0.0, and both ways
# it was wrong are the same mistake — reading a program as if it ran top to
# bottom:
#
#   · the four `_amend_*` handlers are FOUR gestures in one function, one per
#     action, and the create branch writes above the line where the amend
#     branch sanitises. Nothing is wrong there and a line comparison says there
#     is;
#   · a sanitiser called INSIDE the write's own arguments —
#     `execute(sql, (self._prose(...),))` — has a line number after the write
#     and is evaluated before it. That is a stronger guarantee than order, not
#     a weaker one.
#
# So the comparison is made pairwise, and only between a sanitiser and a write
# that can actually run one after the other: not nested one in the other, and
# not on opposite sides of the same `if`.
def _branch_path(node, root):
    """Which branch of which `if` a node sits in, outermost first."""
    path, stack = [], [(root, ())]
    while stack:
        cur, here = stack.pop()
        for field, value in ast.iter_fields(cur):
            items = value if isinstance(value, list) else [value]
            for item in items:
                if not isinstance(item, ast.AST):
                    continue
                nxt = here + ((id(cur), field),) if isinstance(cur, ast.If) else here
                if item is node:
                    path = list(nxt)
                    stack = []
                    break
                stack.append((item, nxt))
            if not stack:
                break
    return path


def _exclusive(a, b, root):
    """True when a and b cannot both run on one call.

    Two shapes, and the second is the one the handlers are written in: opposite
    sides of the same `if`, and — the guard clause — one inside an `if` body
    that always RETURNS or RAISES while the other is outside it. `if action ==
    'create': ... return` followed by the amend branch is four gestures in one
    function, not one gesture written out of order."""
    pa, pb = _branch_path(a, root), _branch_path(b, root)
    for (ia, fa), (ib, fb) in zip(pa, pb):
        if ia == ib and fa != fb:
            return True
        if ia != ib:
            break
    for node in ast.walk(root):
        if not isinstance(node, ast.If):
            continue
        for body in (node.body, node.orelse):
            if not body or not isinstance(body[-1], (ast.Return, ast.Raise)):
                continue
            inside = {id(n) for b in body for n in ast.walk(b)}
            if (id(a) in inside) != (id(b) in inside):
                return True
    return False


_ORDERED = 0
for _m in PROJECT.body:
    if not isinstance(_m, ast.FunctionDef):
        continue
    _sans = [n for n in ast.walk(_m) if isinstance(n, ast.Call)
             and ast.unparse(n.func) in _SANITISERS]
    if not _sans:
        continue
    _writes = [n for n in ast.walk(_m) if isinstance(n, ast.Call)
               and (ast.unparse(n.func) in _COUNTERS
                    or (ast.unparse(n.func) == "self.cx.execute" and n.args
                        and _WRITE_SQL.search(ast.unparse(n.args[0]))))]
    if not _writes:
        continue
    _ORDERED += 1
    _late = []
    for _w in _writes:
        _inside = {id(n) for n in ast.walk(_w)}
        for _s in _sans:
            if id(_s) in _inside or _exclusive(_s, _w, _m):
                continue
            if _s.lineno > _w.lineno:
                _late.append(f"line {_s.lineno} after the write at {_w.lineno}")
    ok(not _late,
       f"Project.{_m.name}: every reference is sanitised before anything is "
       f"written or counted", "; ".join(sorted(set(_late))))
ok(_ORDERED >= 6, f"the order is pinned on {_ORDERED} writing methods", _ORDERED)

# And the sanitisation is ONE definition. Two ideas of what a relic looks like
# is one door that lets them in — which is the shape the defect had before:
# the check existed, on bodies only.
for _name in ("_relics", "_prose", "_task_prose"):
    _defs = [n for n in PROJECT.body
             if isinstance(n, ast.FunctionDef) and n.name == _name]
    ok(len(_defs) == 1, f"Project.{_name} is defined exactly once", len(_defs))
ok(getattr(_rules.Project, "SANITISED", "") == "reference sanitisation failed",
   "the refusal says what failed, in the words the refusal is known by",
   getattr(_rules.Project, "SANITISED", None))
# The canonical form is FOUR digits and the pattern that hunts relics must not
# have a floor: `VE-5` fell under the old one and was never seen.
ok(_rules.RE_ID_SHAPED.search("VE-5") is not None,
   "the relic pattern has no floor: a one-digit ID is caught")
ok(_rules.RE_ID_SHAPED.search("VE-12345") is not None,
   "and no ceiling either: a five-digit ID is caught")
ok(_rules.RE_ID_SHAPED.search("RFC-2119") is None
   and _rules.RE_ID_SHAPED.search("ISO-8601") is None,
   "and a three-letter prefix is not an ID of this project")

print("\n== one icon URL, two files, and a check between them ==")

# The string lives in server.py, which hands it to FastMCP for the consent
# page, and in the Unraid template, which puts it on the container. NOTHING
# links the two, so two hand copies of one URL have an expiry date — this is
# what compares them instead of hoping.
#
# It reads the ASSIGNMENT and then the CALL, because the two failures are
# different in kind: a constant left behind after the argument was dropped
# keeps a string search perfectly happy while the server passes no icon at
# all.
_ICON_ASSIGNS = [n for n in SERVER_TREE.body if isinstance(n, ast.Assign)
                 and any(getattr(t, "id", "") == "ICON_URL" for t in n.targets)]
ok(len(_ICON_ASSIGNS) == 1, "ICON_URL is assigned exactly once, at module level",
   len(_ICON_ASSIGNS))
_ICON_URL = ast.literal_eval(_ICON_ASSIGNS[0].value) if len(_ICON_ASSIGNS) == 1 else None

_ICON_XML = re.search(r"<Icon>\s*(\S+?)\s*</Icon>", TEMPLATE)
ok(_ICON_XML is not None, "the Unraid template still declares an <Icon>")
ok(_ICON_XML is not None and _ICON_URL == _ICON_XML.group(1),
   "the icon of the consent page and the icon of the container are the SAME "
   "url — one image, or the two drift and nobody is told",
   f"{_ICON_URL} vs {_ICON_XML.group(1) if _ICON_XML else None}")

# And the constant has to REACH FastMCP. A name nothing passes is a comment
# with a colon in it.
_FASTMCP_CALL = next((n for n in ast.walk(SERVER_TREE) if isinstance(n, ast.Call)
                      and ast.unparse(n.func) == "FastMCP"), None)
ok(_FASTMCP_CALL is not None, "FastMCP is constructed in server.py")
_ICONS_KW = next((k.value for k in (_FASTMCP_CALL.keywords if _FASTMCP_CALL else [])
                  if k.arg == "icons"), None)
ok(_ICONS_KW is not None,
   "and it is given an `icons` argument — without it the constant above is "
   "decoration and the consent page keeps FastMCP's logo")
_ICONS_SRC = ast.unparse(_ICONS_KW) if _ICONS_KW is not None else ""
ok("ICON_URL" in _ICONS_SRC,
   "and that argument carries ICON_URL, not a second copy of the string",
   _ICONS_SRC[:80] or "(no icons argument)")
# The mimeType is a third statement ABOUT the same file, so it can be wrong on
# its own while both URLs agree.
_EXT_MIME = {".png": "image/png", ".svg": "image/svg+xml",
             ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
_WANT = _EXT_MIME.get(os.path.splitext(_ICON_URL or "")[1].lower())
ok(_WANT is not None and f'"{_WANT}"' in _ICONS_SRC.replace("'", '"'),
   f"the declared mimeType matches the file actually pointed at ({_WANT})",
   _ICONS_SRC[:80])
# And the file is in the repository, under the name the URL asks for. The URL
# serves it from `main`, so a missing file is not an error anywhere: it is a
# raw URL that 404s and a broken image on a page nobody looks at twice. The
# name is DERIVED from the URL and not written here, which is the point — a
# hand copy of the filename is one more thing to keep equal.
ok(bool(_ICON_URL) and os.path.exists(os.path.join(HERE, os.path.basename(_ICON_URL))),
   "the image the URL points at is IN this repository, under that exact name",
   _ICON_URL)

print("\n== the task log: five tools, one of them maintenance ==")

# F3. The log that replaces both the per-role changelog and the "pending"
# sections of the role memories. What is pinned here is the SHAPE of the
# surface — who is gated, what has no default, where the ceilings live —
# because every one of those is a decision that reads as a detail.

# Nine in v3.1.0, FIVE in v4.0.0: search and range folded into `tasks_list`,
# complete and drop into `tasks_close` — one gesture with two verdicts. An
# equality, and the four that went are named below so a resurrection fails by
# its own name rather than by a count nobody reads.
_TASK_TOOLS = sorted(t.name for t in TOOLS if t.name.startswith("tasks_"))
ok(_TASK_TOOLS == ["tasks_add", "tasks_amend", "tasks_close", "tasks_get",
                   "tasks_list", "tasks_overview"],
   "the task log puts exactly six tools on the surface", _TASK_TOOLS)
ok(not ({"tasks_search", "tasks_range", "tasks_complete", "tasks_drop"} & TOOL_NAMES),
   "and the four that folded away stayed away",
   sorted({"tasks_search", "tasks_range", "tasks_complete", "tasks_drop"} & TOOL_NAMES))

# ONE of them is maintenance, and it is the cross-consumer one. An EQUALITY,
# so a gate appearing on a worker's tool fails here as loudly as one going
# missing from the maintainer's: the first would put the architect key in
# every chat of the project, which is what the credential model exists to
# stop.
_TASK_GATED = sorted(t.name for t in TOOLS if t.name.startswith("tasks_")
                     and any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                             and n.func.id == "_admin" for n in ast.walk(t)))
ok(_TASK_GATED == ["tasks_amend", "tasks_close", "tasks_overview"],
   "tasks_overview is gated outright and the two that touch SOMEBODY ELSE's "
   "task conditionally — the rest cost the reference code alone", _TASK_GATED)

# THE CEILINGS ARE NAMED, AND THEY ARE THE ENGINE'S. The spec asked for
# constants at the top of the module rather than literals scattered through
# the queries — and, explicitly, never parameters of the tools: a ceiling a
# caller can raise is not one.
# `GET_IDS` and `GET_BYTES` lost their `TASKS_` prefix in v4.0.0 and are ONE
# pair for rules and tasks: it is the same ceiling on the same client, and two
# constants would be one number written twice.
_TASK_CAPS = ("TASKS_LIST_CAP", "GET_IDS", "GET_BYTES",
              "TASKS_RECENT_DAYS", "TASKS_STALE_DAYS")
ok(getattr(_rules, "MAX_GET_IDS", None) is None
   and getattr(_rules, "TASKS_GET_IDS", None) is None,
   "and the two names they replaced are gone, not shadowing them")
_MODULE_ASSIGNS = {t.id for n in ENGINE_TREE.body if isinstance(n, ast.Assign)
                   for t in n.targets if isinstance(t, ast.Name)}
for _c in _TASK_CAPS:
    ok(_c in _MODULE_ASSIGNS, f"{_c} is a named constant at module level in rules.py")
    ok(RULES_SRC.count(_c) >= 2,
       f"and it is READ, not just declared: {RULES_SRC.count(_c)} mentions")

# And the mentions are not enough on their own — that check stays green while
# the WORKING code goes back to literals, because the constant survives in the
# verdict that reports it. Injected exactly that and the suite did not blink.
# So: inside the task methods, no bare integer may EQUAL a ceiling. A number
# written twice is a number that will disagree with itself.
_CAP_VALUES = {getattr(_rules, c) for c in _TASK_CAPS}
_TASK_METHODS = [n for n in PROJECT.body
                 if isinstance(n, ast.FunctionDef)
                 and ("task" in n.name or n.name == "_order_and_cap")]
ok(len(_TASK_METHODS) >= 12, f"the task methods are found: {len(_TASK_METHODS)}")
for _m in _TASK_METHODS:
    _lits = sorted({n.value for n in ast.walk(_m)
                    if isinstance(n, ast.Constant) and isinstance(n.value, int)
                    and not isinstance(n.value, bool) and n.value in _CAP_VALUES})
    ok(not _lits, f"Project.{_m.name} spells its ceilings by NAME, never as a number",
       f"literals that equal a ceiling: {_lits}")
_PARAMS = {a.arg for t in TOOLS if t.name.startswith("tasks_")
           for a in t.args.posonlyargs + t.args.args + t.args.kwonlyargs}
ok(not (_PARAMS & {"cap", "limit", "max", "ceiling", "n"}),
   "no tasks_ tool takes its own ceiling as a parameter", sorted(_PARAMS))

# The WINDOW moved into `tasks_list` — `since` and `until` — and with it the
# decision `tasks_range` carried: those two are not a filter on one date the
# server picks, they open the window on the CLOSED tasks past the recent ones.
# What is pinned is that both survived the fold, on the tool that absorbed
# them, and that neither grew a default that would answer a question the
# caller did not ask.
_LIST = next((t for t in TOOLS if t.name == "tasks_list"), None)
ok(_LIST is not None, "tasks_list is on the surface")
if _LIST is not None:
    _pos = [a.arg for a in _LIST.args.posonlyargs + _LIST.args.args]
    ok({"since", "until", "query", "authored"} <= set(_pos),
       "and it absorbed the window, the search and the authored view", _pos)
    _doc = ast.get_docstring(_LIST) or ""
    ok("closed" in _doc and ("since" in _doc or "until" in _doc),
       "and says what the window is for: the closed ones past the recent")
_ELIST = METHODS.get("task_list")
ok(_ELIST is not None, "the engine has task_list")
if _ELIST is not None:
    _ep = [a.arg for a in _ELIST.args.posonlyargs + _ELIST.args.args][1:]
    ok({"since", "until", "authored"} <= set(_ep),
       "the engine agrees: one desk, three framings", _ep)
ok("task_range" not in METHODS and "task_search" not in METHODS,
   "and the engine folded them too, rather than keeping a second door open")

# TK is reserved, and the two letter-pair checks that used to be written twice
# are ONE door now. The literal is counted: a second copy is how a reservation
# added to one door stops holding on the other.
ok(getattr(_rules, "RESERVED_DOMAINS", ()) == (getattr(_rules, "TASK_PREFIX", ""),),
   "RESERVED_DOMAINS is exactly the task prefix",
   getattr(_rules, "RESERVED_DOMAINS", None))
ok(RULES_SRC.count(r'r"^[A-Z]{2}$"') == 1,
   "the domain letter-pair is validated in ONE place, so the reservation cannot "
   "hold on one door and not the other", RULES_SRC.count(r'r"^[A-Z]{2}$"'))
_VD = [n for n in ENGINE_TREE.body
       if isinstance(n, ast.FunctionDef) and n.name == "_valid_domain"]
ok(len(_VD) == 1, "rules.py defines `_valid_domain` exactly once, at module level "
                  "— two definitions of what a domain looks like is two "
                  "reservations, and the last one wins", len(_VD))

# The schema objects are DECLARED, which is what makes the preflight see them:
# a table or a trigger that exists in SCHEMA and not in these tuples is one the
# boot would never notice missing.
# Singular in v4.0.0, and `task_counter` is gone with the prune that
# cancelled: the number comes from the version table, so it cannot rewind.
for _t in ("task", "task_version"):
    ok(_t in _rules.TABLES, f"{_t} is declared, so the preflight checks it")
ok("task_counter" not in _rules.TABLES,
   "and the counter table went with the prune that used to need it")
for _g in ("trg_task_ins", "trg_task_upd", "trg_task_closed_is_closed",
           "trg_task_frozen", "trg_task_archive_closed_only"):
    ok(_g in _rules.TRIGGERS, f"{_g} is declared, so the preflight checks it")
ok("ux_task_idem" in _rules.INDEXES,
   "the idempotency guarantee is an INDEX the preflight verifies", _rules.INDEXES)

# What the docstrings must keep promising, because these are the two places a
# caller decides how to behave from the description alone.
for _t in TOOLS:
    _doc = ast.get_docstring(_t) or ""
    if _t.name == "tasks_get":
        ok("refused" in _doc and "truncates and says so" in _doc,
           "tasks_get says the count refuses and the byte ceiling declares")
    if _t.name == "tasks_add":
        ok("signature" in _doc and "created_by" in _doc,
           "tasks_add says created_by is the signature")
        ok("never changes" in _doc,
           "and that urgent belongs to whoever created the task")
    if _t.name == "tasks_close":
        ok("exactly one of the two" in _doc and "outcome" in _doc and "reason" in _doc,
           "tasks_close says one gesture, two verdicts, exactly one of them")
        ok("ADMIN CODE" in _doc and "owner" in _doc,
           "and that somebody else's task takes the admin code")
    if _t.name == "tasks_list":
        ok("urgent first" in _doc and "real total" in _doc,
           "tasks_list promises the order and the declared total")

# THE MANUAL SHIPS WITH THE BEHAVIOUR, and here it also carries a COPY of
# four numbers. A copy needs a check that compares it, or it is just a second
# answer waiting to disagree with the first — a manual with the wrong ceilings
# is worse than one with no ceilings.
_GUIDE_CEILINGS = re.search(r"## THE CEILINGS.*?\n\n(.*?)\n\n", GUIDE_SRC, re.S)
ok(_GUIDE_CEILINGS is not None, "the manual has a ceilings table to read")
if _GUIDE_CEILINGS:
    _tbl = _GUIDE_CEILINGS.group(1)
    for _label, _value in (("items in a task list", _rules.TASKS_LIST_CAP),
                           ("codes per `tasks_get`", _rules.GET_IDS),
                           ("bytes per `tasks_get`", _rules.GET_BYTES),
                           ("body of one task", _rules.MAX_BODY_BYTES)):
        _line = next((l for l in _tbl.splitlines() if _label in l), "")
        ok(str(_value) in _line,
           f"the manual's ceiling for {_label} is {_value}", _line[:80] or "(row absent)")
ok(str(_rules.TASKS_STALE_DAYS) in GUIDE_SRC
   and "MARKED" in GUIDE_SRC and "do not expire" in GUIDE_SRC,
   "the manual says tasks do not expire and gives the staleness threshold")

# And it NAMES every task tool. The witness elsewhere in this file checks the
# other direction — that a name in the prose is still a tool — which stays
# green on a tool the manual never mentioned at all.
# Split by GATE, not lumped together, and that is the "no forward pointer"
# decision made checkable: a working chat's manual must name every tool it can
# call and NONE it cannot — a manual that sends the reader to a tool the reader
# has no key for is a manual that mentions a door it cannot describe.
_ADMIN_SRC = MANUALS.get("reference-guide-admin.md") or ""
_WORK_TASKS = [t for t in _TASK_TOOLS if t not in WITH_KEY or t in ADMIN_IF_KEY]
_ADMIN_TASKS = [t for t in _TASK_TOOLS if t in WITH_KEY and t not in ADMIN_IF_KEY]
_UNDOCUMENTED = sorted(t for t in _WORK_TASKS if t not in GUIDE_SRC)
ok(not _UNDOCUMENTED, "the work manual names every task tool a chat can call",
   _UNDOCUMENTED)
ok(not sorted(t for t in _ADMIN_TASKS if t not in _ADMIN_SRC),
   "and the administration manual names the ones it cannot",
   sorted(t for t in _ADMIN_TASKS if t not in _ADMIN_SRC))
_LEAKED = sorted(t for t in (WITH_KEY - set(ADMIN_IF_KEY)) if t in GUIDE_SRC)
ok(not _LEAKED,
   "and the work manual names no administration tool: no pointer forward to a "
   "door the reader has no key for", _LEAKED)

print("\n== the web layer speaks to the engine, and never to the database ==")

WEB = os.path.join(HERE, "web.py")
WEB_SRC = source_or_none(WEB) or ""
ok(bool(WEB_SRC), "web.py is there to be read at all")
WEB_TREE = parse(WEB) if WEB_SRC else ast.Module(body=[], type_ignores=[])

# THE CONTRACT, and it is the one that keeps a road open rather than the one
# that stops a bug. The layer may call the methods of `Registry` and nothing
# else: a single SELECT in here would tie the UI to this schema, and the day
# the UI becomes a second MCP client — written down as the live alternative,
# not as a fantasy — it would have to be rewritten instead of moved. Nothing
# at runtime would ever complain, which is exactly why it is checked here.
_SQL = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|PRAGMA|BEGIN|COMMIT)\b")
# DOCSTRINGS are subtracted, and that is not a loophole: a docstring is the
# first statement of a module, a class or a function and is never handed to
# sqlite3. Left in, this check goes red on the very paragraph that explains
# the ban — and a check that fails on a legitimate sentence gets deleted
# rather than obeyed, which this file has already paid for twice.
_DOCS = {id(ast.get_docstring(n, clean=False)) for n in ast.walk(WEB_TREE)
         if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
                           ast.AsyncFunctionDef))}
_DOCNODES = set()
for _n in ast.walk(WEB_TREE):
    if isinstance(_n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        if (_n.body and isinstance(_n.body[0], ast.Expr)
                and isinstance(_n.body[0].value, ast.Constant)
                and isinstance(_n.body[0].value.value, str)):
            _DOCNODES.add(id(_n.body[0].value))
_SQLY = sorted({n.value[:40] for n in ast.walk(WEB_TREE)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in _DOCNODES and _SQL.search(n.value)})
ok(not _SQLY, "web.py contains no SQL: the contract towards the engine is "
              "Registry's methods and nothing else", _SQLY)
_WEB_IMPORTS = set()
for _n in ast.walk(WEB_TREE):
    if isinstance(_n, ast.Import):
        _WEB_IMPORTS |= {a.name.split(".")[0] for a in _n.names}
    elif isinstance(_n, ast.ImportFrom) and _n.module:
        _WEB_IMPORTS.add(_n.module.split(".")[0])
ok("sqlite3" not in _WEB_IMPORTS and "rules" not in _WEB_IMPORTS,
   "and it does not import sqlite3 or reach into rules.py behind the engine",
   sorted(_WEB_IMPORTS))
# And starlette is imported INSIDE build(), never at module level: the
# preflight imports this file for the port and the publishable ports, and it
# has to keep running on an image where the web stack is broken.
ok("starlette" not in {a.name.split(".")[0] for n in WEB_TREE.body
                       if isinstance(n, ast.Import) for a in n.names}
   | {n.module.split(".")[0] for n in WEB_TREE.body
      if isinstance(n, ast.ImportFrom) and n.module},
   "and starlette is not imported at module level — the preflight reads this file")

print("\n== the master, the session, and the form the browser can fill ==")

_BUILD = next((n for n in WEB_TREE.body if isinstance(n, ast.FunctionDef)
               and n.name == "build"), None)
ok(_BUILD is not None, "web.py defines build()")
if _BUILD is not None:
    _BARGS = [a.arg for a in _BUILD.args.posonlyargs + _BUILD.args.args
              + _BUILD.args.kwonlyargs]
    ok("master" in _BARGS,
       "and it is HANDED the master: a web layer that read the environment "
       "itself would be a second place the configuration is decided", _BARGS)
    ok("log" in _BARGS,
       "and the service's own logger, or a refusal stops appearing in the log "
       "everybody reads", _BARGS)

# The comparison on the master is constant-time, like _admin's. `==` on a
# secret is a different defect, and it is one nothing at runtime reports.
_CMPS = [n for n in ast.walk(WEB_TREE) if isinstance(n, ast.Call)
         and ast.unparse(n.func) == "secrets.compare_digest"]
ok(len(_CMPS) >= 1, "the master is compared with secrets.compare_digest", len(_CMPS))
ok(not [n for n in ast.walk(WEB_TREE) if isinstance(n, ast.Compare)
        and any(isinstance(o, (ast.Eq, ast.NotEq)) for o in n.ops)
        and "master" in ast.unparse(n).lower()],
   "and never with == or !=",
   [ast.unparse(n)[:50] for n in ast.walk(WEB_TREE) if isinstance(n, ast.Compare)
    and any(isinstance(o, (ast.Eq, ast.NotEq)) for o in n.ops)
    and "master" in ast.unparse(n).lower()])

# The session secret is generated AT BOOT and lives nowhere else. Read from
# the environment it would survive a restart, which is the property this
# design deliberately does not want: a reboot invalidates every session, and
# the cost is typing a password once.
_TOKENS = [n for n in ast.walk(WEB_TREE) if isinstance(n, ast.Call)
           and ast.unparse(n.func).startswith("secrets.token_")]
ok(bool(_TOKENS), "the session secret is generated with secrets.token_*",
   [ast.unparse(n) for n in _TOKENS])
ok(_BUILD is not None and any(t in ast.walk(_BUILD) for t in _TOKENS),
   "and inside build(), so a restart invalidates every session")
ok(not [n for n in ast.walk(WEB_TREE) if isinstance(n, ast.Call)
        and ast.unparse(n.func) in ("os.environ.get", "os.getenv")
        and n.args and isinstance(n.args[0], ast.Constant)
        and "SECRET" in str(n.args[0].value).upper()],
   "and it is never read from the environment")

# The cookie is signed, and the signature is checked with a constant-time
# comparison too — a forged cookie is the whole of the session.
ok(any(ast.unparse(n.func).startswith("hmac.") for n in ast.walk(WEB_TREE)
       if isinstance(n, ast.Call)),
   "the session cookie is signed with hmac")

# One hour of INACTIVITY, and the number is a named constant so the check and
# the code cannot say different things.
ok(getattr(__import__("web"), "SESSION_MAX_IDLE", None) == 3600,
   "the session expires after one hour of inactivity",
   getattr(__import__("web"), "SESSION_MAX_IDLE", None))

# A form with the password alone is the case where automatic filling goes
# wrong, and goes wrong in SILENCE — 1Password and the Apple keychain both
# want a username field to key the entry on. It is hidden and constant: there
# is only one user.
ok('autocomplete="username"' in WEB_SRC,
   "the login form carries a hidden username field, for the password managers")
ok('autocomplete="current-password"' in WEB_SRC,
   "and marks the password as the current one")

# Every value that reaches a page goes through html.escape. There is no
# template engine here — that was the decision — so the escaping is the whole
# defence, and it has to be visible.
ok("html.escape" in WEB_SRC, "values reaching a page go through html.escape")

# A refused login leaves ONE line, at WARNING. Not a traceback: a page that
# answers a wrong password with a stack trace is a page that teaches the
# person nothing and the log everything. WARNING and not INFO, unlike a wrong
# admin code: that one can only come from one of Alfredo's own chats, this one
# can come from anything on the LAN, which is the actor the master exists for
# — and WARNING is the level that survives LOG_LEVEL=WARNING, which is the
# setting this line most needs to survive.
_WEBLOGS = [ast.unparse(n.func) for n in ast.walk(WEB_TREE) if isinstance(n, ast.Call)
            and ast.unparse(n.func).startswith("log.")]
ok(bool(_WEBLOGS), "web.py logs through the logger it was handed", _WEBLOGS)
ok("log.warning" in _WEBLOGS, "and a refused login is a WARNING line", _WEBLOGS)
ok("log.exception" not in _WEBLOGS and "log.error" not in _WEBLOGS,
   "and a wrong password is not an error: no traceback", _WEBLOGS)

print("\n== the UI is refused at the edge, not at the first click ==")

# The `web` check is BLOCKING like every other one, and it is the only place
# two mistakes get caught before they are made: a master still on its
# placeholder, which is an open door to the approval page, and a UI on one of
# the three ports the Funnel CAN publish, which turns a page on the LAN into a
# page on the internet. Both are invisible at boot and both surface as
# something else entirely — the first as "somebody approved rules I did not",
# the second never.
_WEB_CHECK = [n for n in _PF_TREE.body
              if isinstance(n, ast.FunctionDef)
              and any(isinstance(d, ast.Call) and ast.unparse(d.func) == "check"
                      and d.args and getattr(d.args[0], "value", None) == "web"
                      for d in n.decorator_list)]
ok(len(_WEB_CHECK) == 1, "preflight.py declares a `web` check, exactly once",
   len(_WEB_CHECK))
# A check that is defined and not listed never runs, and nothing says so: the
# sheet comes up one line shorter and one line shorter is not a thing anybody
# counts.
ok(_CHECKS_LIST is not None and "c_web" in _CHECKS_LIST.group(1),
   "and it is in CHECKS — one that is defined but not listed never runs")
# It must resolve the port through the same expression the service uses. Two
# expressions that agree today are two expressions, and this one decides
# whether the page is publishable.
if _WEB_CHECK:
    _WSRC = ast.unparse(_WEB_CHECK[0])
    ok("web.port_from_env" in _WSRC,
       "and it resolves WEB_PORT through web.port_from_env, not a second time")
    ok("web.FUNNEL_PORTS" in _WSRC,
       "and the three publishable ports are the engine's constant, not a literal list")
    ok("WEB_UI_PASSWORD" in _WSRC and "is_placeholder" in _WSRC,
       "and it refuses a password that is missing or still a placeholder")
_PF_READS_PORT = [n for n in ast.walk(_PF_TREE)
                  if isinstance(n, ast.Call)
                  and ast.unparse(n.func) in ("os.environ.get", "os.getenv")
                  and n.args and isinstance(n.args[0], ast.Constant)
                  and n.args[0].value == "WEB_PORT"]
ok(not _PF_READS_PORT,
   "preflight.py does not read WEB_PORT on its own — it comes from web.py",
   [ast.unparse(n) for n in _PF_READS_PORT])

print("\n== web.py -> rules.py: every call lands, and on the right class ==")

# The SECOND seam, and it is the same class of defect as the first: a renamed
# parameter between these two files goes unnoticed until somebody clicks. The
# engine's own suites cannot see it — they call the engine directly — and
# nothing in the browser would report it as anything but a 500.
#
# Since v4.0.0 the seam has TWO sides, and telling them apart is the whole of
# this section: `registry` is the ROUTER — one method to list what is served,
# one to open a project by name — and everything else happens on a PROJECT.
# Which class a call belongs to is not guessable from the method name, so the
# file carries a convention instead: the handle a door hands back is always
# called `prj`. That is checked here first, because every check below rests on
# it — a handle under any other name would be an engine call nobody can
# attribute to a class, which is the defect this section exists to catch.
WEB_DOOR = "_open"

_HANDLES = []
for _a in ast.walk(WEB_TREE):
    if not isinstance(_a, ast.Assign):
        continue
    if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
           and n.func.id == WEB_DOOR for n in ast.walk(_a.value)):
        _HANDLES += [t.id for t in _a.targets if isinstance(t, ast.Name)]
ok(len(_HANDLES) >= 3,
   f"web.py opens projects through {WEB_DOOR}(): {len(_HANDLES)} bindings")
ok(set(_HANDLES) == {"prj"},
   "and every one of them is bound to the name `prj` — one name for the handle, "
   "so a call on it can be attributed to a class", sorted(set(_HANDLES)))
# And the door itself is the router's, not a second way in: a `_open` that
# built a Project by hand would leave the registry's re-read out of the loop.
_DOOR_FN = [n for n in ast.walk(WEB_TREE)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == WEB_DOOR]
ok(len(_DOOR_FN) == 1, f"web.py defines {WEB_DOOR}() exactly once", len(_DOOR_FN))
if _DOOR_FN:
    ok("registry.by_name" in ast.unparse(_DOOR_FN[0]),
       "and it goes through registry.by_name(): the router re-reads its file, "
       "so a project served a minute ago is not served from a closure")

# THE FAULT IS CAUGHT FIRST, EVERYWHERE. RulesFault is a SUBCLASS of
# RulesError, so a single `except refusal` swallows both — and a registry that
# does not parse, or a database from another generation, comes back to the
# person as `no project called that`, which is the sentence for a typo. Every
# handler that catches here has to name the fault first and re-raise it, in the
# same order make_tool uses on the MCP side. Counted, because a block whose
# receiver is spelt differently would go green on a file that catches nothing.
_CATCHES = [n for n in ast.walk(WEB_TREE) if isinstance(n, ast.Try)
            and any(isinstance(h.type, ast.Name) and h.type.id == "refusal"
                    for h in n.handlers)]
ok(len(_CATCHES) >= 5, f"{len(_CATCHES)} places in web.py catch a refusal")
for _t in _CATCHES:
    _names = [h.type.id for h in _t.handlers if isinstance(h.type, ast.Name)]
    _first = _t.handlers[0]
    ok(_names[:2] == ["fault", "refusal"]
       and any(isinstance(x, ast.Raise) for x in _first.body),
       f"web.py line {_t.lineno}: the fault is caught FIRST and re-raised — "
       f"RulesFault is a RulesError, and a broken registry must not read as a "
       f"missing project", _names)
# And build() is HANDED both classes: deducing either one here would be this
# file deciding what the engine means by a fault.
_BUILD_ARGS = {a.arg for a in (_BUILD.args.posonlyargs + _BUILD.args.args
                               + _BUILD.args.kwonlyargs)} if _BUILD else set()
ok({"refusal", "fault"} <= _BUILD_ARGS,
   "and build() is handed both classes, never guessing which is which",
   sorted(_BUILD_ARGS))
_BUILD_CALL = [n for n in ast.walk(SERVER_TREE) if isinstance(n, ast.Call)
               and ast.unparse(n.func) == "web.build"]
ok(bool(_BUILD_CALL) and {k.arg for k in _BUILD_CALL[0].keywords} >= {"refusal", "fault"},
   "and server.py hands them over, both",
   sorted(k.arg for k in _BUILD_CALL[0].keywords) if _BUILD_CALL else "no call")


def _seam(calls, table, label):
    """Every call in `calls` exists in `table` with a compatible signature."""
    for call in calls:
        name = call.func.attr
        where = f"web.py line {call.lineno}"
        if name not in table:
            ok(False, f"{label}.{name} exists", where)
            continue
        pos, kwonly, required = signature(table[name])
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
        ok(not problems, f"web.py: {label}.{name}(...) matches its signature",
           f"{where}: {'; '.join(problems)}")


def _calls_on(name: str) -> list:
    return [n for n in ast.walk(WEB_TREE)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name) and n.func.value.id == name]


ROUTER_WEB, PROJECT_WEB = _calls_on("registry"), _calls_on("prj")
ok(len(ROUTER_WEB) >= 2, f"{len(ROUTER_WEB)} calls into the router from web.py")
ok(len(PROJECT_WEB) >= 10, f"{len(PROJECT_WEB)} calls into a project from web.py")
_seam(ROUTER_WEB, ROUTES, "registry")
_seam(PROJECT_WEB, METHODS, "prj")

# THE DEAD, BY NAME, on this side too. The equality of the live above cannot
# see a resurrection that lands on a method the engine still has under another
# class, and these seven are the ones this page used to call: five that the
# declarative registry took away with the deployment page, and two that folded
# into calls the engine now answers in one.
WEB_DEAD = {"create_project", "rekey_project", "approve", "deny", "pending",
            "history", "compare"}
_back = {c.func.attr for c in ROUTER_WEB + PROJECT_WEB} & WEB_DEAD
ok(not _back, "and not one of the methods that went is called again", sorted(_back))

print("\n== the lot page: what you saw, what you ticked, and one master ==")

_WEB_FUNCS = {n.name: n for n in ast.walk(WEB_TREE)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

# The DIGEST rides in the page as a hidden field and comes back with the form.
# It covers what was SEEN, not what was ticked — same contract as
# rules_approve, on purpose: the page does not invent a contract of its own,
# it reuses the one belonging to the tool it will replace.
# Either quoting: the pages are f-strings delimited by double quotes, so the
# attributes inside them are written with single ones. A check that only knows
# one of the two forms is a check that fails on correct code, and this file has
# already learnt what happens to those.
ok(re.search(r"type=[\"']hidden[\"'][^>]*name=[\"']digest[\"']"
             r"|name=[\"']digest[\"'][^>]*type=[\"']hidden[\"']", WEB_SRC) is not None,
   "the digest travels as a hidden field of the form")

# ONE TURN, ONE CALL. The page used to deny the unticked and then approve the
# rest — two writes, in an order that mattered — and v4.0.0 folded them into
# `decide()`, which records the yes and the no as a single decision. What is
# pinned here is that the page did not keep half of the old dance: a second
# engine call in this handler would be a write outside the decision, and the
# corpus would hold a verdict the decision does not name.
_ACT = _WEB_FUNCS.get("batch_action")
ok(_ACT is not None, "web.py defines the lot page's action")
if _ACT is not None:
    _seq = [n.func.attr for n in sorted(
        (n for n in ast.walk(_ACT)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
         and isinstance(n.func.value, ast.Name) and n.func.value.id == "prj"),
        key=lambda n: (n.lineno, n.col_offset))]
    ok(_seq == ["decide"],
       "and the lot is decided in exactly ONE call: approving and denying are "
       "the same gesture, so a page that made two of them could record half of "
       "what happened", _seq)

# The CEILING has no home in this file any anymore, and that is the check: it is
# `queue_cap`, policy of the PROJECT, asked of the project the page already
# holds. A default here would be a container-wide opinion in a multi-tenant
# container — and the two knobs that used to carry it, which this page's own
# contract forced to be equal, are gone from the template with it.
_WEBMOD_CAP = __import__("web")
ok(getattr(_WEBMOD_CAP, "DEFAULT_ACTION_CAP", None) is None
   and not hasattr(_WEBMOD_CAP, "action_cap_from_env"),
   "web.py declares no ceiling of its own: the ceiling belongs to the project")
_CAP_ENV = sorted({n.args[0].value for n in ast.walk(WEB_TREE)
                   if isinstance(n, ast.Call)
                   and ast.unparse(n.func) in ("os.environ.get", "os.getenv")
                   and n.args and isinstance(n.args[0], ast.Constant)
                   and n.args[0].value in ("PENDING_CAP", "WEB_ACTION_CAP")}
                  | {n.args[0].value for n in ast.walk(_PF_TREE)
                     if isinstance(n, ast.Call)
                     and ast.unparse(n.func) in ("os.environ.get", "os.getenv")
                     and n.args and isinstance(n.args[0], ast.Constant)
                     and n.args[0].value in ("PENDING_CAP", "WEB_ACTION_CAP")})
ok(not _CAP_ENV,
   "and neither it nor the preflight reads a ceiling from the environment: "
   "those two knobs left the template when they became one number", _CAP_ENV)
_CAP_FN = _WEB_FUNCS.get("_cap")
ok(_CAP_FN is not None and "prj.queue_cap()" in ast.unparse(_CAP_FN),
   "and the one expression that resolves it asks the project",
   ast.unparse(_CAP_FN)[:80] if _CAP_FN is not None else "absent")

print("\n== the consultation reads, and only reads ==")

# The four views the spec asks for, each named by the method that serves it.
# Naming the METHOD and not the route is the point: a page that quietly built
# its own answer instead of asking the engine would be a second reading of the
# corpus, and two readings of a corpus disagree.
for _m, _what in (("project_info", "the living structure of a project"),
                  ("list_rules", "the rules in force for a consumer"),
                  ("get_rules", "a rule with its dated history"),
                  ("batch", "the lot as it is now"),
                  ("decide", "one turn of the lot page"),
                  ("renew", "another term for a rule about to expire"),
                  ("promote", "a rule made permanent"),
                  ("status", "the state of the project, the retired included"),
                  ("queue_cap", "the ceiling on one action"),
                  ("mint_auth_code", "a one-time code, minted"),
                  ("auth_codes", "the live codes and the spent ones"),
                  ("backup", "a quiescent copy of one project")):
    ok(any(n.func.attr == _m for n in PROJECT_WEB),
       f"the UI serves {_what} from prj.{_m}()")
ok(any(n.func.attr == "projects" for n in ROUTER_WEB),
   "and the menu of what is served from registry.projects()")

# The brief LEADS the list: that is the shape rules_list promises, and a page
# that dropped it would be showing a consumer something different from what
# its chat reads. The legend of the domains lives on the project page now,
# where `project_info` puts it — with the gloss and the count, not as a bare
# mapping the page has to know how to read.
ok("brief" in WEB_SRC, "and the rules page carries the brief, as rules_list does")
ok("description" in WEB_SRC, "and the gloss of each live domain")

# Every route is GET except the three that act, and those three are the whole
# of what this UI writes. A read page that answered POST would be a door
# nobody counted — the derived check further down proves no ENGINE write hides
# behind one, this one proves the surface itself is what it looks like.
_ROUTES = []
for _n in ast.walk(WEB_TREE):
    if (isinstance(_n, ast.Call) and isinstance(_n.func, ast.Name)
            and _n.func.id == "Route"):
        _path = _n.args[0].value if _n.args and isinstance(_n.args[0], ast.Constant) else "?"
        _meth = tuple(sorted(k.value for kw in _n.keywords if kw.arg == "methods"
                             for k in kw.value.elts))
        _ROUTES.append((_path, _meth, ast.unparse(_n.args[1]) if len(_n.args) > 1 else "?"))
ok(bool(_ROUTES), f"web.py declares its routes explicitly: {len(_ROUTES)}")
_POSTS = sorted(r for r in _ROUTES if "POST" in r[1])
ok([(r[0], r[2]) for r in _POSTS] == [("/login", "login"), ("/logout", "logout"),
                                      ("/p/{project}/backup", "project_backup"),
                                      ("/p/{project}/batch", "batch_action"),
                                      ("/p/{project}/codes", "codes_mint"),
                                      ("/p/{project}/renewals", "renewals_action")],
   "and exactly six of them take POST: the door, the exit, and the four "
   "gestures — the lot, renewal/promotion, minting a one-time code, and the "
   "backup, which asks for no master because it handles no secret. Creating a "
   "project and rekeying it are NOT here: a project is a line in a file now",
   [(r[0], r[2]) for r in _POSTS])
# Every writing route is UNDER a project, and that is the shape of "a project
# is a database": a gesture that named no project would be a gesture on all of
# them, and this container is multi-tenant.
ok(all(r[0].startswith("/p/{project}/") for r in _POSTS
       if r[2] not in ("login", "logout")),
   "and every gesture names the project it acts on, in its path",
   [r[0] for r in _POSTS])
ok(all(r[1] in (("GET",), ("POST",)) for r in _ROUTES),
   "and no route answers both — a page that reads and writes at one address is "
   "a page whose method is the only thing between the two",
   [r for r in _ROUTES if r[1] not in (("GET",), ("POST",))])

print("\n== every page is behind the session, every write behind the master too ==")

# The same law as `_admin` on the MCP side, moved to the door a PERSON comes
# through — and it needs both halves, because they fail differently. Without
# the session anybody on the LAN reads the corpus; without the master retyped,
# a browser left open on the iPad approves rules by being borrowed. The set of
# writing methods is DERIVED from rules.py, as it is for the tools: a list
# copied into a second file drifts, and this one would drift towards
# "unguarded".
_BUILD_FUNCS = {n.name: n for n in ast.walk(_BUILD or ast.Module(body=[], type_ignores=[]))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _local_calls(fn) -> set[str]:
    return {n.func.id for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def _reaches(start: str, target: str) -> bool:
    """Transitively, through the helpers defined inside build(). `_read_page`
    holds the session check for four pages at once — written once precisely so
    that adding a page cannot add a page that forgot it — so a check that only
    looked one level deep would call all four of them unguarded."""
    seen, stack = set(), [start]
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in _BUILD_FUNCS:
            continue
        seen.add(cur)
        calls = _local_calls(_BUILD_FUNCS[cur])
        if target in calls:
            return True
        stack.extend(calls)
    return False


# The three that are allowed not to ask: login IS the door, logout takes
# nothing away from anybody, and both are named here rather than inferred so
# that a fourth cannot join them by accident.
NO_SESSION_ON_PURPOSE = {
    "login": "it is the door: asking for a session to get one is a locked room",
    "logout": "throwing a cookie away can harm nobody, and refusing to do it "
              "for want of a valid session would leave a stale one in place",
}
_ENDPOINTS = [r[2] for r in _ROUTES]
ok(bool(_ENDPOINTS), "the routes name their endpoints")
for _e in _ENDPOINTS:
    if _e in NO_SESSION_ON_PURPOSE:
        ok(not _reaches(_e, "_session_ok"),
           f"{_e} asks for no session ON PURPOSE — {NO_SESSION_ON_PURPOSE[_e][:52]}...",
           "it now checks one: if that is the new decision, drop the exception")
        continue
    ok(_reaches(_e, "_session_ok"),
       f"{_e} is behind the session")
ok(set(NO_SESSION_ON_PURPOSE) <= set(_ENDPOINTS),
   "every documented exception names an endpoint that exists",
   sorted(set(NO_SESSION_ON_PURPOSE) - set(_ENDPOINTS)))

# And the writing ones behind the master as well, retyped for the action.
#
# ⚠ The receiver is `prj` and not `registry`, and that one word is the whole
# check: since v4.0.0 every write happens on a Project, so a set derived from
# calls on `registry` would have come back EMPTY — no handler writing, no
# handler to guard, and this whole block green with nothing to say. A check
# that stops counting what it watches is the one kind that fails silently.
def _engine_reached(fn) -> set[str]:
    return {n.func.attr for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "prj"}


_GUARDED = 0
for _name, _fn in _BUILD_FUNCS.items():
    _writes = _engine_reached(_fn) & MUTATING
    if not _writes:
        continue
    _GUARDED += 1
    ok(_reaches(_name, "_session_ok"),
       f"{_name} writes ({', '.join(sorted(_writes))}) and is behind the session")
    ok(any(ast.unparse(n.func) == "secrets.compare_digest" for n in ast.walk(_fn)
           if isinstance(n, ast.Call)),
       f"{_name} writes and retypes the master — a session alone is a browser "
       f"left open on the iPad")
# And the block COUNTS what it guarded. Without this line the loop above is
# green on a file where nothing writes at all, which is exactly what it looked
# like the moment the writes moved from one class to another.
ok(_GUARDED >= 3,
   f"{_GUARDED} writing handlers found and guarded: the lot, the renewals and "
   f"the minting", _GUARDED)

# And the mirror image, because the interesting half of a rule is its
# exceptions. `backup` is NOT in MUTATING — VACUUM INTO produces a file and
# changes nothing in the database — so the loop above never looks at the
# handler that runs it, and dropping the master from it left no red line
# anywhere. That silence is what this block ends: the decision is named here,
# with its reason, and the day somebody puts the master back the suite says so
# and the decision gets taken again instead of drifting.
NO_MASTER_ON_PURPOSE = {
    "project_backup": "VACUUM INTO is a reading: it changes nothing and the "
                      "copy lands on the server's disk, not in the browser, "
                      "so the master would have defended against one extra "
                      "file in a directory — and a master typed where it "
                      "guards nothing is a master typed without looking",
}
ok(set(NO_MASTER_ON_PURPOSE) <= set(_ENDPOINTS),
   "every master-free endpoint named here exists",
   sorted(set(NO_MASTER_ON_PURPOSE) - set(_ENDPOINTS)))
for _name, _why in NO_MASTER_ON_PURPOSE.items():
    _fn = _BUILD_FUNCS.get(_name)
    ok(_fn is not None and not [n for n in ast.walk(_fn) if isinstance(n, ast.Call)
                                and ast.unparse(n.func) == "secrets.compare_digest"],
       f"{_name} asks for no master ON PURPOSE — {_why[:56]}...",
       "it now compares one: if that is the new decision, drop the exception")
    _reached = _engine_reached(_fn or ast.Module(body=[], type_ignores=[]))
    ok(not (_reached & MUTATING),
       f"and the exception holds only while {_name} writes nothing",
       sorted(_reached & MUTATING))

# The session check is the FIRST statement of the handler, and not conditional.
# `if request.query_params.get("preview"): ...` in front of it reads like a
# convenience and is an open page: every check that asks whether _session_ok
# APPEARS in the function is satisfied by it.
for _e in _ENDPOINTS:
    if _e in NO_SESSION_ON_PURPOSE or _e not in _BUILD_FUNCS:
        continue
    _body = [x for x in _BUILD_FUNCS[_e].body
             if not (isinstance(x, ast.Expr) and isinstance(x.value, ast.Constant))]
    _first = ast.unparse(_body[0]) if _body else "(empty)"
    ok(_first in ("if not _session_ok(request):\n    return _guest(request)",
                  "return _read_page(request, render)")
       or _first.startswith("def render"),
       f"{_e}: the session check is the first thing it does, or it delegates to "
       f"the page that does", _first[:60])

# THE GUARDS OF THE TWO ACTIONS, pinned AS WRITTEN. Everything above pins that
# they are called; these pin what they say, which is the half that a
# plausible-looking edit changes. Each was injected and each named its defect:
# the master check turned into a no-op, the digest passed as anything but what
# came back, the ceiling shifted by one.
if _ACT is not None:
    _TESTS = [ast.unparse(n.test) for n in ast.walk(_ACT) if isinstance(n, ast.If)]
    ok("not secrets.compare_digest((form.get('master') or '').strip(), master)"
       in _TESTS,
       "the lot action's master check is the constant-time comparison, as written",
       [t[:60] for t in _TESTS])
    # The digest is no longer COMPARED here — `decide()` does that, in the
    # transaction — so what this file owes is that the one the browser sent
    # travels in untouched. A page that passed the batch's own digest instead
    # would compare a reading with itself and never be stale.
    _DEC = [ast.unparse(n) for n in ast.walk(_ACT) if isinstance(n, ast.Call)
            and ast.unparse(n.func) == "prj.decide"]
    ok(_DEC == ["prj.decide(seen, ticked, denials)"],
       "and it hands decide() the digest it was GIVEN, the ticks and the "
       "reasons — nothing computed here", _DEC)
    ok("seen = (form.get('digest') or '').strip()" in ast.unparse(_ACT),
       "and `seen` is that hidden field and nothing else",
       [x[:60] for x in ast.unparse(_ACT).splitlines() if "seen" in x])
_REN = _WEB_FUNCS.get("renewals_action")
ok(_REN is not None, "web.py defines the renewals action")
if _REN is not None:
    _TESTS = [ast.unparse(n.test) for n in ast.walk(_REN) if isinstance(n, ast.If)]
    ok("not secrets.compare_digest((form.get('master') or '').strip(), master)"
       in _TESTS,
       "the renewals action's master check is the constant-time comparison too",
       [t[:60] for t in _TESTS])
    # THE ONE PLACE the ceiling is still enforced by this file, because renew
    # and promote have no engine method that does it. MORE than the cap, not as
    # many — one character either way is the whole knob — and `cap > 0` because
    # zero means the queue is closed to PROPOSING, not that nothing may be
    # renewed. The engine spells it the same way in decide().
    ok("cap is not None and cap > 0 and (len(ticked) > cap)" in _TESTS,
       "and the ceiling refuses MORE than the cap, reading zero the way the "
       "engine reads it", [t[:60] for t in _TESTS])

# The session's own machinery is pinned by NAME too: `_session_ok = lambda r:
# True` further down, under a flag, leaves every check above green and the UI
# open. Python gives the name to whatever was bound last, in silence.
for _n in ("_session_ok", "_sign", "_issue", "_open"):
    _defs = [x for x in ast.walk(_BUILD) if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))
             and x.name == _n] if _BUILD is not None else []
    ok(len(_defs) == 1, f"build() defines `{_n}` exactly once", len(_defs))
    _rebound = [ast.unparse(x)[:50] for x in ast.walk(_BUILD or ast.Module(body=[], type_ignores=[]))
                if isinstance(x, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr))
                and any(isinstance(t, ast.Name) and t.id == _n
                        and isinstance(t.ctx, ast.Store) for t in ast.walk(x))]
    ok(not _rebound, f"and `{_n}` is never bound to anything else", _rebound)

print("\n== the log reaches the browser from memory, and cannot grow ==")

import logging                                                  # noqa: E402

# THE SHAPE IS THE GUARANTEE. A page that shows log lines has exactly two ways
# of being wrong, and neither of them fails at runtime: it grows without a
# ceiling until the process dies, or it goes looking for the lines somewhere
# else — a file that needs a path and permissions, or the docker socket, which
# is a hole a page on the LAN must not be able to reach through. Both are
# checked from the source, because both work perfectly on the day they are
# written.
_WEBMOD = __import__("web")
ok(getattr(_WEBMOD, "LOG_RING_LINES", None) == 200,
   "web.py declares how many lines the ring holds, and it is a named constant",
   getattr(_WEBMOD, "LOG_RING_LINES", None))
_RING = getattr(_WEBMOD, "LogRing", None)
ok(_RING is not None and issubclass(_RING, logging.Handler),
   "and LogRing is a logging.Handler: the lines arrive by being logged, so it "
   "has no source of its own and cannot show what the console does not")
if _RING is not None:
    _r = _RING(lines=5)
    for _i in range(8):
        _r.emit(logging.LogRecord("codifier-mcp", logging.INFO, "x", 1,
                                  "line %s", (_i,), None))
    ok(len(_r.lines) == 5, "the ring is BOUNDED — measured, not declared: eight "
                           "lines into a ring of five leaves five", len(_r.lines))
    ok("line 0" not in "\n".join(_r.lines) and "line 7" in "\n".join(_r.lines),
       "and it is the OLDEST that falls out", list(_r.lines))
    ok(_r.lines.maxlen == 5, "and maxlen is what does it, so it cannot grow")
    ok("Z " in _r.lines[-1], "and every line carries its zone: the container's "
                             "clock is not the reader's", _r.lines[-1])

# It is hung on the logger the service HANDED IN — a ring on a logger of its
# own is how a line stops appearing in the log everybody reads — and a stale
# one is taken off first, or the probes, which call build() many times, would
# see every line twice with nothing to say so.
if _BUILD is not None:
    _HANDLER_CALLS = {ast.unparse(n.func) for n in ast.walk(_BUILD)
                      if isinstance(n, ast.Call)}
    ok("log.addHandler" in _HANDLER_CALLS,
       "the ring is attached inside build(), to the logger it was handed")
    ok("log.removeHandler" in _HANDLER_CALLS,
       "and an older one is taken off first: two rings on one logger is every "
       "line twice")

# NOWHERE ELSE. No file is opened, no process is run: the ring is the only
# source, and these are the two shortcuts somebody reaches for when 200 lines
# in memory feel like not enough.
_OPENS = sorted({ast.unparse(n.func) for n in ast.walk(WEB_TREE)
                 if isinstance(n, ast.Call)
                 and ast.unparse(n.func) in ("open", "os.popen")
                 or (isinstance(n, ast.Call)
                     and ast.unparse(n.func).startswith(("subprocess.",
                                                         "pathlib.Path")))})
ok(not _OPENS, "web.py opens no file and runs no process for the log", _OPENS)
# Docstrings subtracted, for the reason the SQL check subtracts them: left in,
# this goes red on the very paragraph that explains why the socket is out of
# bounds — and a check that fails on a legitimate sentence gets deleted rather
# than obeyed. Comments never reach the AST at all, so they are free.
_DOCKERY = sorted({n.value[:40] for n in ast.walk(WEB_TREE)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and id(n) not in _DOCNODES
                   and ("docker" in n.value.lower() or "/var/log" in n.value)})
ok(not _DOCKERY,
   "and it never reaches for docker or a log file: neither the socket nor a "
   "path on disk is something a page on the LAN gets to hold", _DOCKERY)

# NEWEST FIRST, and the count on the page is READ from the ring rather than
# written a second time. Both are one edit away from being wrong and neither
# would ever fail.
_MAINT = _WEB_FUNCS.get("_maintenance_html")
ok(_MAINT is not None, "web.py builds the maintenance page in one place")
if _MAINT is not None:
    _MSRC = ast.unparse(_MAINT)
    ok("reversed(" in _MSRC,
       "and it shows the most recent first — a log is read to answer 'what just "
       "happened', and that answer is at the top of a page")
    ok("ring.lines.maxlen" in _MSRC and "200" not in _MSRC,
       "and the ceiling on the page is read from the ring, never spelled out "
       "twice")

print("\n== the boot serves two servers on one loop ==")

# C4. The MCP app and the admin UI live in ONE process, on ONE asyncio loop:
# two processes on the same SQLite do not share the RLock, which is why the
# separate container is a closed alley. The shape is pinned from the AST,
# because every half of it fails silently on its own: `mcp.run(...)` left
# behind serves the MCP and never starts the UI, and a single uvicorn.Server
# serves the UI and never starts the MCP — in both cases the process comes up,
# the startup line is printed, and only one of the two ports answers.
_SERVE = next((n for n in SERVER_TREE.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "_serve"), None)
ok(isinstance(_SERVE, ast.AsyncFunctionDef),
   "server.py defines `_serve`, and it is a coroutine: two servers need one loop",
   type(_SERVE).__name__ if _SERVE is not None else "absent")
ok(not [n for n in ast.walk(SERVER_TREE) if isinstance(n, ast.Call)
        and ast.unparse(n.func) == "mcp.run"],
   "and `mcp.run(...)` is gone: it owns the loop and would never let the UI start")
if _SERVE is not None:
    _CALLED = {ast.unparse(n.func) for n in ast.walk(_SERVE) if isinstance(n, ast.Call)}
    _SERVERS = [n for n in ast.walk(_SERVE) if isinstance(n, ast.Call)
                and ast.unparse(n.func) == "uvicorn.Server"]
    ok(len(_SERVERS) == 2, "and it builds exactly two uvicorn.Server", len(_SERVERS))
    ok("mcp.http_app" in _CALLED,
       "and one of them is handed mcp.http_app() — the MCP surface, unmoved",
       sorted(_CALLED))
    ok("web.build" in _CALLED,
       "and the other the app web.py builds", sorted(_CALLED))
    _GATHERS = [n for n in ast.walk(_SERVE) if isinstance(n, ast.Call)
                and ast.unparse(n.func) == "asyncio.gather"]
    ok(len(_GATHERS) == 1,
       "and both are awaited together: serving one and then the other is serving one",
       len(_GATHERS))

# The startup line is what you read to confirm an update took, and the UI's
# port is the field on it a person needs in order to reach the thing at all.
# READ from the resolved constant, never spelled out a second time: the line
# that said "off (not built yet)" stayed true for exactly as long as nobody
# maintained it.
if _MAIN is not None:
    _WEBLINE = [c for c in ast.walk(_MAIN) if isinstance(c, ast.Call)
                and ast.unparse(c.func) == "log.info"
                and any(isinstance(a, ast.Name) and a.id == "WEB_PORT" for a in c.args)]
    ok(bool(_WEBLINE),
       "the startup line carries WEB_PORT, resolved — not a literal, not a guess")
ok("not built yet" not in SERVER_SRC,
   "and it no longer says the UI is not built yet")

print("\n== the preflight refuses an earlier schema, out loud ==")

# THERE IS NO MIGRATION (decided 2026-08-11): a schema change means a wipe,
# because the corpus goes back in by hand. What the preflight owes an old
# database is a RED LINE with the cure in it — not a half-upgrade, and not a
# boot that pretends the file is fine. The db check opens Registry(DB), and
# Registry is where the refusal lives: this proves the refusal reaches the
# preflight's own verdict.
import sqlite3 as _sq3                                          # noqa: E402
import subprocess                                               # noqa: E402
import tempfile                                                 # noqa: E402

def _pf_db(root: str) -> str:
    """Run the db check in a process of its own, against `root`, and hand back
    the verdict line. A subprocess because the check imports the engine and
    opens files: doing that in here would leave this suite holding them."""
    out = subprocess.run(
        [sys.executable, "-c",
         "import preflight; preflight.c_db(); "
         "from mcp_common_engine import RESULTS; "
         "print(RESULTS[-1])"],
        capture_output=True, text=True, cwd=HERE, timeout=60,
        env=dict(os.environ, DB_DIR=root))
    return out.stdout or out.stderr


# A registry in the v4 SHAPE — a line, a folder, a file — carrying a database
# from an earlier generation. The layout matters: a check that walked one path
# would not even find this file.
_md = tempfile.mkdtemp(prefix="preflight-oldschema-")
with open(os.path.join(_md, "projects.txt"), "w", encoding="utf-8") as _fh:
    _fh.write("Old One | REFCODE12345678 | ADMCODE12345678\n")
os.makedirs(os.path.join(_md, "Old One"), exist_ok=True)
_cx0 = _sq3.connect(os.path.join(_md, "Old One", "old-one.db"))
_cx0.executescript("""
  CREATE TABLE rules (project TEXT NOT NULL, id TEXT NOT NULL,
                      PRIMARY KEY (project, id));
  PRAGMA user_version = 3;
""")
_cx0.close()
_out = _pf_db(_md)
ok("False" in _out, "the db check goes RED on an earlier schema", _out[:200])
ok("schema generation 3" in _out and "no migration" in _out
   and "old-one.db" in _out,
   "and the red line names the disease, the cure AND the file: with several "
   "databases served, which one is the half that used not to be needed",
   _out[:300])

# And the mirror image, which is the half that proves the check above measures
# anything: the SAME shape, at the generation this server speaks, goes green
# and says what it served. A check only ever seen red is a check that might be
# refusing everything.
_gd = tempfile.mkdtemp(prefix="preflight-goodschema-")
with open(os.path.join(_gd, "projects.txt"), "w", encoding="utf-8") as _fh:
    _fh.write("New One | REFCODE12345678 | ADMCODE12345678\n")
_out = _pf_db(_gd)
ok("True" in _out and "New One" in _out,
   "and it goes GREEN on a registry it can serve, naming what it opened",
   _out[:300])

# AND IT WALKS THEM ALL. From v4.0.0 the registry names several databases, and
# a check that opened the first one would pass on a boot where the second is
# corrupt. Two projects in, two names out — measured, because "for p in
# served" is one line away from being "served[0]" and neither version fails.
_2d = tempfile.mkdtemp(prefix="preflight-two-")
with open(os.path.join(_2d, "projects.txt"), "w", encoding="utf-8") as _fh:
    _fh.write("First One | REFCODE12345678 | ADMCODE12345678\n"
              "Second One | REFCODE87654321 | ADMCODE87654321\n")
_out = _pf_db(_2d)
# On the FILE names, and that is not pedantry: the line also carries a
# born-empty list which names every project on a fresh directory, so a check
# written on the project names passes with the walk cut down to `served[:1]`.
# Measured — the injection came back green and this is the repair.
ok("True" in _out and "2 served" in _out
   and "first-one.db" in _out and "second-one.db" in _out,
   "and it opens EVERY project the registry names, not the first",
   _out[:300])

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
