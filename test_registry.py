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


def hides(name, gesture, secrets_):
    """The gesture must be refused, and the refusal must NOT contain any of
    `secrets_`. The other half of `refused`: that one pins what a message has
    to say, this one pins what it must not — and a message is the one place a
    secret leaves the process without anybody deciding that it should."""
    global _passed
    try:
        gesture()
        _failed.append(f"{name}: NOT REFUSED — the router let it through")
        print(f"  FAIL  {name}: not refused")
        return
    except Exception as exc:                       # noqa: BLE001 — any refusal
        msg = str(exc)
    leaked = [s for s in secrets_ if s and s in msg]
    if leaked:
        # The leaked value is NOT printed here either: this file's output is
        # read in a terminal and pasted into chats like any other log.
        _failed.append(f"{name}: the refusal carries {len(leaked)} secret(s) it "
                       f"was handed")
        print(f"  FAIL  {name}: {len(leaked)} secret(s) in the refusal")
    else:
        _passed += 1
        print(f"  ok    {name}")


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


def gesture(name, fn):
    """A gesture that must go THROUGH. The mirror of `refused`, and it exists
    for the same reason `yields` does: a guard taken away must come back as a
    red line, and a bare call in the body of this file comes back as a
    traceback that stops every case after it."""
    global _passed
    try:
        fn()
    except Exception as exc:                       # noqa: BLE001
        _failed.append(f"{name}: refused — {exc}")
        print(f"  FAIL  {name}: refused — {str(exc)[:70]}")
        return
    _passed += 1
    print(f"  ok    {name}")


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

# The refusal above is the ONE that used to quote the offending line whole —
# and two of its three fields are the project's codes, in clear. The message
# goes to stdout at boot, so it lands in the container's log and from there
# into whatever gets pasted when help is asked for. A diagnostic message must
# never carry the material it is describing when that material is a secret.
#
# The count is the thing that is wrong, so the SHAPE diagnoses it whole: a
# separator lost between the name and the reference code shows up as one field
# far too long. Both directions are checked here — the codes are gone AND the
# lengths arrived — because a message stripped of everything would be a
# refusal nobody can act on, which is the failure mode of curing this badly.
r = _root()
_write(r, f"Financial Portfolio {REF} | {ADM}\n")
hides("a malformed line does not put the codes in the log",
      lambda: rules.Registry(r), (REF, ADM))
refused("and it says the line, the count and the SHAPE instead",
        lambda: rules.Registry(r), "chars]")
refused("so a lost separator is visible as one field far too long",
        lambda: rules.Registry(r), f"[{len('Financial Portfolio ' + REF)} chars]")

# The other three doors were already clean, and they stay checked: once the
# count is right, `parts[0]` IS the name and quoting it is quoting a name.
r = _root()
_write(r, f"Financial Portfolio | {REF} | short\n")
hides("a code that is not 8-32 characters is refused without printing any code",
      lambda: rules.Registry(r), (REF,))
r = _root()
_write(r, _line() + f"Palestra | {REF} | {ADM}\n")
hides("a code written twice is refused with the LINE NUMBER, not the code",
      lambda: rules.Registry(r), (REF, ADM))
refused("and that refusal points at the line the twin is on",
        lambda: rules.Registry(r), "line 1")

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

# The other side of `RE_NAME` narrowing to one word, and it is written here as
# its own case because it is the thing that narrowing could take away in
# silence: a PROJECT name has spaces BY DESIGN — the folder is the name as it
# is spelled and the file is the slug derived from it. `Financial Portfolio`
# is used all over this suite, but used is not proved: nothing said out loud
# that the space was legal, so nothing would have gone red when it stopped
# being.
r = _root()
_write(r, _line(name="Financial Portfolio"))
gesture("a PROJECT name may hold spaces — it is not a consumer name",
        lambda: rules.Registry(r))
yields("and the name is served whole, spaces and spelling untouched",
       lambda: rules.Registry(r).projects()["projects"][0]["name"],
       "Financial Portfolio")

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
print("\n— THE ADMIN GATE: A PAIR, AND ONE ANSWER FOR BOTH HALVES —")
r = _root()
_write(r, _line())
reg = rules.Registry(r)
yields("the pair opens administration",
       lambda: rules.check_admin(reg, REF, ADM).name, "Financial Portfolio")
bad_key = bad_code = ""
try:
    rules.check_admin(reg, REF, "wrongadmincode1")
except rules.RulesError as exc:
    bad_key = str(exc)
try:
    rules.check_admin(reg, "wrongrefcode001", ADM)
except rules.RulesError as exc:
    bad_code = str(exc)
equals("a wrong admin code and a wrong project code get the SAME answer",
       (bad_key == bad_code, "admin code" in bad_key), (True, True))
refused("an admin code left empty is not a default",
        lambda: rules.check_admin(reg, REF, ""), "admin code")
# A registry that will not parse must NOT come back as "wrong code": that is
# how an evening goes into retyping credentials at a broken file.
_write(r, "Financial Portfolio | broken\n")
refused("a broken registry is not a wrong password, and says so",
        lambda: rules.check_admin(reg, REF, ADM), "line 1")
_write(r, _line())

# =====================================================================
print("\n— THE ONE-TIME CODE: MINTED, SPENT ONCE, ROLLED BACK ON A REFUSAL —")
prj = reg.project(REF)
minted = prj.mint_auth_code()
equals("a code is minted with the default life", minted["minutes"],
       rules.DEFAULT_AUTH_CODE_MINUTES)
equals("what is stored is the hash, never the code",
       prj.cx.execute("SELECT COUNT(*) FROM auth_code WHERE code_hash=?",
                      (minted["auth_code"],)).fetchone()[0], 0)
yields("and the page can see it live", lambda: prj.auth_codes()["count_live"], 1)
refused("a code that would not survive the walk from page to chat is refused",
        lambda: prj.mint_auth_code(0.5), "less than a minute")

# ADMIN_AUTH_CODE_DURATION is read once, at boot, in server.py — like every
# other piece of configuration — and travels down. The page shows it, so the
# person minting knows how long they have.
_r2 = _root()
_write(_r2, _line())
_reg2 = rules.Registry(_r2, auth_code_minutes=17)
yields("the life of a code is decided at boot, not by a constant in here",
       lambda: _reg2.project(REF).mint_auth_code()["minutes"], 17)
yields("and the maintenance page is told what it will hand out",
       lambda: _reg2.project(REF).auth_codes()["default_minutes"], 17)
_reg2.close()

refused("no code at all is answered with where to get one",
        lambda: rules.check_auth_code(prj, "", "rules_retire"), "maintenance page")
refused("a code from nowhere is refused, and the count of live ones is given",
        lambda: rules.check_auth_code(prj, "notacodeatall", "rules_retire"),
        "not one of this project's")

# The refusal rolls back: an error further down the gesture must not cost a
# trip to the page. This is the whole meaning of "burned in the same
# transaction as the SUCCEEDED gesture".


def _gesture_that_fails():
    with prj._transaction():
        rules.check_auth_code(prj, minted["auth_code"], "rules_retire")
        raise rules.RulesError("the gesture itself is refused")


def _gesture_that_works():
    with prj._transaction():
        rules.check_auth_code(prj, minted["auth_code"], "rules_retire")


refused("the burn happens first, and the gesture after it can still refuse",
        _gesture_that_fails, "the gesture itself is refused")
yields("a gesture refused after the burn leaves the code alive",
       lambda: prj.auth_codes()["count_live"], 1)

gesture("the gesture that succeeds spends it", _gesture_that_works)
yields("and the code leaves the live ones", lambda: prj.auth_codes()["count_live"], 0)
equals("the spent row is the audit: what spent it, and when",
       [(r["spent_action"], bool(r["spent_at"])) for r in prj.auth_codes()["spent"]],
       [("rules_retire", True)])
# The expected words are the FUNCTION'S, not the trigger's. An injection
# proved why: with the check taken out of check_auth_code the case stayed
# green, because the schema refused the second burn on its own and its message
# also says "already spent". Two guarantees are wanted here — the file is
# readable from the share — but a case has to name which of them it is
# measuring, or removing one of the two is free.
refused("a code spent is nothing to the gate, even inside its minutes",
        lambda: rules.check_auth_code(prj, minted["auth_code"], "rules_retire"),
        "one code, one gesture")

# Expiry is not the same refusal as spending, and the difference is the cure:
# one says mint another, the other says go and find what spent it.
stale = prj.mint_auth_code(1)
prj.cx.execute("UPDATE auth_code SET expires_at='2020-01-01T00:00:00Z' "
               "WHERE spent_at IS NULL")
refused("a code past its minutes is refused, and the date is in the message",
        lambda: rules.check_auth_code(prj, stale["auth_code"], "rules_retire"),
        "expired on 2020-01-01")

# The one-time-ness lives in the DATABASE too, not only in the function that
# checks it: this file is readable from the share.
refused("a second burn is refused by the schema, function or no function",
        lambda: prj.cx.execute(
            "UPDATE auth_code SET spent_at=?, spent_action='by hand' "
            "WHERE spent_at IS NOT NULL", (rules._now(),)),
        "already spent")

# =====================================================================
print("\n— THE UI PASSWORD OPENS NO TOOL, AND NO EMPTY DOOR —")
equals("the password compares equal to itself",
       rules.check_web("a long password", "a long password"), True)
equals("and to nothing else", rules.check_web("a long passworD", "a long password"),
       False)
equals("a password that was never configured opens NOTHING, empty included",
       (rules.check_web("", ""), rules.check_web("anything", "")), (False, False))

# =====================================================================
print("\n— THE CEILING IS THE PROJECT'S, NOT THE CONTAINER'S —")
equals("a project with no profile row yet has no ceiling", prj.queue_cap(), None)
prj.cx.execute("INSERT INTO project_profile (profile_id,queue_cap,updated_at) "
               "VALUES (1,?,?)", (3, rules._now()))
yields("and once written, the ceiling is read from the project", prj.queue_cap, 3)
prj.cx.execute("UPDATE project_profile SET queue_cap=0 WHERE profile_id=1")
yields("zero is a closed queue, and it is not the same as no ceiling",
       prj.queue_cap, 0)
reg.close()

# =====================================================================
print("\n— NO REFUSAL IN THE PARSER CAN REACH THE LINE IT IS ABOUT —")

# The cases above pin the messages that exist TODAY. This one pins the next
# one somebody writes, and it is archivist's half of the same cure: the raw
# line stays in scope for the whole loop body, so a branch added tomorrow can
# interpolate it back without anybody noticing — which is exactly how it got
# there the first time.
#
# From the AST, not from a text search: `f"…{line}…"` and `"…".format(line)`
# and `% line` are three spellings of one mistake, and a search for `{line!r}`
# catches one of them. What is forbidden is the CONTENT — the raw line, the
# split fields, the two codes. What is allowed is everything a person needs:
# the line NUMBER, the file, the name once the count is right, and the shape.
import ast                                                          # noqa: E402

_FORBIDDEN = {"line", "raw", "parts", "p", "ref", "adm"}
_src = open(os.path.join(os.path.dirname(os.path.abspath(rules.__file__)),
                         "rules.py"), encoding="utf-8").read()
_fn = next((n for n in ast.walk(ast.parse(_src))
            if isinstance(n, ast.FunctionDef) and n.name == "_registry_lines"), None)
_present = _fn is not None
equals("_registry_lines is where the registry is parsed, and it is found", _present, True)

_leaks: list[str] = []
if _present:
    for _n in ast.walk(_fn):
        # Every string a message can be built from: an f-string, a .format(),
        # and the % operator. A name that is forbidden may not reach any of
        # the three.
        _names: list[str] = []
        if isinstance(_n, ast.JoinedStr):
            _names = [x.id for v in _n.values if isinstance(v, ast.FormattedValue)
                      for x in ast.walk(v.value) if isinstance(x, ast.Name)]
        elif isinstance(_n, ast.Call) and isinstance(_n.func, ast.Attribute) \
                and _n.func.attr == "format":
            _names = [x.id for a in _n.args for x in ast.walk(a) if isinstance(x, ast.Name)]
        elif isinstance(_n, ast.BinOp) and isinstance(_n.op, ast.Mod) \
                and isinstance(_n.left, (ast.Constant, ast.JoinedStr)):
            _names = [x.id for x in ast.walk(_n.right) if isinstance(x, ast.Name)]
        _leaks += [f"{ast.unparse(_n)[:40]} carries {x}" for x in _names if x in _FORBIDDEN]

equals("no message it builds can carry the line, the fields or the codes",
       (_leaks or ["clean"]), ["clean"])

# The other direction, or the check above is satisfied by a parser that says
# nothing at all: the line NUMBER must still reach the messages, because that
# is the whole of what the person needs in order to act.
_uses_n = False
if _present:
    for _n in ast.walk(_fn):
        if isinstance(_n, ast.JoinedStr):
            _uses_n = _uses_n or any(
                isinstance(x, ast.Name) and x.id == "n"
                for v in _n.values if isinstance(v, ast.FormattedValue)
                for x in ast.walk(v.value))
equals("and the line NUMBER does reach them", _uses_n, True)

# =====================================================================
for d in _roots:
    shutil.rmtree(d, ignore_errors=True)
print(f"\n{_passed} cases, {len(_failed)} failed")
for f in _failed:
    print("  -", f)
sys.exit(1 if _failed else 0)
