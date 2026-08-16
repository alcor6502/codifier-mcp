#!/usr/bin/env python3
"""The schema suite: what the DATABASE refuses, proved with raw SQL.

Every case in here goes round the engine and writes with sqlite3 directly.
That is the whole point: this file is readable from the share, and a
guarantee that only holds for callers who came through the tools is not a
guarantee, it is a habit. If a check in here can only be made to fail by
calling a method, it belongs in test_collaudo.py, not here.

A refusal has to NAME the culprit. A case that goes red with a traceback, or
with a message that does not say what was refused and how to cure it, is a
control that will be misread the first time it fires in anger — so each case
declares the words it expects to see.

Runs on the standard library alone: no engine, no network, no FastMCP.
"""

import sqlite3
import sys

import rules

NOW = "2026-08-12T18:00:00Z"

_passed = 0
_failed: list[str] = []


def _db():
    """A fresh project database with a little anagrafica already in it.

    Two groups on purpose, sharing one member: `advisory` is in both
    `deliberativi` and `automatismi`, which is the overlap the audience
    photograph has to survive.
    """
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(rules.SCHEMA)
    con.execute(f"PRAGMA user_version = {rules.SCHEMA_GENERATION}")
    c = con.cursor()
    c.execute("INSERT INTO domain (code,description,reason,created_at,actor)"
              " VALUES ('VA','values','the house doctrine',?,'architect')", (NOW,))
    for name, kind in (("architect", "chat"), ("advisory", "chat"),
                       ("news", "skill"), ("Alfredo", "human")):
        c.execute("INSERT INTO consumer (name,kind,created_at,actor)"
                  " VALUES (?,?,?,'architect')", (name, kind, NOW))
    for g in ("deliberativi", "automatismi"):
        c.execute("INSERT INTO consumer_group (name,created_at,actor)"
                  " VALUES (?,?,'architect')", (g, NOW))
    for gm in ((1, 1), (1, 2), (2, 2), (2, 3)):
        c.execute("INSERT INTO consumer_group_member VALUES (?,?)", gm)
    con.commit()
    return con


def _rule(con, reach="all", groups=(), exceptions=(), seq=1,
          status="active", actor="architect"):
    """Write a rule THE WAY THE ENGINE HAS TO: audience first, rule last.

    The rule_id is taken before the insert and the two audience tables carry a
    DEFERRED reference, so that by the time the AFTER INSERT trigger on `rule`
    photographs the perimeter, the perimeter is there. Written the obvious way
    round — rule first, audience after — version 1 of every targeted rule
    photographs nobody and nothing complains.
    """
    c = con.cursor()
    rid = c.execute("SELECT IFNULL(MAX(rule_id),0)+1 FROM rule").fetchone()[0]
    for g in groups:
        c.execute("INSERT INTO rule_audience_group VALUES (?,?)", (rid, g))
    for e in exceptions:
        c.execute("INSERT INTO rule_audience_exception VALUES (?,?)", (rid, e))
    c.execute(
        "INSERT INTO rule (rule_id,domain_id,seq,type,title,body,status,"
        "reach,reason,proposed_by,actor,created_at,updated_at)"
        " VALUES (?,(SELECT domain_id FROM domain WHERE code='VA'),?,"
        "'R','a title','a body',?,?,"
        "'because it was decided','architect',?,?,?)",
        (rid, seq, status, reach, actor, NOW, NOW))
    con.commit()
    return rid


def _task(con, code="TK", seq=1):
    """The kind is a DOMAIN now, and it is looked up by code and never by id:
    the seed owns the first ids, and a number written here would be a fact
    about insertion order rather than about the kind."""
    did = con.execute("SELECT domain_id FROM domain WHERE code=?",
                      (code,)).fetchone()[0]
    con.execute(
        "INSERT INTO task (domain_id,seq,title,body,consumer_id,created_by,urgent,"
        "status,created_at,updated_at)"
        " VALUES (?,?,'a title','a body',1,'architect',0,'pending',?,?)",
        (did, seq, NOW, NOW))


def refused(name, gesture, expect):
    """The gesture must be refused, and the refusal must contain `expect`."""
    global _passed
    con = _db()
    try:
        gesture(con)
        con.commit()
        _failed.append(f"{name}: NOT REFUSED — the database let it through")
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
    finally:
        con.close()


def allowed(name, gesture):
    """The mirror of `refused`: this gesture must go THROUGH.

    A suite made only of refusals drifts one way — every new trigger makes it
    greener — and the day a guard blocks something legitimate nothing goes
    red. That is exactly what happened to `all -> targeted`: two triggers,
    both defensible on their own, made a documented gesture unreachable in
    every possible order, and forty green cases had nothing to say about it.
    """
    global _passed
    con = _db()
    try:
        gesture(con)
        con.commit()
        _passed += 1
        print(f"  ok    {name}")
    except Exception as exc:                       # noqa: BLE001 — any refusal
        _failed.append(f"{name}: REFUSED, and it should not have been — {exc}")
        print(f"  FAIL  {name}: refused — {str(exc)[:70]}")
    finally:
        con.close()


def equals(name, got, want):
    global _passed
    if got == want:
        _passed += 1
        print(f"  ok    {name}")
    else:
        _failed.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


# =====================================================================
print("\n— THE OBJECTS THE SCHEMA DECLARES —")
# The numbers are COUNTED from the code and compared against what the
# database actually holds: neither side is a literal anybody has to keep in
# step with the other.
con = _db()
have = {t: {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type=?", (t,))
            if not r[0].startswith("sqlite_")}
        for t in ("table", "index", "trigger", "view")}
equals("every declared table exists, and no more", have["table"], set(rules.TABLES))
equals("every declared view exists, and no more", have["view"], set(rules.VIEWS))
equals("every guarantee index exists", set(rules.INDEXES) - have["index"], set())
equals("every declared trigger exists, and no more", have["trigger"], set(rules.TRIGGERS))
equals("the database knows its generation",
       con.execute("PRAGMA user_version").fetchone()[0], rules.SCHEMA_GENERATION)
equals("the display ID is computed, never stored",
       "display_id" not in {r[1] for r in con.execute("PRAGMA table_info(rule)")}, True)
con.close()

# =====================================================================
print("\n— THE ANAGRAFICA —")
refused("a domain code is written once",
        lambda c: c.execute("UPDATE domain SET code='XX' WHERE code='VA'"),
        "written once")
refused("a domain with active rules cannot retire",
        lambda c: (_rule(c),
                   c.execute("UPDATE domain SET retired_at=?,retired_reason='done'"
                             " WHERE code='VA'", (NOW,))),
        "active rules")
# The reservation is a ROW now, not a CHECK in the DDL: the unique index on
# the folded code is what refuses the second TK, and that is the point — a
# third kind one day is a row in the seed, not a rewrite of the schema.
refused("TK is reserved for tasks, and lowercase does not sneak past",
        lambda c: c.execute("INSERT INTO domain (code,reason,created_at)"
                            " VALUES ('tk','sneaking in lowercase',?)", (NOW,)),
        "UNIQUE")
for entity, table, pk in (("domain", "domain", "domain_id"),
                          ("consumer", "consumer", "consumer_id"),
                          ("group", "consumer_group", "group_id")):
    refused(f"retiring a {entity} costs a reason",
            lambda c, t=table, k=pk: c.execute(
                f"UPDATE {t} SET retired_at=? WHERE {k}="
                f"(SELECT MAX({k}) FROM {t})", (NOW,)),
            "CHECK")

# =====================================================================
print("\n— THE EXCLUSIVE ARC: reach is declared, never deduced —")
refused("targeted with no audience at all",
        lambda c: _rule(c, "targeted"),
        "at least one")
refused("universal WITH an audience",
        lambda c: _rule(c, "all", groups=(1,)),
        "takes no group")
# THE NARROWING THAT USED TO BE UNREACHABLE. `all -> targeted` is the widest
# narrowing this registry has, and until the two BEFORE INSERT guards on the
# audience tables were dropped it could not be written in ANY order: audience
# first was refused by them, rule first by trg_rule_arc_upd. The gesture is
# documented — rules_amend takes `reach` and narrows "downwards only" — so the
# invariant had made a legal move impossible, and no case said so.
allowed("a universal rule narrowed to a group, in one transaction",
        lambda c: (_rule(c, "all"),
                   c.execute("INSERT INTO rule_audience_group VALUES (1,1)"),
                   c.execute("UPDATE rule SET reach='targeted',updated_at=?,"
                             "event='narrowed to deliberativi' WHERE rule_id=1",
                             (NOW,))))
allowed("a universal rule narrowed to a single exception",
        lambda c: (_rule(c, "all"),
                   c.execute("INSERT INTO rule_audience_exception VALUES (1,1)"),
                   c.execute("UPDATE rule SET reach='targeted',updated_at=?,"
                             "event='narrowed to architect' WHERE rule_id=1",
                             (NOW,))))

# And the price of dropping those two, paid in the open: a row slipped next to
# a live universal rule is no longer refused on the spot. It is INERT while it
# sits there — the photograph trigger reads the audience tables only when
# `reach` is 'targeted' — and the arc catches it at the next write on the
# rule. Late, and on somebody else's gesture; project_status reports it before
# then.
allowed("a row slipped next to a live universal rule goes in, and is inert",
        lambda c: (_rule(c, "all"),
                   c.execute("INSERT INTO rule_audience_group VALUES (1,1)")))
refused("...and the next write on that rule is the one that refuses",
        lambda c: (_rule(c, "all"),
                   c.execute("INSERT INTO rule_audience_group VALUES (1,1)"),
                   c.execute("UPDATE rule SET event='an innocent amendment',"
                             "updated_at=? WHERE rule_id=1", (NOW,))),
        "takes no group")

refused("a rule emptied of its perimeter by an amendment",
        lambda c: (_rule(c, "targeted", groups=(1,)),
                   c.execute("DELETE FROM rule_audience_group WHERE rule_id=1"),
                   c.execute("UPDATE rule SET updated_at=? WHERE rule_id=1", (NOW,))),
        "at least one")
refused("reach flipped to all while the perimeter stays behind",
        lambda c: (_rule(c, "targeted", groups=(1,)),
                   c.execute("UPDATE rule SET reach='all',updated_at=?"
                             " WHERE rule_id=1", (NOW,))),
        "takes no group")

# =====================================================================
print("\n— THE CORPUS —")
refused("two pending proposals cannot claim the same victim",
        lambda c: (_rule(c, "all"),
                   c.execute("INSERT INTO rule (domain_id,seq,type,title,body,"
                             "status,reach,reason,created_at,updated_at,"
                             "supersedes_rule_id) VALUES ("
                             "(SELECT domain_id FROM domain WHERE code='VA'),"
                             "2,'R','a','b','proposed',"
                             "'all','r',?,?,1)", (NOW, NOW)),
                   c.execute("INSERT INTO rule (domain_id,seq,type,title,body,"
                             "status,reach,reason,created_at,updated_at,"
                             "supersedes_rule_id) VALUES (1,3,'R','a','b','proposed',"
                             "'all','r',?,?,1)", (NOW, NOW))),
        "unique")
refused("a citation towards a rule that does not exist",
        lambda c: (_rule(c, "all"),
                   c.execute("INSERT INTO rule_ref VALUES (1,999)")),
        "FOREIGN KEY")
refused("denying without saying why",
        lambda c: (_rule(c, "all"),
                   c.execute("INSERT INTO decision (digest,decided_at)"
                             " VALUES ('abc',?)", (NOW,)),
                   c.execute("INSERT INTO decision_rule VALUES (1,1,'denied',NULL)")),
        "CHECK")
refused("approving WITH a reason — the yes is the tick",
        lambda c: (_rule(c, "all"),
                   c.execute("INSERT INTO decision (digest,decided_at)"
                             " VALUES ('abc',?)", (NOW,)),
                   c.execute("INSERT INTO decision_rule"
                             " VALUES (1,1,'approved','because')")),
        "CHECK")

# =====================================================================
print("\n— NOTHING IS DELETED, AND THE HISTORY IS WHY —")
# There is no trg_*_del safety net any more, and there is no need of one: the
# version tables reference their entity without ON DELETE CASCADE, a version
# row always exists, so the DELETE is refused by the database itself. "No
# DELETE" stopped being a doctrine and became a property.
refused("a rule the history points at",
        lambda c: (_rule(c, "all"), c.execute("DELETE FROM rule WHERE rule_id=1")),
        "FOREIGN KEY")
refused("a consumer the history points at",
        lambda c: c.execute("DELETE FROM consumer WHERE consumer_id=1"),
        "FOREIGN KEY")
refused("a domain the history points at",
        lambda c: c.execute("DELETE FROM domain WHERE domain_id=1"),
        "FOREIGN KEY")
refused("a group the history points at",
        lambda c: c.execute("DELETE FROM consumer_group WHERE group_id=1"),
        "FOREIGN KEY")

# =====================================================================
print("\n— THE PEOPLE (rev. 5.0.0) —")
# These four are the ones the mandate asked to be proved BY HAND with sqlite3
# and not from the engine. The engine refuses all four with a better sentence,
# but a guarantee that lives only in Python is a guarantee the sqlite3 shell on
# the server walks straight past — and root opening the file by hand is a
# documented, legitimate road out of a lost key. So the schema holds them.
refused("an email on a CHAT",
        lambda c: c.execute("UPDATE consumer SET email='x@y.co' WHERE kind='chat'"),
        "CHECK")
refused("an email on a SKILL",
        lambda c: c.execute("UPDATE consumer SET email='x@y.co' WHERE kind='skill'"),
        "CHECK")
allowed("and on a HUMAN it goes in",
        lambda c: c.execute("UPDATE consumer SET email='alfredo@example.com'"
                            " WHERE kind='human'"))
refused("a brief on a HUMAN",
        lambda c: c.execute("UPDATE consumer SET brief='x' WHERE kind='human'"),
        "CHECK")
refused("and specs on a HUMAN",
        lambda c: c.execute("UPDATE consumer SET specs='x' WHERE kind='human'"),
        "CHECK")
refused("a human created WITH a brief in the same INSERT — the check is on the "
        "row and not on the update",
        lambda c: c.execute("INSERT INTO consumer (name,kind,brief,created_at,actor)"
                            " VALUES ('Marta','human','x',?,'architect')", (NOW,)),
        "CHECK")
refused("the approver flag on a chat",
        lambda c: c.execute("UPDATE consumer SET approver=1 WHERE kind='chat'"),
        "CHECK")
refused("and a value that is neither 0 nor 1",
        lambda c: c.execute("UPDATE consumer SET approver=2 WHERE kind='human'"),
        "CHECK")
allowed("one approver, on a human",
        lambda c: c.execute("UPDATE consumer SET approver=1 WHERE kind='human'"))
# THE ONE THAT NEEDS TWO HUMANS, and it is the case the partial unique index
# exists for: a count in Python is a race — two threads read zero and both
# write one — and this is a guarantee about a project, which is a file several
# hands reach.
refused("a SECOND approver in the same project",
        lambda c: (c.execute("INSERT INTO consumer (name,kind,created_at,actor)"
                             " VALUES ('Marta','human',?,'architect')", (NOW,)),
                   c.execute("UPDATE consumer SET approver=1"
                             " WHERE kind='human'")),
        "UNIQUE")
allowed("while any number of humans WITHOUT the flag is fine: the index is "
        "partial, so the zeros do not collide",
        lambda c: (c.execute("INSERT INTO consumer (name,kind,created_at,actor)"
                             " VALUES ('Marta','human',?,'architect')", (NOW,)),
                   c.execute("INSERT INTO consumer (name,kind,created_at,actor)"
                             " VALUES ('Giulia','human',?,'architect')", (NOW,))))

# =====================================================================
print("\n— THE TASK LOG —")
refused("completed with no outcome",
        lambda c: (_task(c),
                   c.execute("UPDATE task SET status='completed',closed_at=?"
                             " WHERE task_id=1", (NOW,))),
        "CHECK")
refused("dropped with no reason",
        lambda c: (_task(c),
                   c.execute("UPDATE task SET status='dropped',closed_at=?"
                             " WHERE task_id=1", (NOW,))),
        "CHECK")
refused("closed is closed",
        lambda c: (_task(c),
                   c.execute("UPDATE task SET status='completed',outcome='done',"
                             "closed_at=?,updated_at=? WHERE task_id=1", (NOW, NOW)),
                   c.execute("UPDATE task SET title='rewritten',updated_at=?"
                             " WHERE task_id=1", (NOW,))),
        "not amended")
refused("urgency cleared by whoever receives it",
        lambda c: (_task(c),
                   c.execute("UPDATE task SET urgent=1,updated_at=?"
                             " WHERE task_id=1", (NOW,))),
        "frozen field")
refused("archiving a task that is still open",
        lambda c: (_task(c),
                   c.execute("UPDATE task SET archived_at=?,updated_at=?"
                             " WHERE task_id=1", (NOW, NOW))),
        "still pending")

# =====================================================================
print("\n— THE ONE-TIME CODES —")
refused("spent, without naming the gesture that spent it",
        lambda c: c.execute("INSERT INTO auth_code (code_hash,minted_at,"
                            "expires_at,spent_at) VALUES ('h',?,?,?)",
                            (NOW, NOW, NOW)),
        "CHECK")
refused("burning a code that is already spent",
        lambda c: (c.execute("INSERT INTO auth_code (code_hash,minted_at,"
                             "expires_at,spent_at,spent_action)"
                             " VALUES ('h',?,?,?,'rules_retire')", (NOW, NOW, NOW)),
                   c.execute("UPDATE auth_code SET spent_at=? WHERE code_id=1",
                             (NOW,))),
        "already spent")

# =====================================================================
print("\n— THE PHOTOGRAPH —")
con = _db()
rid = _rule(con, "targeted", groups=(1, 2))
equals("version 1 of a targeted rule photographs its perimeter",
       con.execute("SELECT consumer_id,via_group_id FROM rule_version_audience"
                   " WHERE version=1 ORDER BY 1").fetchall(),
       [(1, 1), (2, 1), (3, 2)])
# advisory (2) is in both groups: ONE row, and it carries the lowest group.
# The snapshot answers WHO was reached; the door can legitimately be plural.
equals("a consumer reached by two groups gets one row",
       con.execute("SELECT COUNT(*) FROM rule_version_audience"
                   " WHERE version=1 AND consumer_id=2").fetchone()[0], 1)
con.close()

con = _db()
_rule(con, "all")
equals("a universal rule photographs everyone alive",
       con.execute("SELECT COUNT(*) FROM rule_version_audience"
                   " WHERE version=1 AND via_group_id IS NULL").fetchone()[0], 4)
con.close()

# The overlap that forms LATER is deliberately not blocked — anagrafica does
# not pay for a defect that lives in a rule — so it WILL be sitting there at
# photograph time. The exception was declared by hand, so it keeps the row.
# Without that, a legal write would abort on a primary key.
con = _db()
rid = _rule(con, "targeted", groups=(2,), exceptions=(1,))
con.execute("INSERT INTO consumer_group_member VALUES (2,1)")
con.execute("UPDATE rule SET event='after the overlap',actor='architect',"
            "updated_at=? WHERE rule_id=?", (NOW, rid))
con.commit()
equals("an overlap formed later does not abort the photograph, "
       "and the exception keeps its row",
       con.execute("SELECT consumer_id,via_group_id FROM rule_version_audience"
                   " WHERE version=2 ORDER BY 1").fetchall(),
       [(1, None), (2, 2), (3, 2)])
con.close()

# =====================================================================
print("\n— THE STORY: DATED, AND SIGNED —")
con = _db()
rid = _rule(con, "all", status="proposed", actor="advisory")
for status, event, actor in (("active", "approved in batch 3", "web ui"),
                             ("active", "a typo in the title", "architect"),
                             ("retired", "superseded by (VA-0002)", "architect")):
    con.execute("UPDATE rule SET status=?,event=?,actor=?,updated_at=?"
                " WHERE rule_id=?", (status, event, actor, NOW, rid))
con.commit()
equals("the verbs the database can derive, it derives",
       con.execute("SELECT version,action,actor FROM rule_version"
                   " ORDER BY version").fetchall(),
       [(1, "created", "advisory"), (2, "approved", "web ui"),
        (3, "amended", "architect"), (4, "retired", "architect")])
equals("the display ID is built from the domain code and the number",
       con.execute("SELECT display_id FROM v_rule").fetchone()[0], "VA-0001")
# The new spelling is ONE WORD, like every consumer name since `RE_NAME`
# narrowed: this file writes raw SQL and would take a space happily, but a
# literal here that the engine refuses reads as an endorsement of it.
con.execute("UPDATE consumer SET name='advisor',actor='architect'"
            " WHERE consumer_id=2")
con.commit()
equals("a rename is named, and the snapshot keeps the old spelling",
       con.execute("SELECT name,action,actor FROM consumer_version"
                   " WHERE consumer_id=2 ORDER BY version").fetchall(),
       [("advisory", "created", "architect"),
        ("advisor", "renamed", "architect")])
con.close()

# =====================================================================
print(f"\n{_passed} cases, {len(_failed)} failed")
for f in _failed:
    print("  -", f)
sys.exit(1 if _failed else 0)
