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


SERVER_TREE = parse(SERVER)
ENGINE_TREE = parse(ENGINE)

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
    _refs() and approve() through _require_signature()."""
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


TOOLS = [n for n in SERVER_TREE.body if isinstance(n, ast.FunctionDef) and is_tool(n)]
TOOL_NAMES = {t.name for t in TOOLS}

ok(len(TOOLS) >= 25, f"{len(TOOLS)} tools exposed")

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
_converter = next((n for n in SERVER_TREE.body
                   if isinstance(n, ast.FunctionDef) and n.name == "tool"), None)
ok(_converter is not None, "server.py defines its own `tool` decorator")

if _converter is not None:
    handlers = [h for h in ast.walk(_converter) if isinstance(h, ast.ExceptHandler)]
    caught = {getattr(h.type, "id", "") for h in handlers}
    ok("RulesError" in caught, "the decorator catches RulesError")
    raised = [n for h in handlers for n in ast.walk(h)
              if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "ToolError"]
    ok(raised, "and re-raises ToolError")
    # The level is the whole point: ERROR would change nothing.
    levels = [ast.unparse(k.value) for r in raised for k in r.keywords
              if k.arg == "log_level"]
    ok(levels and "ERROR" not in levels[0],
       f"at a level below ERROR ({levels[0] if levels else 'not set'})")
    # `raise X from None` parses as cause=Constant(None) — not as no cause at
    # all, which is what a bare `raise X` gives you.
    ok(any(isinstance(n, ast.Raise) and isinstance(n.cause, ast.Constant)
           and n.cause.value is None for n in ast.walk(_converter)),
       "with `from None`: the chained traceback is what we are removing")
    # functools.wraps is what keeps the MCP schema intact: FastMCP reads the
    # name, docstring and signature, and follows __wrapped__ to find them.
    ok(any(ast.unparse(d) == "functools.wraps(fn)"
           for f in ast.walk(_converter) if isinstance(f, ast.FunctionDef)
           for d in f.decorator_list),
       "and functools.wraps, or every tool would lose its schema")
    ok(any(isinstance(n, ast.Call) and ast.unparse(n.func) == "mcp.tool"
           for n in ast.walk(_converter)),
       "and it still registers the tool with FastMCP")

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

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
