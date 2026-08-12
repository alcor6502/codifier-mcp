#!/usr/bin/env python3
"""The registry suite: what the ROUTER refuses, and what it says out loud.

`projects.txt` is the truth about which databases are served, and it is edited
by a person from Unraid with an ordinary text editor. Everything that can go
wrong with that — a field missed, a code pasted twice, a folder renamed while
the line was not — has to come back as a refusal that names the LINE or the
FILE, never as a project quietly serving something else.

So every case here works on a real directory with real SQLite files: the
router's whole job is the world outside the process, and a mock of that world
would only prove the mock.

Runs on the standard library alone: no engine beyond rules.py, no network, no
FastMCP.
"""

import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import time

import rules

_passed = 0
_failed: list[str] = []
_roots: list[str] = []

REF = "reference0000001"
ADM = "admin00000000001"


def _root() -> str:
    d = tempfile.mkdtemp(prefix="codifier-registry-")
    _roots.append(d)
    return d


def _write(root: str, text: str) -> str:
    p = os.path.join(root, rules.REGISTRY_FILE)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    # The router re-reads on mtime, and a test writes twice inside one
    # nanosecond-poor filesystem tick. Pushing the stamp back is honest: it
    # simulates the only thing that matters, which is that the file MOVED.
    os.utime(p, (time.time() - 1, time.time() - 1))
    return p


def _line(name=None, ref=REF, adm=ADM) -> str:
    return f"{name or 'Financial Portfolio'} | {ref} | {adm}\n"


class _Ears(logging.Handler):
    """What the log actually said. The `created empty database` line is a
    GUARANTEE, not a courtesy: it is the only signal a rename went half way."""

    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def listening():
    ears = _Ears()
    rules.log.addHandler(ears)
    rules.log.setLevel(logging.INFO)
    return ears


def refused(name, gesture, expect):
    """The gesture must be refused, and the refusal must contain `expect`."""
    global _passed
    try:
        gesture()
        _failed.append(f"{name}: NOT REFUSED — the router let it through")
        print(f"  FAIL  {name}: not refused")
    except Exception as exc:                       # noqa: BLE001 — any refusal
        msg = str(exc)
        if expect.lower() in msg.lower():
            _passed += 1
            print(f"  ok    {name}")
        else:
            _failed.append(f"{name}: refused, but the message never says "
                           f"{expect!r} — {msg}")
            print(f"  FAIL  {name}: refusal does not name it — {msg[:70]}")


def equals(name, got, want):
    global _passed
    if got == want:
        _passed += 1
        print(f"  ok    {name}")
    else:
        _failed.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


def yields(name, gesture, want):
    """`equals`, but the value comes from a CALL that may refuse.

    It exists because of an injection: with the re-read frozen, a case that
    read `reg.project(...).name` inline raised out of the suite and stopped it
    — the run died at case nine with a traceback, which is the one shape a
    failure must never take. A guard taken away has to come back as a red line
    with a name on it."""
    global _passed
    try:
        got = gesture()
    except Exception as exc:                       # noqa: BLE001
        _failed.append(f"{name}: raised {type(exc).__name__} — {exc}")
        print(f"  FAIL  {name}: raised {type(exc).__name__} instead of answering")
        return
    equals(name, got, want)


def says(name, ears, expect):
    global _passed
    hit = [ln for ln in ears.lines if expect.lower() in ln.lower()]
    if hit:
        _passed += 1
        print(f"  ok    {name}")
    else:
        _failed.append(f"{name}: the log never said {expect!r} — said {ears.lines!r}")
        print(f"  FAIL  {name}: the log never said {expect!r}")


# =====================================================================
print("\n— THE FILE IS THE TRUTH —")
r = _root()
ears = listening()
reg = rules.Registry(r)
equals("a missing registry is created, not assumed",
       os.path.exists(os.path.join(r, rules.REGISTRY_FILE)), True)
equals("and it is created from the template, instructions inside",
       open(os.path.join(r, rules.REGISTRY_FILE), encoding="utf-8").read(),
       rules.REGISTRY_TEMPLATE)
equals("the codes are in clear, so the file is root-only",
       oct(os.stat(reg.file).st_mode & 0o777), oct(rules.REGISTRY_MODE))
yields("a registry with no project line serves nothing",
       lambda: reg.projects()["count"], 0)
says("and it says why nothing is served", ears, "created")
reg.close()

r = _root()
_write(r, "# a comment\n\n   \n" + _line())
reg = rules.Registry(r)
yields("comments and blank lines are not projects",
       lambda: reg.projects()["count"], 1)
yields("the folder is the name and the file is its slug",
       lambda: reg.project(REF).path,
       os.path.join(r, "Financial Portfolio", "financial-portfolio.db"))
reg.close()

# =====================================================================
print("\n— A MALFORMED LINE STOPS EVERYTHING, AND NAMES ITSELF —")
r = _root()
_write(r, "# head\n" + f"Financial Portfolio | {REF}\n")
refused("a line with two fields is refused, naming the line",
        lambda: rules.Registry(r), "line 2")

r = _root()
_write(r, _line() + f"Palestra | {REF}x | {ADM}y | extra\n")
refused("a line with four fields is refused, naming the line",
        lambda: rules.Registry(r), "line 2")

r = _root()
_write(r, "  | %s | %s\n" % (REF, ADM))
refused("a line with no name is refused",
        lambda: rules.Registry(r), "line 1")

r = _root()
_write(r, _line(name="Piano_A"))
refused("a name the file system cannot carry names the characters",
        lambda: rules.Registry(r), "'_'")

# The two a slug would happily swallow: a name that starts with a dash is a
# folder every shell reads as an option, and a name of any length at all is a
# path waiting to be too long. Only the name check stands between them and a
# directory nobody can address.
r = _root()
_write(r, _line(name="- Palestra"))
refused("a name that starts with a separator is refused before it becomes a folder",
        lambda: rules.Registry(r), "starts with a letter or a digit")

r = _root()
_write(r, _line(name="P" * 42))
refused("a name past the ceiling is refused, and the ceiling is said",
        lambda: rules.Registry(r), "41 characters")

r = _root()
_write(r, _line(ref="short"))
refused("a code that is not 8 to 32 letters and digits is refused, and it says which",
        lambda: rules.Registry(r), "reference code")

r = _root()
_write(r, _line(adm=""))
refused("an empty admin code is a door left open, not a default",
        lambda: rules.Registry(r), "admin code")

r = _root()
_write(r, _line() + _line(name="Palestra", ref="otherref00001"))
refused("the same admin code on two projects is refused, naming both lines",
        lambda: rules.Registry(r), "line 1")

r = _root()
_write(r, _line(adm=REF))
refused("a reference code that is also the admin code is elevation, and is refused",
        lambda: rules.Registry(r), "admin code")

r = _root()
_write(r, _line() + _line(name="financial   portfolio",
                          ref="otherref00001", adm="otheradm00001"))
refused("two names that would share one database are refused, naming both lines",
        lambda: rules.Registry(r), "financial-portfolio.db")

r = _root()
with open(os.path.join(r, rules.REGISTRY_FILE), "wb") as fh:
    fh.write(b"# head\n# second\nFinanci\xe0 | %s | %s\n" % (REF.encode(), ADM.encode()))
refused("a file saved in another encoding is refused, naming the line",
        lambda: rules.Registry(r), "line 3")
# And the refusal has to come from the DECODING, not from a name check that
# happens to trip over a replacement character further down. Written as its
# own case because an injection proved it: with errors='replace' the case
# above still passed, and what it was measuring was not what it claimed.
refused("and it is the encoding that says so, not a name check downstream",
        lambda: rules.Registry(r), "not UTF-8")

# =====================================================================
print("\n— A LINE WITHOUT A DATABASE CREATES ONE, OUT LOUD —")
r = _root()
ears = listening()
_write(r, _line())
reg = rules.Registry(r)
db = os.path.join(r, "Financial Portfolio", "financial-portfolio.db")
equals("the database is created where the line says", os.path.exists(db), True)
says("and the creation is shouted, because it is the mark of a half-done rename",
     ears, "created empty database for Financial Portfolio")
yields("the boot can list what it created",
       reg.born_empty, ["Financial Portfolio"])
cx = sqlite3.connect(db)
equals("the file created carries the generation it was built with",
       cx.execute("PRAGMA user_version").fetchone()[0], rules.SCHEMA_GENERATION)
yields("and the listing reads that generation off the file, never off the constant",
       lambda: reg.projects()["projects"][0]["schema"],
       cx.execute("PRAGMA user_version").fetchone()[0])
equals("and it carries the whole schema",
       {t for t in rules.TABLES} <= {row[0] for row in cx.execute(
           "SELECT name FROM sqlite_master WHERE type='table'")}, True)
cx.close()
reg.close()

# A database that already exists is opened, not re-created, and the second
# boot must be silent about it: an alarm that fires every day is not read.
ears = listening()
reg = rules.Registry(r)
yields("an existing database is opened, not created again", reg.born_empty, [])
yields("and it is not repaired for nothing", reg.repaired, {})
reg.close()

# =====================================================================
print("\n— THE GENERATION DECIDES WHETHER THE FILE IS SERVED —")
r = _root()
_write(r, _line())
os.makedirs(os.path.join(r, "Financial Portfolio"))
old = os.path.join(r, "Financial Portfolio", "financial-portfolio.db")
cx = sqlite3.connect(old)
cx.execute("CREATE TABLE rules (id TEXT)")
cx.execute(f"PRAGMA user_version = {rules.SCHEMA_GENERATION - 1}")
cx.commit()
cx.close()
refused("a database of an earlier generation is refused, naming the file",
        lambda: rules.Registry(r), "financial-portfolio.db")
refused("and the refusal says both numbers",
        lambda: rules.Registry(r), f"speaks {rules.SCHEMA_GENERATION}")

r = _root()
_write(r, _line())
os.makedirs(os.path.join(r, "Financial Portfolio"))
open(os.path.join(r, "Financial Portfolio", "financial-portfolio.db"), "w").close()
refused("an empty file left behind by a touch is not a database of this generation",
        lambda: rules.Registry(r), "generation 0")

# =====================================================================
print("\n— THE ROUTER IS A DOOR, NOT A DIRECTORY —")
r = _root()
_write(r, _line())
reg = rules.Registry(r)
yields("the reference code opens the project", lambda: reg.project(REF).name,
       "Financial Portfolio")
missing = wrong = ""
try:
    reg.project("")
except rules.RulesError as exc:
    missing = str(exc)
try:
    reg.project("nosuchcode0001")
except rules.RulesError as exc:
    wrong = str(exc)
equals("a wrong code and a missing one get the SAME answer, so neither is confirmed",
       (missing == wrong, bool(missing)), (True, True))
refused("the admin code is not a way in: it elevates, it does not open",
        lambda: reg.project(ADM), "project CODE")
yields("two calls get the same object — one connection and one lock per file",
       lambda: reg.project(REF) is reg.project(REF), True)
yields("what is served is listed by NAME, and carries no code",
       lambda: [k for row in reg.projects()["projects"] for k in row],
       ["name", "slug", "path", "schema", "born_empty"])
yields("the administration page addresses a project by name, case be damned",
       lambda: reg.by_name("financial portfolio").slug, "financial-portfolio")
refused("an unknown name says what IS served, because that door is behind the password",
        lambda: reg.by_name("Palestra"), "Financial Portfolio")
reg.close()

# =====================================================================
print("\n— THE FILE MOVES, AND THE SERVICE FOLLOWS —")
r = _root()
_write(r, _line())
reg = rules.Registry(r)
_write(r, _line() + _line(name="Palestra", ref="palestra00001", adm="palestraadm01"))
yields("a project added does not need a restart", lambda: reg.projects()["count"], 2)
yields("and the one that was already open was not reopened",
       lambda: reg.project("palestra00001").name, "Palestra")
_write(r, _line(name="Palestra", ref="palestra00001", adm="palestraadm01"))
yields("a line removed stops being served", lambda: reg.projects()["count"], 1)
refused("and its code stops opening anything",
        lambda: reg.project(REF), "project CODE")
_write(r, _line(name="Palestra", ref="palestra99999", adm="palestraadm01"))
yields("a code changed in the file is a code changed at the door",
       lambda: reg.project("palestra99999").name, "Palestra")
refused("and the old one is nothing",
        lambda: reg.project("palestra00001"), "project CODE")
reg.close()

# The rename that goes half way: the line says the new name, the folder on
# disk still has the old one. Nothing is lost and nothing is silent — a new
# empty database appears and the log shouts about it.
r = _root()
ears = listening()
_write(r, _line())
reg = rules.Registry(r)
_write(r, _line(name="Financial Book"))
yields("a renamed line points at a new folder", lambda: reg.project(REF).path,
       os.path.join(r, "Financial Book", "financial-book.db"))
says("and the empty database it creates is the alarm", ears,
     "created empty database for Financial Book")
equals("the old folder is left exactly where it was: no tool touches files",
       os.path.exists(os.path.join(r, "Financial Portfolio",
                                   "financial-portfolio.db")), True)
reg.close()

# =====================================================================
print("\n— A BAD RE-READ DOES NOT REPLACE A GOOD READING —")
r = _root()
_write(r, _line())
reg = rules.Registry(r)
before = reg.project(REF)
_write(r, "Financial Portfolio | broken\n")
refused("the re-read refuses, naming the line", lambda: reg.projects(), "line 1")
refused("and it keeps refusing: nothing is served from a stale reading",
        lambda: reg.project(REF), "line 1")
_write(r, _line())
yields("the file fixed, the service answers again — same object, same connection",
       lambda: reg.project(REF) is before, True)
reg.close()

# =====================================================================
print("\n— A SCHEMA OBJECT REMOVED BY HAND IS REBUILT, AND DECLARED —")
r = _root()
_write(r, _line())
reg = rules.Registry(r)
reg.close()
cx = sqlite3.connect(os.path.join(r, "Financial Portfolio", "financial-portfolio.db"))
cx.execute(f"DROP TRIGGER {rules.TRIGGERS[0]}")
cx.commit()
cx.close()
reg = rules.Registry(r)
yields("the trigger somebody dropped comes back, and it is named",
       reg.repaired, {"Financial Portfolio": [rules.TRIGGERS[0]]})
reg.close()

# =====================================================================
for d in _roots:
    shutil.rmtree(d, ignore_errors=True)
print(f"\n{_passed} cases, {len(_failed)} failed")
for f in _failed:
    print("  -", f)
sys.exit(1 if _failed else 0)
