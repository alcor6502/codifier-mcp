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
ok({"propose", "approve", "retire", "amend"} <= MUTATING,
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
NAME_IN_PROSE = re.compile(r"\b(rules_[a-z_]+|[a-z][a-z_]*_guide)\b")
# Names that read like tools and are not, allowed in prose anywhere. It has
# to be subtracted in BOTH places the shape is used: naming the server in a
# manual is a legitimate sentence, and a check that goes red on a legitimate
# sentence gets deleted rather than obeyed.
NOT_TOOLS = {"rules_mcp"}
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

# The gate is a NAME, and every check above is happy as long as the name is
# called. `_admin` redefined once more further down — under an `if
# os.environ.get("DEV")`, which is the shape this arrives in — leaves every one
# of them green with no gate anywhere. Python does not warn: the last
# definition wins. So the definition is pinned too: one, at module level, never
# reassigned.
_ADMIN = sole_binding("_admin", (ast.FunctionDef, ast.AsyncFunctionDef),
                      "the last binding wins and every gated tool calls the name")

# And the BODY of the gate, because everything else here pins the name, the
# count and the call site while leaving what it does unconstrained. A decorator
# that skips it, or a `if os.environ.get("DEV"): return` in front of the
# comparison, are two lines that read like conveniences and open the registry:
# both measured green before this. The comparison is constant-time on purpose —
# `==` on a secret is a different defect — so it is pinned as written.
if _ADMIN is not None:
    _gbody = [s for s in _ADMIN.body
              if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    _guard = _gbody[0] if _gbody else None
    ok(isinstance(_guard, ast.If)
       and ast.unparse(_guard.test).startswith("not secrets.compare_digest(")
       and any(isinstance(r, ast.Raise) for r in ast.walk(_guard)),
       "and its first act is the constant-time comparison, and it raises",
       ast.unparse(_guard)[:70] if _guard else "(empty)")

# And it is called UNCONDITIONALLY, first thing. `if code: _admin(code)` reads
# like making an argument optional and is an open door: every check that asks
# whether _admin appears inside the function is satisfied by it. First
# statement after the docstring, exactly `_admin(code)`, or say so.
for _t in TOOLS:
    if not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "_admin" for n in ast.walk(_t)):
        continue
    _body = [s for s in _t.body
             if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    _first = ast.unparse(_body[0]) if _body else "(empty)"
    # The tool's own first parameter, whatever it is called, passed straight
    # through — positionally or by keyword. Pinning the literal string
    # `_admin(code)` would go red on `_admin(code=code)`, which changes
    # nothing, and a check that fails on a harmless edit gets deleted.
    _params = [a.arg for a in _t.args.posonlyargs + _t.args.args]
    _accept = {f"_admin({p})" for p in _params} | {f"_admin({p}={p})" for p in _params}
    ok(_first in _accept,
       f"{_t.name}: the gate is the first statement, and it is not conditional",
       _first[:60])

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

for _label, _attr, _unit in (("IDs per `rules_get`", "MAX_GET_IDS", ""),
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
_LOOSE = sorted({ast.unparse(n.func.value) for n in _READERS
                 if ast.unparse(n.func.value) not in PATH_CONSTS})
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
if _REF is not None:
    _reads = {PATH_CONSTS.get(c) for c in _TOUCHES_CONST.get("reference_guide", set())}
    ok(_reads == {"reference-guide.md"},
       "reference_guide serves reference-guide.md, and nothing else",
       sorted(map(str, _reads)))

# The manual is OPEN, and that is the decision this section holds: it is read
# by three chats, the skills do not read it at all, and the stop line inside
# the file is the boundary that used to be a second tool behind the code.
_GATED = {t.name for t in ([_REF] if _REF is not None else [])
          if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "_admin" for n in ast.walk(t))}
ok(_GATED == set(),
   "reference_guide takes no admin code: the manual is one file, open",
   sorted(_GATED))

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

# The STOP line is the boundary the whole delivery is: one manual, and the
# consumer's part ends where it stands. COUNTED, not `in`-tested — a rewrite
# that leaves a second copy behind, with a contradicting line between them, is
# exactly what `in` cannot see.
ok(GUIDE_SRC.count("⛔ STOP — everything below requires the maintenance code") == 1,
   "the manual carries the stop line, exactly once",
   GUIDE_SRC.count("⛔ STOP — everything below requires the maintenance code"))

# The legislator's part has to stay APPLICABLE, and that is not something a
# test can judge. What it can hold is the shape the applicability rests on: the
# gates, each of which is a question asked of one line. A rewrite that turns
# them back into principles has to come through here and say so.
for _pin in ("GATE 1 — Is it a rule, or a step?",
             "GATE 2 — Is it a rule, or a missing manual?",
             "GATE 3 — Is it a rule, or a reminder?",
             "GATE 4 — Who could violate it?",
             "Would it still be true if the procedure changed?"):
    ok(GUIDE_SRC.count(_pin) == 1,
       f"the manual carries, exactly once: {_pin!r}",
       GUIDE_SRC.count(_pin))

# Every file a tool serves has to exist, and be IN the image. The explicit list
# in the Dockerfile section is the other half; this half is derived, so a
# manual added tomorrow cannot be forgotten in a list nobody remembers to
# extend. The defect has been paid once already, with reference_guide pointing
# at a file that did not exist.
ok(SERVED_FILES == ["reference-guide.md"],
   "server.py serves exactly the one manual", SERVED_FILES)
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

DOCKER_COPIES = [l for l in DOCKERFILE.splitlines() if l.startswith("COPY ")]
ok(not any("*" in l for l in DOCKER_COPIES),
   "Dockerfile: no wildcard COPY — the test files do not belong in the image",
   [l for l in DOCKER_COPIES if "*" in l])
for f in ("rules.py", "server.py", "preflight.py", "entrypoint.sh",
          "reference-guide.md"):
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
ok("<Icon>" in TEMPLATE and os.path.exists(os.path.join(HERE, "codifier-icon.png")),
   "the icon the template links to is IN the repository — a raw URL that 404s "
   "is the twin's oldest open item")
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
for var in ("PROVISIONAL_DAYS", "WEB_PORT", "WEB_MASTER_CODE", "WEB_ACTION_CAP",
            "LOG_LEVEL", "ALLOWED_CIDRS", "DB_PATH", "BACKUP_DIR", "ADMIN_ACCESS_CODE"):
    ok(f'Target="{var}"' in TEMPLATE, f"template declares {var}")

# The master is the TWIN of ADMIN_ACCESS_CODE, and the spec says so in those
# words: mandatory, masked, and blocked at boot while it is a placeholder. It
# is the one new variable that cannot be "born optional with a working default
# in the code" — a default for a master IS the placeholder the preflight
# refuses. Required in the template moves the failure from a container that
# will not boot to a form that will not save.
_MASTER_FIELD = re.search(r'<Config[^>]*Target="WEB_MASTER_CODE"[^>]*>', TEMPLATE)
ok(_MASTER_FIELD is not None, "the master is a field of the template")
if _MASTER_FIELD:
    _f = _MASTER_FIELD.group(0)
    # The NAME a person reads in Unraid, and it is the sibling's: "Admin Access
    # Code" and "Web UI Master Code" sit next to each other in the form, and
    # the pairing is half of what says they are two secrets of the same kind
    # and not one secret with two homes.
    ok('Name="Web UI Master Code"' in _f,
       "and it is named as the Admin Access Code's sibling", _f[:60])
    ok('Mask="true"' in _f, "and it is masked, like the maintenance code", _f[:80])
    ok('Required="true"' in _f,
       "and required: a master with a working default is the open door", _f[:80])
# The ceiling is the other kind of new variable: optional, with the default in
# the code, because Unraid does not propagate new variables to containers that
# are already installed.
_CAP_FIELD = re.search(r'<Config[^>]*Target="WEB_ACTION_CAP"[^>]*>', TEMPLATE)
ok(_CAP_FIELD is not None and 'Required="false"' in _CAP_FIELD.group(0),
   "the per-action ceiling is optional, with its default in the code",
   _CAP_FIELD.group(0)[:80] if _CAP_FIELD else "absent")
# And the field that promised a read-only interface that would read a
# VACUUM INTO snapshot no longer says any of that: it was true of a design
# that was not built, and the one that was built writes.
for _dead in ("not built yet", "read-only web interface", "VACUUM INTO snapshot",
              "unpublishable by construction"):
    ok(_dead not in TEMPLATE,
       f"the template no longer promises: {_dead!r}")

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

# Decided 2026-08-10: sign.py, the key knobs and the signature parameter all
# leave; approve/renew/promote stay on the MCP behind the admin code until the
# UI exists. The digest stays — it is the check that you approve the batch you
# read, and it was never the signature's.
ok(not os.path.exists(os.path.join(HERE, "sign.py")),
   "sign.py is gone from the repository")
for _tok in ("APPROVAL_PUBKEY", "APPROVAL_GRACE_UNTIL"):
    for _fname, _src2 in (("codifier-mcp.xml", TEMPLATE), ("server.py", SERVER_SRC),
                          ("rules.py", RULES_SRC), ("preflight.py", PREFLIGHT_SRC)):
        ok(_tok not in _src2, f"{_fname} no longer knows {_tok}")
for _tname in ("rules_approve", "rules_renew", "rules_promote"):
    _t2 = next((t for t in TOOLS if t.name == _tname), None)
    ok(_t2 is not None, f"{_tname} is still on the surface: the UI is not built yet")
    if _t2 is not None:
        _p2 = [a.arg for a in _t2.args.posonlyargs + _t2.args.args]
        ok("signature" not in _p2, f"{_tname} takes no signature", _p2)
        ok("code" in _p2, f"{_tname} still wants the admin code", _p2)
_APPR = next((t for t in TOOLS if t.name == "rules_approve"), None)
if _APPR is not None:
    _p3 = [a.arg for a in _APPR.args.posonlyargs + _APPR.args.args]
    ok("digest" in _p3,
       "rules_approve still wants the digest: you approve the batch you READ", _p3)
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
ok("ux_rules_supersedes" in _rules.INDEXES,
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
ok("consumer_versions" in _rules.TABLES
   and {"trg_consumers_ins", "trg_consumers_upd"} <= set(_rules.TRIGGERS),
   "the versions table and its triggers are declared, so the preflight sees them")
ok(GUIDE_SRC.count("your **brief**") == 1,
   "the manual pins the brief at the head of the list, exactly once",
   GUIDE_SRC.count("your **brief**"))

print("\n== proposed_by is a door, and the queue has a ceiling ==")

# F4: the owner's reading rhythm as a number that refuses, moved out of the
# dead AM domain and into the tool, where a machine-checkable constraint
# belongs. Born optional with a working default in the code, because Unraid
# does not propagate new variables to installed containers.
ok('Target="PENDING_CAP"' in TEMPLATE, "the template declares PENDING_CAP")
ok(getattr(_rules, "DEFAULT_PENDING_CAP", None) == 5,
   "the default lives in the code, and it is 5",
   getattr(_rules, "DEFAULT_PENDING_CAP", None))
for _t in TOOLS:
    if _t.name == "rules_propose":
        _doc = ast.get_docstring(_t) or ""
        ok("proposed_by` is MANDATORY" in _doc,
           "rules_propose says proposed_by is mandatory")
        ok("default 5" in _doc, "and names the queue ceiling's default")
ok("limited number of pending proposals" in GUIDE_SRC,
   "the manual documents the pending ceiling")

print("\n== the renewal reads the why, and the lists carry the legend ==")

# F5 and F7: the reason where the deciding happens, the glosses where the IDs
# are listed in bulk.
for _t in TOOLS:
    _doc = ast.get_docstring(_t) or ""
    if _t.name == "rules_renew":
        ok("ORIGINAL reason" in _doc,
           "rules_renew promises the original reason in its verdict")
    if _t.name == "rules_pending":
        ok("expiring" in _doc and "reason" in _doc,
           "rules_pending says the expiring queue carries the reason")
    if _t.name == "rules_list":
        ok("LEGEND" in _doc, "rules_list promises the domain legend")
ok(GUIDE_SRC.count("legend of the domains present") == 1,
   "the manual pins the legend, exactly once",
   GUIDE_SRC.count("legend of the domains present"))

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
    ok("WEB_MASTER_CODE" in _WSRC and "is_placeholder" in _WSRC,
       "and it refuses a master that is missing or still a placeholder")
_PF_READS_PORT = [n for n in ast.walk(_PF_TREE)
                  if isinstance(n, ast.Call)
                  and ast.unparse(n.func) in ("os.environ.get", "os.getenv")
                  and n.args and isinstance(n.args[0], ast.Constant)
                  and n.args[0].value == "WEB_PORT"]
ok(not _PF_READS_PORT,
   "preflight.py does not read WEB_PORT on its own — it comes from web.py",
   [ast.unparse(n) for n in _PF_READS_PORT])

print("\n== web.py -> rules.py: every call lands ==")

# The SECOND seam, and it is the same class of defect as the first: a renamed
# parameter between these two files goes unnoticed until somebody clicks. The
# engine's own suites cannot see it — they call Registry directly — and
# nothing in the browser would report it as anything but a 500.
WEB_CALLS = [n for n in ast.walk(WEB_TREE)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "registry"]
ok(len(WEB_CALLS) >= 3, f"{len(WEB_CALLS)} calls into the engine found in web.py")
for call in WEB_CALLS:
    name = call.func.attr
    where = f"web.py line {call.lineno}"
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
    ok(not problems, f"web.py: registry.{name}(...) matches its signature",
       f"{where}: {'; '.join(problems)}")

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

# The ORDER inside the action, and it is the whole of "one round": the ones
# left unticked are DENIED first, and only then is what remains approved.
# Approving first would approve the whole pending batch — the engine's
# approve() takes no list — which is the unticked ones let in by the very
# gesture that meant to keep them out. Read from the AST because the two calls
# are three lines apart and swapping them looks like tidying.
_ACT = _WEB_FUNCS.get("batch_action")
ok(_ACT is not None, "web.py defines the lot page's action")
if _ACT is not None:
    # By LINE, not by ast.walk's order, which is breadth-first and had these
    # two the wrong way round while the code was right — a check that reports
    # the order of a tree traversal as the order of the source is a check that
    # cannot see the defect it exists for.
    _seq = [n.func.attr for n in sorted(
        (n for n in ast.walk(_ACT)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
         and isinstance(n.func.value, ast.Name) and n.func.value.id == "registry"
         and n.func.attr in ("deny", "approve")),
        key=lambda n: (n.lineno, n.col_offset))]
    ok(_seq == ["deny", "approve"],
       "and it denies the unticked BEFORE approving the rest: approve() takes "
       "the whole batch, so the other order lets in exactly what was refused",
       _seq)

# The CEILING is a knob of the template with a default in the code, resolved
# once like the port, and refused at the edge rather than at the click.
ok(getattr(__import__("web"), "DEFAULT_ACTION_CAP", None) is not None,
   "web.py declares the per-action ceiling's default")
ok(hasattr(__import__("web"), "action_cap_from_env"),
   "and resolves it in one expression, for the preflight to share")
if _WEB_CHECK:
    ok("web.action_cap_from_env" in ast.unparse(_WEB_CHECK[0]),
       "and the preflight validates it at the edge, like the port")

print("\n== the consultation reads, and only reads ==")

# The four views the spec asks for, each named by the method that serves it.
# Naming the METHOD and not the route is the point: a page that quietly built
# its own answer instead of asking the engine would be a second reading of the
# corpus, and two readings of a corpus disagree.
for _m, _what in (("list_rules", "the rules in force for a consumer"),
                  ("history", "a rule's history"),
                  ("compare", "the diff between two of its versions"),
                  ("pending", "the pendings and the expiring"),
                  ("status", "the state of the registry")):
    ok(any(n.func.attr == _m for n in WEB_CALLS),
       f"the UI serves {_what} from registry.{_m}()")

# The brief LEADS the list, and the legend travels with it: that is the shape
# rules_list promises, and a page that dropped either would be showing a
# consumer something different from what its chat reads.
ok("brief" in WEB_SRC, "and the rules page carries the brief, as rules_list does")
ok("domains" in WEB_SRC, "and the legend of the domains present")

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
                                      ("/p/{project}/batch", "batch_action")],
   "and exactly three of them take POST: login, logout and the lot's action",
   [(r[0], r[2]) for r in _POSTS])
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
for _name, _fn in _BUILD_FUNCS.items():
    _reached = {n.func.attr for n in ast.walk(_fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "registry"}
    _writes = _reached & MUTATING
    if not _writes:
        continue
    ok(_reaches(_name, "_session_ok"),
       f"{_name} writes ({', '.join(sorted(_writes))}) and is behind the session")
    ok(any(ast.unparse(n.func) == "secrets.compare_digest" for n in ast.walk(_fn)
           if isinstance(n, ast.Call)),
       f"{_name} writes and retypes the master — a session alone is a browser "
       f"left open on the iPad")

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

# THE THREE GUARDS OF THE ACTION, pinned AS WRITTEN. Everything above pins
# that they are called; these pin what they say, which is the half that a
# plausible-looking edit changes. Each was injected and each named its
# defect: the master check turned into a no-op, the digest comparison
# removed, the ceiling shifted by one.
if _ACT is not None:
    _TESTS = [ast.unparse(n.test) for n in ast.walk(_ACT) if isinstance(n, ast.If)]
    ok("not secrets.compare_digest((form.get('master') or '').strip(), master)"
       in _TESTS,
       "the action's master check is the constant-time comparison, as written",
       [t[:60] for t in _TESTS])
    ok("seen != current['digest']" in _TESTS,
       "and the digest it was handed is compared with the batch's, before "
       "anything is written", [t[:60] for t in _TESTS])
    ok("len(ticked) > action_cap" in _TESTS,
       "and the ceiling refuses MORE than the cap, not as many — one character "
       "either way is the whole knob", [t[:60] for t in _TESTS])

# The session's own machinery is pinned by NAME too: `_session_ok = lambda r:
# True` further down, under a flag, leaves every check above green and the UI
# open. Python gives the name to whatever was bound last, in silence.
for _n in ("_session_ok", "_sign", "_issue", "_code_of"):
    _defs = [x for x in ast.walk(_BUILD) if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))
             and x.name == _n] if _BUILD is not None else []
    ok(len(_defs) == 1, f"build() defines `{_n}` exactly once", len(_defs))
    _rebound = [ast.unparse(x)[:50] for x in ast.walk(_BUILD or ast.Module(body=[], type_ignores=[]))
                if isinstance(x, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr))
                and any(isinstance(t, ast.Name) and t.id == _n
                        and isinstance(t.ctx, ast.Store) for t in ast.walk(x))]
    ok(not _rebound, f"and `{_n}` is never bound to anything else", _rebound)

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

print("\n== the preflight declares the migration it performs ==")

# The preflight opens Registry(DB) BEFORE the server, so the migration happens
# in there — and at v1.6.0 it happened in silence: the server's "schema
# migrated at open" line never appeared, because by the time the server opened
# the file there was nothing left to migrate. A declaration whose only
# possible reader is switched off is not a declaration. So the preflight says
# it itself, and this proves it on a database that really migrates.
import subprocess                                               # noqa: E402
import tempfile                                                 # noqa: E402

_md = tempfile.mkdtemp(prefix="preflight-migrate-")
_mdb = os.path.join(_md, "rules.db")
_r0 = _rules.Registry(_mdb)
_r0.cx.execute("ALTER TABLE approvals ADD COLUMN signature TEXT")
_r0.cx.execute("ALTER TABLE approvals ADD COLUMN signed INTEGER NOT NULL DEFAULT 1")
_r0.close()
_env = dict(os.environ, DB_PATH=_mdb)
_out = subprocess.run(
    [sys.executable, "-c",
     "import preflight; preflight.c_db(); "
     "from mcp_common_engine import RESULTS; "
     "print(RESULTS[-1])"],
    capture_output=True, text=True, env=_env, cwd=HERE, timeout=60)
ok(_out.returncode == 0, "the db check runs against a database that migrates",
   (_out.stderr or _out.stdout)[:120])
ok("migrated: approvals.signature dropped" in _out.stdout,
   "and its line DECLARES what the open migrated", _out.stdout[:200])

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
