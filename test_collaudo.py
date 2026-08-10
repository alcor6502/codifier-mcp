#!/usr/bin/env python3
"""
test_collaudo.py — the whole engine, refusals included.

One suite, deliberately: two suites over the same engine are two numbers that
drift apart, and the drift is never noticed until it matters. This absorbs the
smoke run and the v4.0 cases, and adds the ones the consumers/scopes model
brought with it.

No network, no FastMCP, no Docker: this is the layer where the real bugs live.
Run it with `python3 test_collaudo.py`. Exit code 0 means green.

The four proofs that carry the model, if you only read four:
  · history written BEFORE a scope changes still reports the consumers of that
    day (a version is a photograph, not a pointer);
  · _ALL_ reaches a consumer created AFTER the rule;
  · a managed scope refuses a second member, and the refusal comes from the
    DATABASE, not from tool code;
  · add a consumer and the reading order stays right with nobody touching
    anything — which is what "the order is computed" means.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rules import (ALL, FILE_MODE, MAX_BODY_BYTES, MAX_GET_IDS,
                   Registry, RulesError, VERSION, _plus_days)

# =====================================================================
# Harness
# =====================================================================

OK = FAIL = 0
FAILURES: list[str] = []


def head(title: str) -> None:
    print(f"\n== {title} ==")


def ok(cond, label: str, extra="") -> None:
    """One assertion, one line. `extra` is printed only when it fails, so a
    green run stays readable and a red one says enough to act."""
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        FAILURES.append(label)
        print(f"  FAIL  {label}  {extra}")


def case(label: str, fn) -> None:
    """For a block of assertions: any exception is the failure."""
    global OK, FAIL
    try:
        fn()
        OK += 1
        print(f"  PASS  {label}")
    except Exception as e:
        import traceback
        FAIL += 1
        FAILURES.append(label)
        line = traceback.extract_tb(e.__traceback__)[-1]
        print(f"  FAIL  {label}: {type(e).__name__}: {e}")
        print(f"        at line {line.lineno}: {line.line}")


def refuses(label: str, fn, fragment: str = "", kind=Exception) -> None:
    """A refusal is a feature: it must happen, and it must SAY the right thing.
    Checking the message matters — a talking error that stops talking is a
    regression the caller pays for, not us."""
    global OK, FAIL
    try:
        fn()
    except kind as e:
        if fragment and fragment.lower() not in str(e).lower():
            FAIL += 1
            FAILURES.append(label)
            print(f"  FAIL  {label}: refused, but said {str(e)[:90]!r}")
            return
        OK += 1
        print(f"  PASS  {label}  ({str(e).splitlines()[0][:58]})")
        return
    FAIL += 1
    FAILURES.append(label)
    print(f"  FAIL  {label}: did NOT refuse")


D = tempfile.mkdtemp(prefix="collaudo-")
DB = os.path.join(D, "rules.db")
R = Registry(DB, provisional_days=90)

FP, HT, CASA = "Fp7m2Qx91Ab", "Ht4Rn8Wq02zz", "Ca6Hj3Lv77xy"
NAME_FP, NAME_HT, NAME_CASA = "Financial Portfolio", "Health Tracking", "Casa"

# =====================================================================
head("projects: the code is the only door")
# =====================================================================

refuses("missing code: a blind error",
        lambda: R.list_rules("", "architect"), "project not specified", RulesError)
refuses("invented code: the SAME error, no hint",
        lambda: R.list_rules("Invented99", "architect"), "project not specified", RulesError)

case("create Financial Portfolio", lambda: R.create_project(
    FP, NAME_FP,
    [("architect", "chat"), ("advisory", "chat"), ("alt-funds", "chat"),
     ("tax", "chat"), ("market-news", "chat"), ("update-tax", "skill")],
    {"VA": "vault and files", "ST": "structure", "RL": "roles",
     "VE": "verification", "FI": "tax", "PE": "perimeter"},
    "the historical project"))

case("create Health Tracking (different consumers)", lambda: R.create_project(
    HT, NAME_HT, [("architect", "chat"), ("coach", "chat")],
    {"VA": "vault", "MS": "measures"}))

refuses("the NAME is not an access key",
        lambda: R.list_rules(NAME_FP, "architect"), "project not specified", RulesError)
refuses("one project's code, another's consumer",
        lambda: R.list_rules(FP, "coach"), "unknown consumer", RulesError)
refuses("duplicate project name",
        lambda: R.create_project("Aa11Bb22Cc", NAME_FP, ["x"], {"AA": ""}),
        "already exists", RulesError)
refuses("duplicate code",
        lambda: R.create_project(FP, "Other", ["x"], {"AA": ""}),
        "already in use", RulesError)
refuses("short code",
        lambda: R.create_project("abc", "Other", ["x"], {"AA": ""}), "8 to 32", RulesError)
refuses("code with symbols",
        lambda: R.create_project("ab-cd-ef-gh", "Other", ["x"], {"AA": ""}), "8 to 32", RulesError)
refuses("no consumers",
        lambda: R.create_project("Qq11Ww22Ee", "Empty", [], {"AA": ""}),
        "at least one consumer", RulesError)
refuses("no domains",
        lambda: R.create_project("Qq11Ww22Ee", "Empty", ["a"], {}),
        "at least one domain", RulesError)
refuses("malformed domain",
        lambda: R.create_project("Qq11Ww22Ee", "Empty", ["a"], {"vault": ""}),
        "two uppercase", RulesError)
refuses("'*' is not a consumer name",
        lambda: R.create_project("Qq11Ww22Ee", "Empty", ["*"], {"AA": ""}),
        "invalid consumer name", RulesError)
refuses("'_ALL_' is not a consumer name",
        lambda: R.create_project("Qq11Ww22Ee", "Empty", [ALL], {"AA": ""}),
        "invalid consumer name", RulesError)
refuses("unknown kind refused",
        lambda: R.create_project("Qq11Ww22Ee", "Empty", [("a", "person")], {"AA": ""}),
        "it must be one of", RulesError)


def project_info_says_enough_and_no_more():
    i = R.project_info(FP)
    assert i["project"] == NAME_FP
    names = {c["name"] for c in i["consumers"]}
    assert "tax" in names and "update-tax" in names
    assert {c["kind"] for c in i["consumers"]} == {"chat", "skill"}
    assert "VA" in i["domains"]
    assert i["registry_version"] == VERSION
    assert "code" not in i, "the code is not echoed back"
    assert "Health" not in repr(i), "it must not name another project"


case("project_info: consumers, scopes, domains — and no other project",
     project_info_says_enough_and_no_more)

# =====================================================================
head("the database makes the singletons, and defends them")
# =====================================================================


def singletons_exist():
    scopes = {s["name"]: s for s in R.project_info(FP)["scopes"]}
    assert set(scopes) == {ALL, "architect", "advisory", "alt-funds", "tax",
                           "market-news", "update-tax"}, sorted(scopes)
    assert all(s["managed"] for s in scopes.values()), "every scope born so far is managed"
    assert scopes["tax"]["members"] == ["tax"]


case("a trigger gives every consumer a scope of its own", singletons_exist)

ok(R._breadth(NAME_FP, ALL) == 6, "_ALL_ is worth 6 consumers", R._breadth(NAME_FP, ALL))
case("a group scope is created by hand",
     lambda: R.create_scope(FP, "deliberativi", ["architect", "advisory", "alt-funds", "tax"]))
ok(R._breadth(NAME_FP, "deliberativi") == 4, "deliberativi is worth 4")

refuses("managed scope refuses a second member — FROM THE DATABASE",
        lambda: R.cx.execute("INSERT INTO scope_members (project, scope, consumer) "
                             "VALUES (?,?,?)", (NAME_FP, "tax", "advisory")),
        "managed scope", sqlite3.IntegrityError)
refuses("a managed scope is not renamed — FROM THE DATABASE",
        lambda: R.cx.execute("UPDATE scopes SET name='x' WHERE project=? AND name='tax'",
                             (NAME_FP,)), "managed scope", sqlite3.IntegrityError)
refuses("a managed scope's membership is not moved — FROM THE DATABASE",
        lambda: R.cx.execute("UPDATE scope_members SET consumer='advisory' "
                             "WHERE project=? AND scope='tax'", (NAME_FP,)),
        "managed scope", sqlite3.IntegrityError)
refuses("a consumer is not renamed — FROM THE DATABASE",
        lambda: R.cx.execute("UPDATE consumers SET name='x' WHERE project=? AND name='tax'",
                             (NAME_FP,)), "not renamed", sqlite3.IntegrityError)
refuses("a scope with one member adds nothing",
        lambda: R.create_scope(FP, "alone", ["tax"]), "fewer than two", RulesError)
refuses("a scope cannot take a consumer's name",
        lambda: R.create_scope(FP, "tax", ["architect", "advisory"]),
        "already exists", RulesError)
refuses("a duplicate group scope",
        lambda: R.create_scope(FP, "deliberativi", ["architect", "tax"]),
        "already exists", RulesError)
refuses("a group cannot hold a consumer of another project",
        lambda: R.create_scope(FP, "mixed", ["architect", "coach"]),
        "unknown consumer", RulesError)
refuses("edit_scope refuses a managed scope",
        lambda: R.edit_scope(FP, "tax", add=["advisory"]), "managed scope", RulesError)
refuses("edit_scope on a scope that is not there",
        lambda: R.edit_scope(FP, "ghosts", add=["tax"]), "no scope named", RulesError)

# =====================================================================
head("proposing: a proposal reaches nobody, and the NUMBER is not yours")
# =====================================================================

# Nothing below hard-codes an ID: every one of them is what the registry HANDED
# BACK. That is not tidiness, it is the point — a suite that typed the numbers
# in would still pass with the counter removed.
VA1 = R.propose(
    FP, "VA", "R", "Re-read the sources",
    "SOURCE data is re-read right before writing the derivative.",
    ["*"], "initial import", "architect")["id"]
PE1 = R.propose(
    FP, "PE", "M", "The method of the four", "The four deliberative chats agree first.",
    ["deliberativi"], "initial import", "architect")["id"]
FI1 = R.propose(
    FP, "FI", "M", "Estimating the bracket",
    "The bracket is estimated from the rollup by character.",
    ["tax"], "initial import", "tax")["id"]
ST1 = R.propose(
    FP, "ST", "F", "Where the vault lives", "The vault root is read, never assumed.",
    ["update-tax"], "initial import", "architect")["id"]

ok([VA1, PE1, FI1, ST1] == ["VA-0001", "PE-0001", "FI-0001", "ST-0001"],
   "the database assigns the number: four digits, one counter per domain",
   [VA1, PE1, FI1, ST1])
ok(R.list_rules(FP, "tax")["count"] == 0, "a proposal reaches nobody before approval")
ok(R.pending(FP, "tax")["waiting"][0]["id"] == FI1,
   "the noticeboard shows the consumer's own proposal")
ok(len(R.pending(FP)["waiting"]) == 4, "without a consumer the noticeboard shows them all")


def the_number_cannot_be_asked_for():
    """The guarantee is an ABSENCE, so it is worth proving from the outside:
    there is no parameter that carries a number, under any name."""
    import inspect
    params = list(inspect.signature(R.propose).parameters)
    assert "rid" not in params and "id" not in params and "seq" not in params, params
    assert params[1] == "domain", params


case("propose() has no way to receive a number", the_number_cannot_be_asked_for)


refuses("an undeclared domain",
        lambda: R.propose(FP, "ZZ", "R", "x", "y", ["*"], "m"), "not declared", RulesError)
refuses("another project's domain",
        lambda: R.propose(FP, "MS", "R", "x", "y", ["*"], "m"), "not declared", RulesError)
refuses("no domain at all",
        lambda: R.propose(FP, "", "R", "x", "y", ["*"], "m"), "needs a DOMAIN", RulesError)
refuses("a whole ID passed where the domain goes",
        lambda: R.propose(FP, "VA-0003", "R", "x", "y", ["*"], "m"), "not declared", RulesError)
refuses("another project's consumer as a scope",
        lambda: R.propose(FP, "VA", "R", "x", "y", ["coach"], "m"),
        "neither a consumer nor a scope", RulesError)
refuses("type X",
        lambda: R.propose(FP, "VA", "X", "x", "y", ["*"], "m"),
        "R binding, M method", RulesError)
refuses("no reason",
        lambda: R.propose(FP, "VA", "R", "x", "y", ["*"], ""), "reason is mandatory", RulesError)
refuses("no title",
        lambda: R.propose(FP, "VA", "R", "", "y", ["*"], "m"), "needs a title", RulesError)
refuses("no body",
        lambda: R.propose(FP, "VA", "R", "x", "", ["*"], "m"), "needs a body", RulesError)
refuses("empty perimeter",
        lambda: R.propose(FP, "VA", "R", "x", "y", [], "m"), "reaches nobody", RulesError)
refuses("a body over the ceiling",
        lambda: R.propose(FP, "VA", "R", "x", "z" * (MAX_BODY_BYTES + 1), ["*"], "m"),
        "split the rule", RulesError)


refuses("proposed_by must be a consumer",
        lambda: R.propose(FP, "VA", "R", "x", "y", ["*"], "m", "alfredo"),
        "unknown consumer", RulesError)


# A project of its own for the counter, so that proving how numbers are spent
# does not litter the one the rest of the suite reasons about.
CNT = "Cn7t3Rq88zz"
R.create_project(CNT, "Counter", [("architect", "chat")], {"VA": "vault", "ZZ": "other"})


def the_counter_does_not_skip_and_does_not_go_back():
    a = R.propose(CNT, "VA", "R", "One", "Body one.", ["*"], "m")["id"]
    assert a == "VA-0001", a
    # A refusal happens BEFORE the insert, so the counter does not move: if it
    # did, the numbering would carry a scar for every typo.
    try:
        R.propose(CNT, "VA", "X", "Bad type", "Body.", ["*"], "m")
    except RulesError:
        pass
    b = R.propose(CNT, "VA", "R", "Two", "Body two.", ["*"], "m")["id"]
    assert b == "VA-0002", b
    # Denying is different from refusing: the row STAYS, the number is spent,
    # and the counter carries on past it. That is what "never reused" means.
    R.deny(CNT, [b], "spent on purpose")
    c = R.propose(CNT, "VA", "R", "Three", "Body three.", ["*"], "m")["id"]
    assert c == "VA-0003", c
    R.approve(CNT, R.batch(CNT)["digest"])
    R.retire(CNT, c, reason="retired to spend the number")
    d = R.propose(CNT, "VA", "R", "Four", "Body four.", ["*"], "m")["id"]
    assert d == "VA-0004", d
    # And the domains count separately.
    assert R.propose(CNT, "ZZ", "R", "Elsewhere", "Body.", ["*"], "m")["id"] == "ZZ-0001"


case("the counter: no skips, no reuse, one per domain",
     the_counter_does_not_skip_and_does_not_go_back)


def a_full_domain_says_so():
    """Four digits is a ceiling, not infinity. When a domain reaches it the
    answer is a NEW DOMAIN, never a reused number — said out loud, because the
    day it happens there is no remedy left to invent."""
    R.cx.execute("UPDATE rules SET seq=9999 WHERE project='Counter' AND domain='ZZ'")
    try:
        R.propose(CNT, "ZZ", "R", "One too many", "Body.", ["*"], "m")
        raise AssertionError("it should have refused")
    except RulesError as e:
        assert "burned all" in str(e), e


case("a domain that has burned all its numbers asks for a new domain",
     a_full_domain_says_so)


def deferred_fk_photographs_a_full_perimeter():
    """The engine writes rule_scopes BEFORE the rule, inside one transaction, so
    the AFTER INSERT trigger sees a complete perimeter. If the FK were not
    DEFERRED this would not be possible — and version 1 would say 'no scope'."""
    v1 = R.history(FP, "PE-0001")["versions"][0]
    assert v1["action"] == "created"
    assert v1["scopes"] == "deliberativi", v1["scopes"]
    assert set(v1["consumers"].split(",")) == {"architect", "advisory", "alt-funds", "tax"}


case("version 1 already carries the perimeter (deferred FK)",
     deferred_fk_photographs_a_full_perimeter)

# =====================================================================
head("the batch, and the digest on it")
# =====================================================================

B = R.batch(FP)
ok(B["count"] == 4, "the batch holds the four proposals", B["ids"])
ok(B["digest"] == R.batch(FP)["digest"], "the digest is stable while the batch is")
refuses("a digest that is not the current one",
        lambda: R.approve(FP, "deadbeef"), "not the current one", RulesError)

A = R.approve(FP, B["digest"])
ok(A["count"] == 4, "batch approved against its digest")
ok(R.batch(FP)["count"] == 0, "the batch is empty afterwards")
refuses("nothing to approve on an empty batch",
        lambda: R.approve(FP, R.batch(FP)["digest"]),
        "batch is empty", RulesError)


def approved_means_provisional():
    row = R._row(NAME_FP, "VA-0001")
    assert row["status"] == "active" and row["permanence"] == "provisional"
    assert row["expires_at"], "an approved rule expires: staying costs a decision"


case("approved is ACTIVE and PROVISIONAL, with an expiry", approved_means_provisional)


def the_batch_changes_under_you():
    late = R.propose(FP, "VE", "R", "Late arrival", "Proposed after you read the batch.",
                     ["*"], "test")["id"]
    assert late == "VE-0001", late
    b2 = R.batch(FP)
    assert b2["digest"] != B["digest"], "one more proposal must move the digest"
    R.deny(FP, [late], "only here to move the digest")


case("a proposal arriving later changes the digest", the_batch_changes_under_you)

# =====================================================================
head("the order IS the breadth")
# =====================================================================

L = R.list_rules(FP, "tax")
ok([x["id"] for x in L["rules"]] == ["VA-0001", "PE-0001", "FI-0001"],
   "order: _ALL_, then the group, then the singleton", [x["id"] for x in L["rules"]])
ok(set(L["rules"][0]) == {"id", "body"},
   "the consumer reading carries no via and no breadth", sorted(L["rules"][0]))
FULL = R._rules_for(NAME_FP, "tax")
ok(FULL["rules"][0]["via"] == [ALL] and FULL["rules"][0]["breadth"] == 6,
   "the maintenance reading still reports via and breadth")
ok(L["outside_your_scope"] == 1, "it declares how many stay outside", L["outside_your_scope"])
ok(R.list_rules(FP, "market-news")["count"] == 1,
   "market-news only sees the rule that binds everyone")
ok(R.list_rules(FP, "update-tax")["count"] == 2,
   "a SKILL downloads its own rules exactly like a chat")

BEFORE = [x["id"] for x in R.list_rules(FP, "tax")["rules"]]
case("a new consumer is born", lambda: R.add_consumers(FP, [("genera-dashboard", "skill")]))
ok([x["id"] for x in R.list_rules(FP, "tax")["rules"]] == BEFORE,
   "add a consumer and the order stays right on its own")
ok(R._breadth(NAME_FP, ALL) == 7, "_ALL_ widened by itself")
ok(R.list_rules(FP, "genera-dashboard")["count"] == 1,
   "_ALL_ reaches a consumer created AFTER the rule")
refuses("a consumer cannot take an existing scope's name",
        lambda: R.add_consumers(FP, ["deliberativi"]), "a scope named", RulesError)
ok(R.add_consumers(FP, [("tax", "chat")])["added"] == [],
   "adding a consumer twice is quiet, not an error")

# =====================================================================
head("widening a rule does not touch the group")
# =====================================================================

V_BEFORE = R.history(FP, "PE-0001")["count"]
W = R.widen(FP, "PE-0001", ["market-news"])
ok("market-news" in W["reaches"], "PE-0001 now reaches market-news too")
ok(R.history(FP, "PE-0001")["count"] == V_BEFORE + 1, "widening writes a version")
ok(R._members(NAME_FP, "deliberativi") == ["advisory", "alt-funds", "architect", "tax"],
   "the group is untouched", R._members(NAME_FP, "deliberativi"))


def via_differs_by_consumer():
    # `via` moved to the maintenance reading: the engine under it is shared
    # with list_rules, so what is measured here is the same reaching logic.
    for r in R._rules_for(NAME_FP, "market-news")["rules"]:
        if r["id"] == "PE-0001":
            assert r["via"] == ["market-news"], r["via"]
    for r in R._rules_for(NAME_FP, "architect")["rules"]:
        if r["id"] == "PE-0001":
            assert r["via"] == ["deliberativi"], r["via"]


case("`via` says where the rule reaches you FROM", via_differs_by_consumer)


def widest_scope_decides_the_position():
    ids = [x["id"] for x in R.list_rules(FP, "architect")["rules"]]
    assert ids.index("VA-0001") < ids.index("PE-0001"), ids
    r = [x for x in R._rules_for(NAME_FP, "architect")["rules"] if x["id"] == "PE-0001"][0]
    assert r["breadth"] == 4


case("a rule appears ONCE, positioned by its widest scope",
     widest_scope_decides_the_position)

ok(R.widen(FP, "PE-0001", ["market-news"])["added"] == [], "widening twice adds nothing")
refuses("widening onto something that is not a scope",
        lambda: R.widen(FP, "PE-0001", ["nobody"]), "neither a consumer nor a scope", RulesError)
refuses("widening a rule that was never defined",
        lambda: R.widen(FP, "VE-0099", ["tax"]), "never defined", RulesError)

# =====================================================================
head("history is a photograph, not a pointer")
# =====================================================================


def history_keeps_the_consumers_of_that_day():
    before = [v for v in R.history(FP, "PE-0001")["versions"] if v["action"] == "created"][0]
    assert "alt-funds" in before["consumers"]
    R.edit_scope(FP, "deliberativi", remove=["alt-funds"])
    after = [v for v in R.history(FP, "PE-0001")["versions"] if v["action"] == "created"][0]
    assert after["consumers"] == before["consumers"], \
        f"the past was rewritten: {before['consumers']} -> {after['consumers']}"
    assert "alt-funds" in after["consumers"]
    # and the present did move
    assert "alt-funds" not in R._members(NAME_FP, "deliberativi")
    assert "PE-0001" not in [x["id"] for x in R.list_rules(FP, "alt-funds")["rules"]]


case("a version written BEFORE a scope changed still reports the consumers of then",
     history_keeps_the_consumers_of_that_day)

case("alt-funds goes back into the group",
     lambda: R.edit_scope(FP, "deliberativi", add=["alt-funds"]))
ok(R._breadth(NAME_FP, "deliberativi") == 4, "the group is back to four")


def history_separates_intention_from_effect():
    v = R.history(FP, "PE-0001")["versions"][-1]
    assert "deliberativi" in v["scopes"], v["scopes"]
    assert "market-news" in v["consumers"], v["consumers"]


case("scopes says the intention, consumers says the effect",
     history_separates_intention_from_effect)

refuses("history of an ID never defined",
        lambda: R.history(FP, "VE-98"), "never defined", RulesError)

# =====================================================================
head("citations: marked, validated, expanded")
# =====================================================================


def a_citation_must_be_marked():
    """A citation is what is MARKED as one. The old pattern caught anything that
    looked like an acronym, so prose that merely NAMED one became a reference
    nobody wanted."""
    body = f"This one leans on ({PE1}) and nothing else."
    rid = R.propose(FP, "VE", "R", "Cites the method", body, ["*"], "test")["id"]
    assert R.cx.execute("SELECT dst FROM rule_refs WHERE project=? AND src=?",
                        (NAME_FP, rid)).fetchall()[0][0] == PE1
    R.deny(FP, [rid], "it existed only to be parsed")


case("a marked citation is recorded", a_citation_must_be_marked)

refuses("a bare ID outside the brackets is a forgotten bracket",
        lambda: R.propose(FP, "VE", "R", "x", f"See {PE1} for the method.", ["*"], "m"),
        "bare ID", RulesError)
refuses("and there is no escape hatch, not even backticks",
        lambda: R.propose(FP, "VE", "R", "x", f"An ID looks like `{PE1}`.", ["*"], "m"),
        "no exception", RulesError)


def an_ordinary_parenthesis_is_ordinary_prose():
    """This is what round brackets buy. The check hangs on the SHAPE XX-NNNN,
    not on the bracket, so a parenthesis that holds no ID is just punctuation —
    and the vault's own [[wiki links]] are left free for whatever they may be
    wanted for later."""
    body = ("Prose with (an aside), a [[vault note]], a (nested (one)) and "
            f"a real citation ({PE1}).")
    rid = R.propose(FP, "VE", "R", "Brackets everywhere", body, ["*"], "test")["id"]
    assert R.cx.execute("SELECT body FROM rules WHERE project=? AND id=?",
                        (NAME_FP, rid)).fetchone()[0] == body, "prose is left alone"
    got = [r[0] for r in R.cx.execute(
        "SELECT dst FROM rule_refs WHERE project=? AND src=?", (NAME_FP, rid))]
    assert got == [PE1], got
    R.deny(FP, [rid], "parsed, done")


case("an ordinary parenthesis is prose, and wiki links stay free",
     an_ordinary_parenthesis_is_ordinary_prose)


def the_registry_never_loses_a_word():
    """The worst thing this check could do is not refuse too much — it is to
    accept and quietly delete. A wide gloss slot did exactly that: everything
    after the separator was swallowed before the bare-ID scan could look at it,
    so a body lost a pointer AND the author's sentence without a word. Now the
    odd shapes fall through the pattern and are refused out loud."""
    for label, body in (
            ("a second ID smuggled into the gloss", f"Vedi ({PE1} | {VA1}) e poi altro."),
            ("a hand-written note", f"Vedi ({PE1} — nota mia che sparirebbe)."),
            ("an unbalanced bracket eating a paragraph",
             f"Vedi ({PE1} — la regola\n\nSecondo paragrafo) fine."),
            ("a closing bracket inside the gloss", f"Vedi ({PE1} — a) trappola) fine.")):
        try:
            R.propose(FP, "VE", "R", "x", body, ["*"], "m")
            raise AssertionError(f"accepted, and would have eaten text: {label}")
        except RulesError:
            pass


case("nothing is ever swallowed in silence", the_registry_never_loses_a_word)


def only_the_project_s_own_domains_are_hunted():
    """The bare-ID scan looks only for domains this project DECLARED. Chasing
    every two-letters-and-digits token caught a URL path, a locale and a ticket
    number — things no rewriting of the sentence can fix — and caught nothing
    extra, because a forgotten bracket is always around a domain that exists."""
    body = (f"Guida [qui](https://example.com/en-2024/x), ticket PR-1234, "
            f"norma ISO-9001. Vedi ({PE1}).")
    rid = R.propose(FP, "VE", "R", "Prose that is not a citation", body,
                    ["*"], "test")["id"]
    assert R.cx.execute("SELECT body FROM rules WHERE project=? AND id=?",
                        (NAME_FP, rid)).fetchone()[0] == body
    R.deny(FP, [rid], "parsed, done")


case("a URL, a ticket and a standard are not IDs of this project",
     only_the_project_s_own_domains_are_hunted)

refuses("a mistyped ID of a real domain is still caught",
        lambda: R.propose(FP, "VE", "R", "x", "Vedi VA-00001 in prosa.", ["*"], "m"),
        "bare ID", RulesError)
refuses("a citation that does not resolve: a chat cannot invent a pointer",
        lambda: R.propose(FP, "VE", "R", "x", "See (VE-0099).", ["*"], "m"),
        "does not resolve", RulesError)
refuses("a lower-cased bare ID is the same forgotten bracket",
        lambda: R.propose(FP, "VE", "R", "x", f"See {PE1.lower()} for the method.",
                          ["*"], "m"),
        "bare ID", RulesError)
refuses("and so is a half-cased one",
        lambda: R.propose(FP, "VE", "R", "x", "See Pe-0001 for the method.", ["*"], "m"),
        "bare ID", RulesError)
refuses("brackets around a SENTENCE are not a citation",
        lambda: R.propose(FP, "VE", "R", "x", f"(see {PE1} for the method)", ["*"], "m"),
        "ALONE inside round brackets", RulesError)


def the_two_doors_agree_on_what_an_ID_looks_like():
    """rules_get tolerates the type suffix and a short number; a body must
    tolerate exactly the same, or a tolerance documented in one place becomes a
    refusal in another."""
    rid = R.propose(FP, "VE", "R", "Suffixed citation",
                    f"Leans on ({PE1}-M) and on (va-01).", ["*"], "test")["id"]
    got = {r[0] for r in R.cx.execute(
        "SELECT dst FROM rule_refs WHERE project=? AND src=?", (NAME_FP, rid))}
    assert got == {PE1, "VA-0001"}, got
    R.deny(FP, [rid], "parsed, done")


case("the citation parser and rules_get read an ID the same way",
     the_two_doors_agree_on_what_an_ID_looks_like)


def you_may_only_cite_a_rule_ALREADY_APPROVED():
    """The load-bearing refusal, and it is a decision about how the corpus is
    built. Citing something still in the batch looks convenient and is a trap:
    the number of a proposal is not final until it is in, so a batch whose
    members cite each other can be approved into a state where the pointers were
    right only while they were being written.

    So the order of work is forced — file the cited rule, get it approved, then
    file the one that cites it — and a rule that needs one that does not exist
    yet simply waits. Nobody is writing twelve thousand rules here."""
    pending = R.propose(FP, "VE", "R", "Not approved yet", "Body.", ["*"], "test")["id"]
    for label, target in (("still proposed", pending),):
        try:
            R.propose(FP, "VE", "R", "Leaning on it", f"Builds on ({target}).", ["*"], "m")
            raise AssertionError(f"it should have refused: {label}")
        except RulesError as e:
            assert "not in force yet" in str(e) and "ALREADY been approved" in str(e), e
    # Denied is the same answer: the row is kept so the refusal stays readable,
    # not so a later rule can build on it.
    R.deny(FP, [pending], "the idea was refused")
    try:
        R.propose(FP, "VE", "R", "Leaning on it", f"Builds on ({pending}).", ["*"], "m")
        raise AssertionError("it should have refused: denied")
    except RulesError as e:
        assert "not in force yet" in str(e), e
    # And once the cited rule IS approved, the citing one goes in.
    ok_target = R.propose(FP, "VE", "R", "Approved first", "Body.", ["*"], "test")["id"]
    R.approve(FP, R.batch(FP)["digest"])
    citer = R.propose(FP, "VE", "R", "Leans on an approved one",
                      f"Builds on ({ok_target}).", ["*"], "test")["id"]
    assert R.cx.execute("SELECT dst FROM rule_refs WHERE project=? AND src=?",
                        (NAME_FP, citer)).fetchall()[0][0] == ok_target
    R.deny(FP, [citer], "done")
    R.retire(FP, ok_target, reason="done too")


case("you may only cite a rule that is already approved",
     you_may_only_cite_a_rule_ALREADY_APPROVED)


def the_audit_watches_what_the_door_cannot():
    """The door now refuses a citation towards anything not yet approved, so
    this state can only be reached the way everything else bypasses the door: a
    write made by hand. It still has to be REPORTED — a rule in force pointing
    at something that never came into force is exactly the kind of defect that
    is invisible until it blocks somebody.

    And the buckets count the SOURCE only when it is in force, or a batch would
    report the project as incoherent for citing rules that are on their way
    in."""
    target = R.propose(FP, "VE", "R", "Never approved", "Body.", ["*"], "test")["id"]
    citer = R.propose(FP, "VE", "R", "Points at it", "Body without a citation.",
                      ["*"], "test")["id"]
    R.cx.execute("INSERT INTO rule_refs (project, src, dst) VALUES (?,?,?)",
                 (NAME_FP, citer, target))
    # Both are proposals: a batch on its way in is not a defect.
    assert R.check(FP)["citations_to_proposed"] == [], R.check(FP)["citations_to_proposed"]
    # Put only the citing one in force, by hand, and the picture changes.
    R.cx.execute("UPDATE rules SET status='active', expires_at=? WHERE project=? AND id=?",
                 (_plus_days(90), NAME_FP, citer))
    v = R.check(FP)
    assert {"from": citer, "cites": target} in v["citations_to_proposed"], v
    assert not v["coherent"]
    R.deny(FP, [target], "and now it is refused outright")
    v2 = R.check(FP)
    assert {"from": citer, "cites": target} in v2["citations_to_denied"], v2
    assert v2["citations_to_proposed"] == []
    R.retire(FP, citer, reason="tidy up after the audit case")
    R.cx.execute("DELETE FROM rule_refs WHERE project=? AND src=?", (NAME_FP, citer))


case("the audit reports what the door could not have known",
     the_audit_watches_what_the_door_cannot)


def a_short_citation_still_resolves():
    """Older text says VA-02. Padding is what stops the change costing a rewrite
    of every body that was ever written."""
    rid = R.propose(FP, "VE", "R", "Short form", "See (VA-01).", ["*"], "test")["id"]
    assert R.cx.execute("SELECT dst FROM rule_refs WHERE project=? AND src=?",
                        (NAME_FP, rid)).fetchall()[0][0] == "VA-0001"
    R.deny(FP, [rid], "parsed, done")


case("a two-digit citation resolves onto the four-digit rule",
     a_short_citation_still_resolves)


def reading_expands_the_citation():
    """The gloss is GENERATED, never stored: it cannot go stale, and it carries
    the STATE of what it points at."""
    body = f"Leans on ({PE1})."
    rid = R.propose(FP, "VE", "R", "Reads expanded", body, ["*"], "test")["id"]
    stored = R.cx.execute("SELECT body FROM rules WHERE project=? AND id=?",
                          (NAME_FP, rid)).fetchone()[0]
    assert stored == body, "only the pointer is stored"
    shown = [x for x in R.pending(FP)["waiting"] if x["id"] == rid][0]["body"]
    assert shown == f"Leans on ({PE1} — The method of the four).", shown
    # A title carrying a bracket would otherwise produce a citation the parser
    # cannot take back — and the refusal would blame the author for text the
    # registry generated. The brackets are neutralised inside the gloss.
    v = R._version(NAME_FP, PE1)
    R.amend(FP, PE1, v, reason="a title with brackets in it",
            title="The method of the four (as amended)")
    risky = [x for x in R.pending(FP)["waiting"] if x["id"] == rid][0]["body"]
    assert "(as amended)" not in risky and "[as amended]" in risky, risky
    R.amend(FP, rid, R._version(NAME_FP, rid), reason="paste back a bracketed gloss",
            body=risky)
    assert R.cx.execute("SELECT dst FROM rule_refs WHERE project=? AND src=?",
                        (NAME_FP, rid)).fetchall()[0][0] == PE1
    R.amend(FP, PE1, R._version(NAME_FP, PE1), reason="put the title back",
            title="The method of the four")
    # And it comes straight back in: the expanded form is accepted, and what is
    # STORED is the bare pointer again. Storing the gloss would be the whole
    # point thrown away — a title changed tomorrow would leave a stale copy of
    # itself inside somebody else's rule, which is the staleness of an export
    # but inside the authoritative source.
    R.approve(FP, R.batch(FP)["digest"])
    R.amend(FP, rid, R._version(NAME_FP, rid), reason="pasted back as read", body=shown)
    assert R.cx.execute("SELECT body FROM rules WHERE project=? AND id=?",
                        (NAME_FP, rid)).fetchone()[0] == body, "the gloss is NOT stored"
    assert R.cx.execute("SELECT dst FROM rule_refs WHERE project=? AND src=?",
                        (NAME_FP, rid)).fetchall()[0][0] == PE1
    # Pasting back what you read is therefore a no-op on the text, which is why
    # amend does not treat it as a body change at all.
    assert R.amend(FP, rid, R._version(NAME_FP, rid), reason="same text again",
                   body=shown)["cites"] == "unchanged"
    R.retire(FP, rid, reason="it had done its job")
    # A pointer at a retired rule arrives already marked as such, in the text.
    other = R.propose(FP, "VE", "R", "Points at the retired one",
                      f"Still points at ({rid}).", ["*"], "test")["id"]
    seen = [x for x in R.pending(FP)["waiting"] if x["id"] == other][0]["body"]
    assert "· retired" in seen, seen
    R.deny(FP, [other], "done")


case("reading expands with the current title, and marks the state",
     reading_expands_the_citation)


def the_ceiling_is_measured_on_WHAT_IS_STORED():
    """Padding a short citation makes a body BIGGER, and dropping a gloss makes
    it smaller. Measuring the text as it arrived would let one over the ceiling
    and refuse another that fits — the same rule answered two ways depending on
    which form you happened to paste."""
    unit = f"({VA1[:2]}-01) "                         # 8 bytes stored as 10
    n = (MAX_BODY_BYTES // len(unit)) - 100
    fat = unit * n
    while len(R._compact(fat).encode()) <= MAX_BODY_BYTES:
        n += 100
        fat = unit * n
    assert len(fat.encode()) < MAX_BODY_BYTES < len(R._compact(fat).encode())
    try:
        R.propose(FP, "VA", "R", "Fat once padded", fat, ["*"], "m")
        raise AssertionError("it should have refused")
    except RulesError as e:
        assert "once stored" in str(e), e


case("the body ceiling is measured after compaction, not before",
     the_ceiling_is_measured_on_WHAT_IS_STORED)

# =====================================================================
head("amending: same ID, a defect fixed")
# =====================================================================


def a_broken_pointer_can_only_get_in_BY_HAND():
    """The door refuses an unresolved citation, so the only way one exists is a
    write made with sqlite3 as root — which is the documented exception, and the
    reason rules_check still has to look. This is that write."""
    R.cx.execute("UPDATE rules SET body=? WHERE project=? AND id=?",
                 ("SOURCE data is re-read right before writing the derivative. "
                  "See (ST-0007).", NAME_FP, "VA-0001"))
    R.cx.execute("INSERT OR IGNORE INTO rule_refs (project, src, dst) VALUES (?,?,?)",
                 (NAME_FP, "VA-0001", "ST-0007"))
    R.cx.execute("UPDATE rules SET body=? WHERE project=? AND id=?",
                 ("The bracket is estimated from the rollup by character. "
                  "Cross-check with (VE-0090).", NAME_FP, "FI-0001"))
    R.cx.execute("INSERT OR IGNORE INTO rule_refs (project, src, dst) VALUES (?,?,?)",
                 (NAME_FP, "FI-0001", "VE-0090"))


case("two dangling pointers written by hand, bypassing the door",
     a_broken_pointer_can_only_get_in_BY_HAND)

BROKEN_AT = R._version(NAME_FP, "VA-0001")


def check_finds_the_broken_pointer():
    v = R.check(FP)
    assert {"from": "VA-0001", "cites": "ST-0007"} in v["broken_pointers"], v["broken_pointers"]
    assert not v["coherent"]
    assert R.check(HT)["broken_pointers"] == [], "references do not spill between projects"


case("check finds ST-0007 broken, and stays inside the project",
     check_finds_the_broken_pointer)


def the_expansion_does_not_blow_up_on_a_dangling_pointer():
    """A reading path that can fail is a reading path that will. The expansion
    marks it and carries on."""
    shown = R.get_rules(FP, "VA-0001", "tax")["found"][0]["body"]
    assert "never defined" in shown, shown


case("a dangling pointer is MARKED on reading, not raised",
     the_expansion_does_not_blow_up_on_a_dangling_pointer)


def amend_rewrites_the_refs():
    # The version number is administration now: a consumer reading has no
    # `version` field, so the maintainer reads it where maintenance reads.
    v = R._version(NAME_FP, "VA-0001")
    R.amend(FP, "VA-0001", v, reason="dropped the broken pointer",
            body="SOURCE data is re-read right before writing the derivative.")
    assert R._version(NAME_FP, "VA-0001") == v + 1
    broken = R.check(FP)["broken_pointers"]
    assert {"from": "VA-0001", "cites": "ST-0007"} not in broken
    assert {"from": "FI-0001", "cites": "VE-0090"} in broken, "the others stay"


case("amend: a new version, and the citations recomputed", amend_rewrites_the_refs)

refuses("rules_fix cannot let in what propose refuses",
        lambda: R.amend(FP, "VA-0001", R._version(NAME_FP, "VA-0001"),
                        reason="m", body="See (VE-0099)."),
        "does not resolve", RulesError)

refuses("a stale version is refused (compare-and-swap)",
        lambda: R.amend(FP, "VA-0001", 1, reason="m", body="z"), "someone wrote", RulesError)
refuses("amend without a reason",
        lambda: R.amend(FP, "VA-0001", R._version(NAME_FP, "VA-0001"), reason=""),
        "reason is mandatory", RulesError)
refuses("amend an ID never defined",
        lambda: R.amend(FP, "VE-97", 1, reason="m"), "never defined", RulesError)


def history_reads_like_a_story():
    s = R.history(FP, "VA-0001")
    actions = [x["action"] for x in s["versions"]]
    assert actions[0] == "created" and "amended" in actions, actions
    assert s["versions"][-1]["reason"] == "dropped the broken pointer"


case("history: the actions and the reasons are the right ones", history_reads_like_a_story)


def compare_shows_what_changed():
    n = R.history(FP, "VA-0001")["count"]
    c = R.compare(FP, "VA-0001", BROKEN_AT, n)
    assert "ST-0007" in c["diff"] and not c["identical"]
    assert R.compare(FP, "VA-0001", n, n)["identical"]


case("compare: the diff shows what moved", compare_shows_what_changed)

refuses("comparing against a version that does not exist",
        lambda: R.compare(FP, "VA-0001", 1, 99), "does not exist", RulesError)

# =====================================================================
head("reading: three different answers, and no oracle")
# =====================================================================

ok(R.get_rules(FP, "FI-0001", "tax")["found"][0]["id"] == "FI-0001", "your own rule: you read it")
ok(R.get_rules(FP, "FI-0001-M", "tax")["found"][0]["id"] == "FI-0001",
   "a citation carrying the type suffix is tolerated")


def three_answers_kept_apart():
    g = R.get_rules(FP, ["VA-0001", "FI-0001", "VE-0099"], "market-news")
    assert [x["id"] for x in g["found"]] == ["VA-0001"]
    assert g["not_yours"][0]["id"] == "FI-0001" and "tax" in g["not_yours"][0]["held_by"]
    assert g["never_defined"] == ["VE-0099"]
    assert "BROKEN CITATION" in g["warning"]


case("found · not_yours · never_defined, one call", three_answers_kept_apart)


def no_oracle_across_projects():
    g = R.get_rules(HT, ["FI-0001"], "coach")      # FI-0001 exists, but in the other project
    assert g["never_defined"] == ["FI-0001"]
    assert "Financial" not in repr(g), f"ORACLE: {g}"


case("an ID that exists in ANOTHER project is not revealed", no_oracle_across_projects)

refuses("asking for no ID at all",
        lambda: R.get_rules(FP, [], "tax"), "no ID asked for", RulesError)
refuses("more IDs than the ceiling",
        lambda: R.get_rules(FP, [f"VA-{i:03d}" for i in range(MAX_GET_IDS + 1)], "tax"),
        "the ceiling is", RulesError)
refuses("a consumer left blank",
        lambda: R.list_rules(FP, ""), "consumer not specified", RulesError)


def search_stays_inside_perimeter_and_project():
    s = R.search(FP, "bracket", "tax")
    assert s["count"] == 1 and s["hits"][0]["id"] == "FI-0001"
    s2 = R.search(FP, "bracket", "market-news")
    assert s2["count"] == 0 and s2["outside_your_scope"] == 1, s2
    assert R.search(HT, "bracket", "coach")["count"] == 0, "no spill between projects"


case("search stays inside the perimeter AND the project",
     search_stays_inside_perimeter_and_project)

refuses("a one-character search",
        lambda: R.search(FP, "a", "tax"), "at least two characters", RulesError)

# =====================================================================
head("denial: the registry remembers the refusals")
# =====================================================================

DUP = R.propose(FP, "FI", "R", "Duplicate", f"Says the same as ({FI1}).",
                ["tax"], "worth a try", "tax")["id"]
ok(DUP == "FI-0002", "the duplicate takes the next number of its domain", DUP)
case("denial needs no signature — refusing cannot do harm",
     lambda: R.deny(FP, [DUP], "single case, not a pattern"))


def the_guard_on_denied_IDs_is_gone_and_that_is_the_deal():
    """It used to be impossible to re-file a denied ID. That guard worked
    BECAUSE the ID was yours to choose: with the counter, the same text filed
    again simply takes a new number and goes through. Accepted with eyes open —
    the risk is not bad faith but a chat that does not KNOW, and it already has
    the information: rules_pending shows its own refusals with the reason. The
    guard went from an obligation to a reminder."""
    again = R.propose(FP, "FI", "R", "Duplicate", "Filed again, knowingly.",
                      ["tax"], "proving the guard is gone", "tax")["id"]
    assert again == "FI-0003", again
    mine = R.pending(FP, "tax")
    assert [d["id"] for d in mine["denied"]] == [DUP], mine["denied"]
    assert mine["denied"][0]["denied_reason"].startswith("single case")
    R.deny(FP, [again], "and denied again, on purpose")


case("the denied-ID guard is gone, and the noticeboard carries the weight",
     the_guard_on_denied_IDs_is_gone_and_that_is_the_deal)

refuses("denying without a reason",
        lambda: R.deny(FP, ["VA-0001"], ""), "say why", RulesError)
refuses("denying something that is not a proposal",
        lambda: R.deny(FP, ["VA-0001"], "because"), "not a pending proposal", RulesError)
refuses("denying an ID that does not exist",
        lambda: R.deny(FP, ["VE-96"], "because"), "no such proposal", RulesError)


def the_noticeboard_replaces_the_chat_memory():
    p = R.pending(FP, "tax")
    assert len(p["denied"]) == 2, p["denied"]
    assert any(d["denied_reason"].startswith("single case") for d in p["denied"])
    assert p["waiting"] == []


case("the noticeboard shows the denial WITH its reason",
     the_noticeboard_replaces_the_chat_memory)

# =====================================================================
head("expiry, renewal, promotion")
# =====================================================================


def an_expired_provisional_leaves_by_itself():
    R.cx.execute("UPDATE rules SET expires_at=? WHERE project=? AND id='FI-0001'",
                 ("2020-01-01T00:00:00Z", NAME_FP))
    assert "FI-0001" not in [x["id"] for x in R.list_rules(FP, "tax")["rules"]]
    assert R.status(FP)["rules"]["expired_not_retired"] == 1


case("an expired provisional leaves the lists on its own",
     an_expired_provisional_leaves_by_itself)


def renewal_brings_it_back():
    out = R.renew(FP, ["FI-0001"])
    assert out["renewed"] == ["FI-0001"] and out["expires_at"]
    assert "FI-0001" in [x["id"] for x in R.list_rules(FP, "tax")["rules"]]


case("renew brings it back, with a fresh expiry", renewal_brings_it_back)

refuses("renewing a rule that is not active",
        lambda: R.renew(FP, ["FI-0002"]),
        "not an active rule", RulesError)
refuses("renewing nothing", lambda: R.renew(FP, []), "no ID to renew", RulesError)


def promotion_makes_it_permanent():
    out = R.promote(FP, ["VA-0001"])
    row = R._row(NAME_FP, "VA-0001")
    assert out["promoted"] == ["VA-0001"]
    assert row["permanence"] == "permanent" and row["expires_at"] is None


case("promote: permanent, no expiry", promotion_makes_it_permanent)

refuses("promoting a rule that is not active",
        lambda: R.promote(FP, ["FI-0002"]), "not an active rule", RulesError)


def the_noticeboard_warns_before_the_expiry():
    R.cx.execute("UPDATE rules SET expires_at=? WHERE project=? AND id='PE-0001'",
                 (_plus_days(10), NAME_FP))
    p = R.pending(FP, "architect")
    assert [x["id"] for x in p["expiring_within_30_days"]] == ["PE-0001"], p["expiring_within_30_days"]
    R.renew(FP, ["PE-0001"])
    assert R.pending(FP, "architect")["expiring_within_30_days"] == []


case("the noticeboard lists what expires within 30 days",
     the_noticeboard_warns_before_the_expiry)

# =====================================================================
head("retiring: out of the lists, still resolvable")
# =====================================================================


FI_NEW = R.propose(FP, "FI", "M", "Estimating the bracket (rev)", "New method.",
                   ["tax"], "superseded decision: the first one underestimated")["id"]
ok(FI_NEW == "FI-0004",
   "the successor takes the next number: the denied ones are spent", FI_NEW)


def retire_with_a_successor():
    b = R.batch(FP)
    R.approve(FP, b["digest"])
    out = R.retire(FP, FI1, reason=f"superseded by {FI_NEW}", superseded_by=FI_NEW)
    assert out["superseded_by"] == FI_NEW
    assert FI1 not in [x["id"] for x in R.list_rules(FP, "tax")["rules"]]
    assert R.get_rules(FP, FI1, "tax")["found"][0]["status"] == "retired", \
        "by ID it still resolves: citations must keep working"


case("retire + superseded_by: out of the active list, still there by ID",
     retire_with_a_successor)


def a_retired_number_is_not_handed_out_again():
    """The old suite proved this by trying to re-file the ID. There is no such
    move any more, so the proof moved to where the decision now lives: the
    counter."""
    nxt = R.propose(FP, "FI", "R", "After the retirement", "Body.", ["tax"], "m")["id"]
    assert nxt == "FI-0005", nxt
    R.deny(FP, [nxt], "done")


case("the counter walks past a retired number, it does not fill it",
     a_retired_number_is_not_handed_out_again)

refuses("retiring twice",
        lambda: R.retire(FP, FI1, reason="m"), "already retired", RulesError)


def a_successor_must_be_approved_too():
    """superseded_by is the one pointer the supersede workflow depends on, and
    it is NOT written to rule_refs — so no audit ever comes back to it. If it
    could point at a proposal, a retired rule would end up claiming a successor
    that was later denied, for good and in silence. Same rule as a citation,
    checked in the only place it can be."""
    unborn = R.propose(FP, "FI", "M", "Never approved successor", "Body.",
                       ["tax"], "test")["id"]
    victim = R.propose(FP, "FI", "M", "To be retired", "Body.", ["tax"], "test")["id"]
    R.approve(FP, R.batch(FP)["digest"])
    # (the batch above approved both; put one back to 'proposed' by hand so the
    # case is about the check and not about the batch)
    R.cx.execute("UPDATE rules SET status='proposed' WHERE project=? AND id=?",
                 (NAME_FP, unborn))
    try:
        R.retire(FP, victim, reason="m", superseded_by=unborn)
        raise AssertionError("it should have refused")
    except RulesError as e:
        assert "has not been approved yet" in str(e), e
    try:
        R.retire(FP, victim, reason="m", superseded_by=victim)
        raise AssertionError("it should have refused")
    except RulesError as e:
        assert "cannot supersede itself" in str(e), e
    R.deny(FP, [unborn], "done")
    R.retire(FP, victim, reason="retired without a successor")


case("a successor must already be approved, and cannot be the rule itself",
     a_successor_must_be_approved_too)
refuses("retiring without a reason",
        lambda: R.retire(FP, "PE-0001", reason=""), "reason is mandatory", RulesError)
refuses("superseded_by pointing at nothing",
        lambda: R.retire(FP, "PE-0001", reason="m", superseded_by="FI-0099"),
        "does not exist", RulesError)
refuses("retiring an ID never defined",
        lambda: R.retire(FP, "VE-95", reason="m"), "never defined", RulesError)


def check_sees_citations_to_a_retired_rule():
    v = R.check(FP)
    assert {"from": FI_NEW, "cites": FI1} not in v["citations_to_retired"], \
        f"{FI_NEW} does not cite {FI1}"
    R.amend(FP, FI_NEW, R._version(NAME_FP, FI_NEW), reason="cite the ancestor",
            body=f"New method. Supersedes ({FI1}).")
    v2 = R.check(FP)
    assert {"from": FI_NEW, "cites": FI1} in v2["citations_to_retired"], v2


case("check flags an active rule citing a retired one",
     check_sees_citations_to_a_retired_rule)

# =====================================================================
head("narrowing, and a rule left with no perimeter")
# =====================================================================


ORPHAN = R.propose(FP, "VE", "R", "Orphan to be", "Only here to be narrowed to nothing.",
                   ["market-news"], "test")["id"]


def narrow_to_nothing_is_reported():
    b = R.batch(FP)
    R.approve(FP, b["digest"])
    out = R.narrow(FP, ORPHAN, ["market-news"])
    assert out["scopes"] == [] and "reaches nobody" in out["warning"]
    assert ORPHAN in R.check(FP)["rules_without_perimeter"]
    R.widen(FP, ORPHAN, ["market-news"])            # put it back
    assert R.check(FP)["rules_without_perimeter"] == []


case("narrowed to nothing: the tool warns and the audit lists it",
     narrow_to_nothing_is_reported)

ok(R.narrow(FP, ORPHAN, ["tax"])["removed"] == [],
   "removing a scope that was not there is quiet")

# =====================================================================
head("the audit: redundancy candidates, and one report that is GONE")
# =====================================================================


def numbering_gaps_are_gone_for_good():
    """The report is not fixed, it is DELETED. With the counter a gap cannot
    happen — it does not skip, and retiring leaves the row in place — so any gap
    the old code reported would have been a choice, not a loss. A check that
    cannot tell a fault from a choice is a line you learn to skip, and the day
    it says something true you have already stopped reading it."""
    v = R.check(FP)
    assert "numbering_gaps" not in v, sorted(v)
    seqs = sorted(r[0] for r in R.cx.execute(
        "SELECT seq FROM rules WHERE project=? AND domain='VA'", (NAME_FP,)))
    assert seqs == list(range(1, len(seqs) + 1)), seqs


case("numbering_gaps is gone, and the numbering has none to report",
     numbering_gaps_are_gone_for_good)

TWIN_A = TWIN_B = ""


def redundancy_is_a_suspicion_not_a_verdict():
    global TWIN_A, TWIN_B
    TWIN_A = R.propose(FP, "ST", "R", "One", f"Body one, see ({VA1}).",
                       ["market-news"], "test")["id"]
    TWIN_B = R.propose(FP, "ST", "R", "Two", f"Body two, see ({VA1}).",
                       ["market-news"], "test")["id"]
    b = R.batch(FP)
    R.approve(FP, b["digest"])
    pairs = [c["pair"] for c in R.check(FP)["redundancy_candidates"]]
    assert [TWIN_A, TWIN_B] in pairs, pairs


case("two rules, same perimeter, same citations: a candidate pair",
     redundancy_is_a_suspicion_not_a_verdict)

# =====================================================================
head("the triggers hold even against a hand at the sqlite3 prompt")
# =====================================================================


def a_manual_delete_still_lands_in_history():
    R.cx.execute("DELETE FROM rules WHERE project=? AND id=?", (NAME_FP, TWIN_B))
    assert R.history(FP, TWIN_B)["versions"][-1]["action"] == "DELETED"


case("a DELETE by hand is recorded anyway", a_manual_delete_still_lands_in_history)


def a_dropped_trigger_is_rebuilt_AND_declared():
    """A vanished trigger raises no error: it just stops writing history. So the
    repair is announced, or nobody would ever learn it happened."""
    R.close()
    cx = sqlite3.connect(DB)
    cx.execute("DROP TRIGGER trg_rules_upd")
    cx.commit()
    cx.close()
    r2 = Registry(DB)
    assert "trg_rules_upd" in r2.repaired, r2.repaired
    assert r2.status(FP)["repaired_at_open"] == ["trg_rules_upd"]
    r2.close()


case("a trigger dropped by hand is rebuilt, and the repair is DECLARED",
     a_dropped_trigger_is_rebuilt_AND_declared)

R = Registry(DB, provisional_days=90)
ok(R.repaired == [], "a clean reopen repairs nothing")

# =====================================================================
head("projects stay apart")
# =====================================================================


def same_id_two_projects_two_histories():
    twin = R.propose(HT, "VA", "R", "Namesake but different", "Body of Health Tracking.",
                     ["*"], "initial import")["id"]
    assert twin == VA1, "each project counts on its own, from one"
    b = R.batch(HT)
    R.approve(HT, b["digest"])
    assert R._row(NAME_HT, "VA-0001")["title"] == "Namesake but different"
    assert R._row(NAME_FP, "VA-0001")["title"] == "Re-read the sources"
    assert R.history(HT, "VA-0001")["count"] < R.history(FP, "VA-0001")["count"]


case("the same ID lives in two projects with two histories",
     same_id_two_projects_two_histories)

ok(R.project_info(HT)["scopes"][0]["breadth"] == 2, "_ALL_ is counted per project")
refuses("the other project's consumer does not exist here",
        lambda: R.list_rules(HT, "tax"), "unknown consumer", RulesError)
refuses("a wrong code answers like a missing one",
        lambda: R.project_info("Xxxxxxxxxxx"), "project not specified", RulesError)


def new_domain_and_consumer_work_at_once():
    R.add_domains(HT, {"AL": "food"})
    R.add_consumers(HT, [("nutritionist", "chat")])
    fresh = R.propose(HT, "AL", "R", "New domain", "Body.", ["nutritionist"], "test")["id"]
    assert fresh == "AL-0001", "a domain born later starts its own counter at one"
    b = R.batch(HT)
    R.approve(HT, b["digest"])
    assert [x["id"] for x in R.list_rules(HT, "nutritionist")["rules"]] == [VA1, fresh]


case("a domain and a consumer added later work immediately",
     new_domain_and_consumer_work_at_once)

ok(R.add_domains(HT, {"AL": "food again"})["added"] == [], "adding a domain twice is quiet")
refuses("a malformed domain added later",
        lambda: R.add_domains(HT, {"food": ""}), "two uppercase", RulesError)


def all_aliases_all_mean_all():
    for i, alias in enumerate(["_all_", "*", "all", "tutti", "chiunque"], start=1):
        rid = R.propose(HT, "MS", "F", f"Universal via {alias}", "Binds everyone.",
                        [alias], "test")["id"]
        assert rid == f"MS-{i:04d}", (alias, rid)
        assert R._scopes_of(NAME_HT, rid) == [ALL], alias
    b = R.batch(HT)
    R.approve(HT, b["digest"])


case("_ALL_ and its aliases all land on the same scope", all_aliases_all_mean_all)


def rekey_kills_the_old_code():
    R.rekey_project(HT, "Nn99Mm88Kkzz")
    try:
        R.list_rules(HT, "coach")
        raise AssertionError("the old code should be dead")
    except RulesError as e:
        assert "project not specified" in str(e)
    assert R.list_rules("Nn99Mm88Kkzz", "coach")["project"] == NAME_HT
    R.rekey_project("Nn99Mm88Kkzz", HT)


case("rekey: the old code dies, the rules stay", rekey_kills_the_old_code)

refuses("rekey onto a code in use",
        lambda: R.rekey_project(HT, FP), "already in use", RulesError)
refuses("rekey onto a malformed code",
        lambda: R.rekey_project(HT, "short"), "8 to 32", RulesError)

# =====================================================================
head("a third project, seeded the only way left: one rule at a time")
# =====================================================================

# There is no bulk door any more. What a migration used to get from
# import_rules — active rules in a fresh project — now takes the same three
# calls as any other rule: propose, batch, approve. That is the point, not a
# regression: the pass over the corpus IS the migration.


def a_project_is_seeded_by_proposing():
    R.create_project(CASA, NAME_CASA, [("architect", "chat")], {"CA": "house"})
    rid = R.propose(CASA, "CA", "R", "First of the house", "Body of the house.",
                    ["architect"], "seeded by hand", "architect")["id"]
    assert rid == "CA-0001", rid
    R.approve(CASA, R.batch(CASA)["digest"])
    row = R._row(NAME_CASA, rid)
    assert row["status"] == "active" and row["permanence"] == "provisional"
    assert row["expires_at"], "a seeded rule expires like any other: rule five applies"
    assert R.list_rules(CASA, "architect")["count"] == 1


case("a fresh project fills through propose/approve, and rule five applies",
     a_project_is_seeded_by_proposing)

EMPTY = "Ee11Ff22Gg33"
case("an empty project stays possible, and empty", lambda: R.create_project(
    EMPTY, "Empty", [("architect", "chat")], {"CA": "house"}))

# =====================================================================
head("the signature is gone, and the digest stays")
# =====================================================================

# The signature was the clumsy way of letting a PERSON in instead of a chat;
# the admin UI solves that at the root, and keeping it would be ceremony
# (decided 2026-08-10). THE DIGEST IS NOT THE SIGNATURE'S: it is the check
# that you approve the batch you READ, and it stays — proved above, where a
# stale digest is refused. With the signature went the grace window: there is
# no unsigned state left to record, so there is nothing for a date to permit.


def no_write_takes_a_signature():
    """The guarantee is an ABSENCE, so it is proved from the outside: no
    lifecycle method has a parameter that could carry a signature."""
    import inspect
    for name in ("approve", "renew", "promote"):
        params = list(inspect.signature(getattr(R, name)).parameters)
        assert "signature" not in params, f"{name}() still takes a signature: {params}"


case("approve, renew and promote have no way to receive a signature",
     no_write_takes_a_signature)


def the_approvals_table_records_no_signature():
    cols = {r[1] for r in R.cx.execute("PRAGMA table_info(approvals)")}
    assert "signature" not in cols, f"approvals still records a signature: {sorted(cols)}"
    assert "signed" not in cols, f"approvals still records signed/unsigned: {sorted(cols)}"
    # What it DOES record: what was let in and when, one row per approval.
    rows = R.cx.execute("SELECT n_rules, rule_ids FROM approvals WHERE project=?",
                        (NAME_FP,)).fetchall()
    assert rows, "no approval was recorded at all"
    first = [r for r in rows if "VA-0001" in r["rule_ids"]]
    assert first and first[0]["n_rules"] == 4, [dict(r) for r in rows]


case("the approvals table records what was let in, and no signature",
     the_approvals_table_records_no_signature)


def the_engine_has_no_verifier_and_no_grace():
    import rules as _r
    assert not hasattr(_r, "verify_signature"), "verify_signature is still in the engine"
    assert not hasattr(R, "in_grace"), "in_grace() is still on the Registry"
    assert not hasattr(R, "_require_signature"), "_require_signature is still on the Registry"
    import inspect
    init = list(inspect.signature(Registry.__init__).parameters)
    assert "public_key" not in init and "grace_until" not in init, init


case("no verifier, no grace window, no key on the Registry",
     the_engine_has_no_verifier_and_no_grace)

# =====================================================================
head("derivatives: export and backup")
# =====================================================================


def export_full_and_per_consumer():
    e = R.export(FP)
    assert VA1 in e["markdown"] and "_retired_" in e["markdown"]
    assert "Health Tracking" not in e["markdown"], "only its own project"
    ex = R.export(FP, "tax")
    assert ex["consumer"] == "tax"
    assert FI_NEW in ex["markdown"] and VA1 in ex["markdown"]
    assert "RL-" not in ex["markdown"], "someone else's perimeter leaked in"
    assert "_retired_" not in ex["markdown"], "a retired rule leaked into a consumer export"
    headings = [l for l in ex["markdown"].splitlines() if l.startswith("## Reaching")]
    breadths = [int(h.split()[2]) for h in headings]
    assert breadths == sorted(breadths, reverse=True), f"blocks out of order: {breadths}"
    assert "DERIVATIVE" in ex["markdown"], "the export must say it is regenerable"


case("export: whole project, and the block for one consumer, widest first",
     export_full_and_per_consumer)


def backup_is_a_quiescent_copy():
    b = R.backup(os.path.join(D, "bk"))
    cx = sqlite3.connect(b["backup"])
    assert cx.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 5
    assert cx.execute("SELECT COUNT(DISTINCT project) FROM rules").fetchone()[0] == 4
    assert cx.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    cx.close()
    assert (os.stat(b["backup"]).st_mode & 0o777) == FILE_MODE


case("backup: VACUUM INTO, all projects, 644", backup_is_a_quiescent_copy)


def the_database_is_644():
    mode = os.stat(R.path).st_mode & 0o777
    assert mode == FILE_MODE, oct(mode)
    assert R.status(FP)["database"]["mode"] == oct(FILE_MODE)


case("the database is 644: whoever mounts the share reads and does not touch",
     the_database_is_644)

# =====================================================================
head("verdicts")
# =====================================================================


def status_counts_agree_with_the_lists():
    s = R.status(FP)
    assert s["database"]["integrity"] == "ok"
    assert s["database"]["journal_mode"] == "wal"
    # Counted against the OTHER path rather than against a number typed here: a
    # constant in a suite is a second source of truth, and this project has paid
    # for those before.
    assert s["rules"]["denied"] == len(R.pending(FP)["denied"]) > 0, \
        "a refusal is KEPT, and status and the noticeboard agree on how many"
    assert s["rules"]["proposed"] == len(R.pending(FP)["waiting"]) == 0
    assert s["rules"]["retired"] == R.cx.execute(
        "SELECT COUNT(*) FROM rules WHERE project=? AND status='retired'",
        (NAME_FP,)).fetchone()[0] > 0
    assert s["rules"]["permanent"] == 1
    assert s["approval"]["batches_approved"] > 0
    assert s["approval"]["provisional_days"] == 90
    assert s["registry_version"] == VERSION
    for consumer, n in s["by_consumer"].items():
        assert n == R.list_rules(FP, consumer)["count"], consumer
    assert sum(s["by_domain"].values()) == s["rules"]["in_force"], \
        (s["by_domain"], s["rules"]["in_force"])


case("status: two paths to the same number, and they agree",
     status_counts_agree_with_the_lists)


def the_registry_lists_projects_only_here():
    e = R.projects()
    assert e["count"] == 5
    assert {p["code"] for p in e["projects"]} == {FP, HT, CASA, EMPTY, CNT}
    assert {p["name"]: p["active_rules"] for p in e["projects"]}["Empty"] == 0


case("projects(): the only door codes come out of — gated in the server",
     the_registry_lists_projects_only_here)

# =====================================================================
head("reopening")
# =====================================================================


def reopen_finds_everything_where_it_was():
    versions = R.history(FP, "VA-0001")["count"]
    listed = [x["id"] for x in R.list_rules(FP, "tax")["rules"]]
    R.close()
    r3 = Registry(DB)
    s = r3.status(FP)
    assert s["database"]["integrity"] == "ok" and s["database"]["journal_mode"] == "wal"
    assert r3.projects()["count"] == 5
    assert r3.history(FP, "VA-0001")["count"] == versions
    assert [x["id"] for x in r3.list_rules(FP, "tax")["rules"]] == listed
    r3.close()


case("reopen: WAL, whole, three projects, history and lists intact",
     reopen_finds_everything_where_it_was)

# =====================================================================
head("the upgrade: a database shaped like the versions before this one")
# =====================================================================

# The migration DROPS the relic columns and converts nothing. legacy_id was
# the ledger of a bulk-import world: with the import gone the old->new
# mapping lives in the migration files, outside the registry, and the column
# would be a relic conserved in the clean system — the exact thing the
# seeding pass exists to kill. The signature columns went with the signature.
# Bodies, IDs, versions and refs are untouched: a migration is not code, it
# is the work, and this method's whole job is to leave the work alone.

OLD_DB = os.path.join(D, "legacy.db")


_OLD_TRG_UPD = """
CREATE TRIGGER trg_rules_upd AFTER UPDATE ON rules BEGIN
  INSERT INTO rule_versions (project, rule_id, version, type, title, body,
    status, permanence, expires_at, superseded_by, changelog, scopes, consumers,
    ts, action, reason)
  VALUES (NEW.project, NEW.id,
          (SELECT IFNULL(MAX(version),0)+1 FROM rule_versions
            WHERE project = NEW.project AND rule_id = NEW.id),
          NEW.type, NEW.title, NEW.body, NEW.status, NEW.permanence,
          NEW.expires_at, NEW.superseded_by, NEW.changelog,
          (SELECT IFNULL(GROUP_CONCAT(scope, ', '), '') FROM
            (SELECT scope FROM rule_scopes
              WHERE project = NEW.project AND rule_id = NEW.id ORDER BY scope)),
          '', NEW.updated_at, 'amended', NEW.reason);
END"""


def a_v16_database_loses_the_relic_columns_and_nothing_else():
    o = Registry(OLD_DB)
    o.create_project("Ll11Mm22Nn33", "Legacy", [("architect", "chat")],
                     {"PE": "perimeter", "VA": "vault"})
    # Written the way the old engine wrote it: the ID chosen by the caller, the
    # citations bare, because that is what its parser read.
    for rid, dom, seq, body in (("PE-99", "PE", 99, "The guinea pig. See VA-07."),
                                ("VA-07", "VA", 7, "Cited by PE-99, in prose.")):
        o.cx.execute("BEGIN")
        o.cx.execute("INSERT INTO rule_scopes (project, rule_id, scope) VALUES (?,?,?)",
                     ("Legacy", rid, ALL))
        o.cx.execute(
            "INSERT INTO rules (project, id, domain, seq, type, title, body, status, "
            "permanence, reason, updated_at) "
            "VALUES (?,?,?,?,'R',?,?,'active','provisional','legacy',"
            "'2026-01-01T00:00:00Z')",
            ("Legacy", rid, dom, seq, rid, body))
        o.cx.execute("COMMIT")
    o.cx.execute("INSERT INTO rule_refs (project, src, dst) VALUES ('Legacy','PE-99','VA-07')")
    # Shape the file like a v1.6.0 database: the legacy_id column WITH data in
    # it and its partial unique index, and the signature columns on approvals.
    o.cx.execute("ALTER TABLE rules ADD COLUMN legacy_id TEXT")
    o.cx.execute("UPDATE rules SET legacy_id='OLD-99' "
                 "WHERE project='Legacy' AND id='PE-99'")
    o.cx.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_rules_legacy "
                 "ON rules(project, legacy_id) WHERE legacy_id IS NOT NULL")
    o.cx.execute("ALTER TABLE approvals ADD COLUMN signature TEXT")
    o.cx.execute("ALTER TABLE approvals ADD COLUMN signed INTEGER NOT NULL DEFAULT 1")
    # Photographed AFTER the reshape: the reshape itself writes history (the
    # UPDATE above goes through the trigger, as it must), the migration none.
    before = {r[0]: r[1] for r in o.cx.execute(
        "SELECT id, body FROM rules WHERE project='Legacy'")}
    versions = o.cx.execute("SELECT COUNT(*) FROM rule_versions WHERE project='Legacy'"
                            ).fetchone()[0]
    o.close()

    n = Registry(OLD_DB)
    assert n.repaired == [], f"an upgrade is not a repair: {n.repaired}"
    assert n.migrated == ["rules.legacy_id dropped",
                          "approvals.signature dropped",
                          "approvals.signed dropped"], n.migrated
    after = {r[0]: r[1] for r in n.cx.execute(
        "SELECT id, body FROM rules WHERE project='Legacy'")}
    assert after == before, "not one ID and not one body moved"
    assert n.cx.execute("SELECT COUNT(*) FROM rule_versions WHERE project='Legacy'"
                        ).fetchone()[0] == versions, "and no version was invented"
    assert n.cx.execute("SELECT src, dst FROM rule_refs WHERE project='Legacy'"
                        ).fetchone()[0:2] == ("PE-99", "VA-07")
    cols = {r[1] for r in n.cx.execute("PRAGMA table_info(rules)")}
    assert "legacy_id" not in cols, sorted(cols)
    idx = {r[0] for r in n.cx.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ux_rules_legacy" not in idx, sorted(idx)
    # The counter still walks past the old numbers: nothing about the drop
    # touched the sequence.
    out = n.propose("Ll11Mm22Nn33", "VA", "R", "The first rule of the new corpus",
                    "Body.", ["*"], "the seeding starts", "architect")
    assert out["id"] == "VA-0008", out
    n.close()

    again = Registry(OLD_DB)
    assert again.migrated == [], f"the migration ran twice: {again.migrated}"
    assert again.repaired == []
    again.close()


case("a v1.6.0-shaped database loses the relic columns, and nothing else moves",
     a_v16_database_loses_the_relic_columns_and_nothing_else)

# =====================================================================
head("the engine is used from a THREAD POOL, not from here")
# =====================================================================

# The hole this suite had. Everything above runs on one thread; the server does
# not. FastMCP hands sync tools to anyio.to_thread.run_sync, so the connection
# is opened on the import thread and used from a worker — and sqlite3 refuses
# that outright. The first call that touched the database in production died
# with "SQLite objects created in a thread can only be used in that same
# thread", and no test here could have seen it. Now one can.

R = Registry(DB, provisional_days=90)    # the reopen test closed the last one


def a_worker_thread_can_use_the_engine():
    import threading
    errors: list[Exception] = []

    def read():
        try:
            R.list_rules(FP, "tax")
            R.project_info(FP)
            R.status(FP)
        except Exception as e:                                  # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=read)
    t.start()
    t.join(30)
    assert not t.is_alive(), "the worker hung"
    assert not errors, f"a worker thread cannot read: {errors[0]}"


case("a thread that did not open the connection can still read",
     a_worker_thread_can_use_the_engine)


def many_threads_reading_and_writing():
    """Writes too, and from several threads at once: the transactions in here
    are multi-statement with an explicit BEGIN, so two of them interleaving on
    one connection would COMMIT somebody else's work."""
    import threading
    errors: list[Exception] = []
    done: list[str] = []
    lock = threading.Lock()

    def worker(n: int):
        try:
            # Eight threads asking the SAME counter for a number at the same
            # time. Nobody passes an ID any more, so a race here would not throw
            # — it would hand two rules the same number, and UNIQUE(project,
            # domain, seq) is the last net under that.
            rid = R.propose(FP, "ST", "F", f"Concurrent {n}", f"Body {n}.",
                            ["market-news"], "thread safety")["id"]
            R.list_rules(FP, "market-news")
            R.check(FP)
            R.history(FP, rid)
            with lock:
                done.append(rid)
        except Exception as e:                                  # noqa: BLE001
            with lock:
                errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not any(t.is_alive() for t in threads), "a worker hung: a deadlock?"
    assert not errors, f"{len(errors)} failures, first: {errors[0]}"
    assert len(done) == 8, done
    assert len(set(done)) == 8, f"two threads got the same number: {sorted(done)}"
    # And the writes are all there, none lost and none half-written.
    waiting = {r["id"] for r in R.pending(FP)["waiting"]}
    assert set(done) <= waiting, sorted(waiting)
    assert R.status(FP)["database"]["integrity"] == "ok"


case("eight threads proposing and reading at once: nothing lost, nothing torn",
     many_threads_reading_and_writing)


def two_connections_asking_the_same_counter():
    """The RLock only covers ONE process. The preflight opens the database while
    the server is starting, and a second Registry is one line away — so the
    counter has to hold across CONNECTIONS too, not just across threads.

    This is what BEGIN IMMEDIATE buys. With the default deferred BEGIN the
    transaction reads MAX(seq) first and asks for the write lock afterwards, and
    in WAL that upgrade cannot wait: the loser dies with a raw
    'database is locked' that no busy timeout can help, and it would reach the
    chat as a fault rather than as a refusal."""
    import threading
    engines = [Registry(DB, provisional_days=90) for _ in range(4)]
    got: list[str] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def worker(e, n):
        try:
            rid = e.propose(FP, "RL", "F", f"Across connections {n}", f"Body {n}.",
                            ["market-news"], "two connections")["id"]
            with lock:
                got.append(rid)
        except Exception as exc:                                # noqa: BLE001
            with lock:
                errors.append(exc)

    ts = [threading.Thread(target=worker, args=(e, i)) for i, e in enumerate(engines)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    for e in engines:
        e.close()
    assert not errors, f"a second connection could not file: {errors[0]!r}"
    assert len(set(got)) == 4, f"two connections got the same number: {sorted(got)}"


case("four connections asking the same counter at once get four numbers",
     two_connections_asking_the_same_counter)


def the_lock_is_reentrant():
    """status() calls list_rules(), import_rules() calls check(). With a plain
    Lock instead of an RLock this deadlocks instead of failing, which is the
    worse way to break: the container would hang, not crash."""
    import threading
    ok_flag = []

    def call():
        R.status(FP)
        ok_flag.append(True)

    t = threading.Thread(target=call)
    t.start()
    t.join(15)
    assert ok_flag, "status() hung — the lock is not re-entrant"


case("a public method calling another does not deadlock", the_lock_is_reentrant)

# =====================================================================
head("the reason is immutable, and the registry has TWO readings")
# =====================================================================

# The defect measured on 2026-08-10: the `reason` column kept the why of the
# last EVENT, not the why of the rule — approve rewrote it with 'approved',
# renew with 'renewed' — and no reading tool returned it at all, so whoever
# signed a batch was approving reasons they could not see. From here on the
# column keeps what its name promises, the events land in a column of their
# own, and the registry has two readings: the consumer's — the ID and the
# body, and nothing else — and the maintainer's, everything, `reason` first.

C1 = "Cc11Rr22Ss33"
C1_NAME = "C1 Reason"
WHY = "because the aggregator misreports the closing price"

case("create the C1 bench project", lambda: R.create_project(
    C1, C1_NAME, [("architect", "chat"), ("advisory", "chat")],
    {"VA": "vault", "PE": "perimeter"}, "the immutability bench"))


def _c1(rid):
    return R.cx.execute("SELECT * FROM rules WHERE project=? AND id=?",
                        (C1_NAME, rid)).fetchone()


def _c1_versions(rid):
    return R.cx.execute("SELECT version, action, reason FROM rule_versions "
                        "WHERE project=? AND rule_id=? ORDER BY version",
                        (C1_NAME, rid)).fetchall()


def reason_survives_every_event():
    rid = R.propose(C1, "VA", "R", "The immutable guinea pig", "Body.",
                    ["*"], WHY, "architect")["id"]
    b = R.batch(C1)
    R.approve(C1, b["digest"])
    assert _c1(rid)["reason"] == WHY, \
        f"approve rewrote reason to {_c1(rid)['reason']!r}"
    R.renew(C1, [rid])
    assert _c1(rid)["reason"] == WHY, \
        f"renew rewrote reason to {_c1(rid)['reason']!r}"
    R.promote(C1, [rid])
    assert _c1(rid)["reason"] == WHY, \
        f"promote rewrote reason to {_c1(rid)['reason']!r}"
    ver = len(_c1_versions(rid))
    R.amend(C1, rid, ver, "renamed on the bench", title="The guinea pig, renamed")
    assert _c1(rid)["reason"] == WHY, \
        f"amend rewrote reason to {_c1(rid)['reason']!r}"


case("no event rewrites the reason: approve, renew, promote, amend",
     reason_survives_every_event)


def the_events_land_in_their_own_column():
    cols = {r[1] for r in R.cx.execute("PRAGMA table_info(rules)")}
    assert "event" in cols, \
        "the rules table has no `event` column: the events still live in `reason`"
    rid = "VA-0001"
    assert _c1(rid)["event"] == "renamed on the bench", \
        f"after the amend the event column says {_c1(rid)['event']!r}"
    # A denial: the reason stays the author's, the event says what happened,
    # and denied_reason keeps carrying the maintainer's why as before.
    rid2 = R.propose(C1, "VA", "R", "To be denied", "Body.", ["*"],
                     "a why that must survive the denial", "advisory")["id"]
    R.deny(C1, [rid2], "bench cleanup")
    assert _c1(rid2)["reason"] == "a why that must survive the denial", \
        f"deny rewrote reason to {_c1(rid2)['reason']!r}"
    assert _c1(rid2)["event"] == "denied", _c1(rid2)["event"]
    assert _c1(rid2)["denied_reason"] == "bench cleanup"
    # A retirement: same shape, the why of the event is the event.
    rid3 = R.propose(C1, "PE", "R", "To be retired", "Body.", ["*"],
                     "born to die on the bench", "architect")["id"]
    b = R.batch(C1)
    R.approve(C1, b["digest"])
    R.retire(C1, rid3, "the bench is done with it")
    assert _c1(rid3)["reason"] == "born to die on the bench", \
        f"retire rewrote reason to {_c1(rid3)['reason']!r}"
    assert _c1(rid3)["event"] == "the bench is done with it"


case("the events land in `event`, and denied_reason still works",
     the_events_land_in_their_own_column)


def history_keeps_the_why_at_v1_and_the_event_after():
    rows = _c1_versions("VA-0001")
    assert rows[0]["reason"] == WHY, \
        f"version 1 lost the propose reason: {rows[0]['reason']!r}"
    by_action = {r["action"]: r["reason"] for r in rows}
    assert by_action.get("amended") in ("approved", "renewed",
                                        "promoted to permanent",
                                        "renamed on the bench"), \
        f"the update trigger no longer records the event: {dict(by_action)}"
    assert rows[-1]["reason"] == "renamed on the bench", \
        f"the last version's reason is {rows[-1]['reason']!r}, not the last event"


case("history: version 1 keeps the why, the later versions keep the events",
     history_keeps_the_why_at_v1_and_the_event_after)


def the_consumer_reading_is_the_id_and_the_body():
    lst = R.list_rules(C1, "advisory")["rules"]
    assert lst, "no rule in force reached advisory"
    for d in lst:
        assert set(d) == {"id", "body"}, \
            f"rules_list leaks {sorted(set(d) - {'id', 'body'})} to a consumer"
    got = R.get_rules(C1, [lst[0]["id"]], "advisory")["found"]
    assert got, "rules_get found nothing"
    for d in got:
        assert set(d) == {"id", "body"}, \
            f"rules_get leaks {sorted(set(d) - {'id', 'body'})} to a consumer"
    hits = R.search(C1, "guinea", "advisory")["hits"]
    assert hits, "rules_search found nothing"
    for d in hits:
        assert set(d) == {"id", "body"}, \
            f"rules_search leaks {sorted(set(d) - {'id', 'body'})} to a consumer"
    assert "(" in got[0]["body"] or got[0]["body"] == "Body.", got[0]["body"]


case("the consumer reading is the ID and the body, and nothing else",
     the_consumer_reading_is_the_id_and_the_body)


def the_order_is_still_the_breadth():
    # One rule through _ALL_ (breadth 2 here), one through a singleton: the
    # widest must come first even though the fields that said so are gone.
    rid_narrow = R.propose(C1, "PE", "R", "For the architect alone", "Body.",
                           ["architect"], "narrow on purpose", "architect")["id"]
    b = R.batch(C1)
    R.approve(C1, b["digest"])
    ids = [d["id"] for d in R.list_rules(C1, "architect")["rules"]]
    assert ids.index("VA-0001") < ids.index(rid_narrow), \
        f"the widest rule no longer comes first: {ids}"


case("the order is still the breadth, with the fields gone",
     the_order_is_still_the_breadth)


def the_maintenance_reading_carries_the_why():
    rid = R.propose(C1, "PE", "R", "Waiting for the batch", "Body.", ["*"],
                    "so the batch shows the why", "architect")["id"]
    b = R.batch(C1)
    mine = [p for p in b["proposals"] if p["id"] == rid]
    assert mine, "the proposal is not in the batch"
    assert mine[0].get("reason") == "so the batch shows the why", \
        "rules_batch does not show the reason being approved"
    R.deny(C1, [rid], "bench cleanup")
    md = R.export(C1)["markdown"]
    assert WHY in md, "rules_export does not carry the reason"
    per_consumer = R.export(C1, "architect")["markdown"]
    assert WHY in per_consumer, \
        "the per-consumer export does not carry the reason"


case("the maintenance reading carries the why: batch and export",
     the_maintenance_reading_carries_the_why)


C1M_DB = os.path.join(D, "c1-migration.db")

def an_old_database_gains_the_event_column_and_the_new_trigger():
    o = Registry(C1M_DB)
    o.create_project("Mm11Nn22Oo33", "Migration bench", [("architect", "chat")],
                     {"VA": "vault"})
    rid = o.propose("Mm11Nn22Oo33", "VA", "R", "Old-world rule", "Body.", ["*"],
                    "the original why", "architect")["id"]
    b = o.batch("Mm11Nn22Oo33")
    o.approve("Mm11Nn22Oo33", b["digest"])
    cols = {r[1] for r in o.cx.execute("PRAGMA table_info(rules)")}
    assert "event" in cols, \
        "the rules table has no `event` column: the events still live in `reason`"
    # Reshape the file the way v1.5.0 left it: no `event` column, the update
    # trigger copying NEW.reason into the history, `reason` already
    # overwritten by the last event, the legacy_id column with its index, and
    # the signature columns still on approvals. The dirt is PART of the shape.
    o.cx.execute("DROP TRIGGER trg_rules_upd")
    o.cx.execute("UPDATE rules SET reason='approved' "
                 "WHERE project='Migration bench'")
    o.cx.execute("ALTER TABLE rules DROP COLUMN event")
    o.cx.execute(_OLD_TRG_UPD)
    o.cx.execute("ALTER TABLE rules ADD COLUMN legacy_id TEXT")
    o.cx.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_rules_legacy "
                 "ON rules(project, legacy_id) WHERE legacy_id IS NOT NULL")
    o.cx.execute("DROP INDEX ux_rules_supersedes")
    o.cx.execute("ALTER TABLE rules DROP COLUMN supersedes")
    o.cx.execute("ALTER TABLE approvals ADD COLUMN signature TEXT")
    o.cx.execute("ALTER TABLE approvals ADD COLUMN signed INTEGER NOT NULL DEFAULT 1")
    versions = o.cx.execute("SELECT COUNT(*) FROM rule_versions "
                            "WHERE project='Migration bench'").fetchone()[0]
    o.close()

    n = Registry(C1M_DB)
    assert n.repaired == [], \
        f"an upgrade is not a repair — the supersedes index rode in with its " \
        f"column and must not be reported: {n.repaired}"
    assert n.migrated == ["rules.event", "rules.supersedes",
                          "rules.legacy_id dropped",
                          "trg_rules_upd",
                          "approvals.signature dropped",
                          "approvals.signed dropped"], n.migrated
    assert "supersedes" in {r[1] for r in n.cx.execute("PRAGMA table_info(rules)")}
    assert "ux_rules_supersedes" in {r[0] for r in n.cx.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    # NOTHING was converted: the reason v1.5.0 dirtied stays dirty. It is
    # gym data and it dies with the reset — a migration that rewrote it would
    # be inventing a why nobody wrote.
    r = n.cx.execute("SELECT reason, event FROM rules "
                     "WHERE project='Migration bench' AND id=?", (rid,)).fetchone()
    assert r["reason"] == "approved", f"the migration touched reason: {r['reason']!r}"
    assert r["event"] is None, f"the migration invented an event: {r['event']!r}"
    assert n.cx.execute("SELECT COUNT(*) FROM rule_versions "
                        "WHERE project='Migration bench'").fetchone()[0] == versions, \
        "the migration invented a version"
    # The NEW trigger must be in place, not the old one it replaced: the next
    # event has to land in the history as the event, not as the frozen reason.
    n.renew("Mm11Nn22Oo33", [rid])
    r = n.cx.execute("SELECT reason, event FROM rules "
                     "WHERE project='Migration bench' AND id=?", (rid,)).fetchone()
    assert r["reason"] == "approved" and r["event"] == "renewed", dict(r)
    last = n.cx.execute("SELECT reason FROM rule_versions "
                        "WHERE project='Migration bench' AND rule_id=? "
                        "ORDER BY version DESC LIMIT 1", (rid,)).fetchone()
    assert last["reason"] == "renewed", \
        f"the update trigger still copies the frozen reason: {last['reason']!r}"
    n.close()

    again = Registry(C1M_DB)
    assert again.migrated == [], f"the migration ran twice: {again.migrated}"
    assert again.repaired == []
    again.close()


case("an old database gains the event column and the rebuilt trigger, "
     "and nothing else moves",
     an_old_database_gains_the_event_column_and_the_new_trigger)


# =====================================================================
head("the bulk import is gone, and legacy_id with it")
# =====================================================================

# Decided 2026-08-10, and executed here: an import stamps one reason across
# the batch, files everything permanent so rule five never starts, and asks
# no questions — the seeding pass is not the price of the migration, it is
# its content. The old->new mapping lives in the migration files, outside
# the registry: relics do not enter the clean system.


def the_import_door_is_bricked():
    import rules as _r
    assert not hasattr(R, "import_rules"), "import_rules is still on the Registry"
    assert not hasattr(_r, "MAX_IMPORT"), "MAX_IMPORT is still declared"
    assert not hasattr(_r, "RE_BARE_LEGACY"), "the legacy citation parser survives"
    assert not hasattr(_r, "RE_LEGACY"), "the legacy_id validator survives"
    assert not hasattr(_r.Registry, "_legacy_cites"), "_legacy_cites survives"


case("the import door is bricked: no method, no ceiling, no legacy parser",
     the_import_door_is_bricked)


def no_rule_carries_a_legacy_id():
    import inspect
    params = list(inspect.signature(R.propose).parameters)
    assert "legacy_id" not in params, f"propose still takes legacy_id: {params}"
    cols = {r[1] for r in R.cx.execute("PRAGMA table_info(rules)")}
    assert "legacy_id" not in cols, f"the rules table still has legacy_id: {sorted(cols)}"
    idx = {r[0] for r in R.cx.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ux_rules_legacy" not in idx, "the legacy unique index survives"


case("no rule carries a legacy identifier: no column, no index, no parameter",
     no_rule_carries_a_legacy_id)


# =====================================================================
head("the supersede is atomic: propose names the victim, approve does both")
# =====================================================================

# F6, decided 2026-08-10. A rule has three fates: it holds, it retires, or it
# gets CHANGED — and the third used to be two separate steps, held together by
# discipline. Now `supersedes` is a dedicated field on the proposal, never a
# citation in the body: at approval, in the SAME transaction, the heir goes
# active and the superseded rule is retired pointing forward. At denial the
# old rule is not touched.

SUP = "Su9p3Rc55dd"
SUP_NAME = "Supersede bench"

case("create the supersede bench", lambda: R.create_project(
    SUP, SUP_NAME, [("architect", "chat"), ("advisory", "chat")],
    {"VA": "vault"}))

OLD_RULE = R.propose(SUP, "VA", "R", "The rule to be replaced", "Old body.",
                     ["*"], "born to be superseded", "architect")["id"]
R.approve(SUP, R.batch(SUP)["digest"])


def _sup(rid):
    return R.cx.execute("SELECT * FROM rules WHERE project=? AND id=?",
                        (SUP_NAME, rid)).fetchone()


refuses("supersedes towards a rule that was never defined",
        lambda: R.propose(SUP, "VA", "R", "x", "y", ["*"], "m", "architect",
                          supersedes="VA-0099"),
        "never defined", RulesError)


def a_proposal_cannot_supersede_a_proposal():
    pending = R.propose(SUP, "VA", "R", "Still pending", "Body.", ["*"],
                        "m", "architect")["id"]
    try:
        R.propose(SUP, "VA", "R", "x", "y", ["*"], "m", "architect",
                  supersedes=pending)
        raise AssertionError("it should have refused")
    except RulesError as e:
        assert "not in force" in str(e).lower(), e
    R.deny(SUP, [pending], "bench cleanup")


case("supersedes towards a rule not in force is refused at the door",
     a_proposal_cannot_supersede_a_proposal)


def the_second_pending_supersede_is_refused_by_the_database():
    """A partial UNIQUE index, not a Python check: two pending proposals
    superseding the same rule cannot coexist no matter which door they came
    through."""
    idx = {r[0] for r in R.cx.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ux_rules_supersedes" in idx, sorted(idx)
    first = R.propose(SUP, "VA", "R", "Heir one", "Body one.", ["*"],
                      "the first heir", "architect", supersedes=OLD_RULE)["id"]
    try:
        R.propose(SUP, "VA", "R", "Heir two", "Body two.", ["*"],
                  "the second heir", "architect", supersedes=OLD_RULE)
        raise AssertionError("it should have refused")
    except RulesError as e:
        assert "already" in str(e).lower(), e
    # Deny frees the slot: the index watches PENDING proposals only.
    R.deny(SUP, [first], "make room")
    assert _sup(OLD_RULE)["status"] == "active", \
        "denying the heir must not touch the old rule"


case("a second pending proposal on the same victim is refused by the database",
     the_second_pending_supersede_is_refused_by_the_database)


def approve_swaps_the_two_in_one_transaction():
    heir = R.propose(SUP, "VA", "R", "The heir", "New body.", ["advisory"],
                     "the decision changed", "architect",
                     supersedes=OLD_RULE)["id"]
    b = R.batch(SUP)
    mine = [p for p in b["proposals"] if p["id"] == heir]
    assert mine and mine[0].get("supersedes") == OLD_RULE, \
        "the batch does not SHOW the supersede: whoever approves must see the retirement"
    out = R.approve(SUP, b["digest"])
    assert out["superseded"] == [{"retired": OLD_RULE, "by": heir}], out
    old = _sup(OLD_RULE)
    assert old["status"] == "retired" and old["superseded_by"] == heir, dict(old)
    new = _sup(heir)
    assert new["status"] == "active", dict(new)
    # The heir DECLARES its scopes: nothing was inherited from the victim.
    scopes = [r[0] for r in R.cx.execute(
        "SELECT scope FROM rule_scopes WHERE project=? AND rule_id=?",
        (SUP_NAME, heir))]
    assert scopes == ["advisory"], scopes
    # And the reading expands the retired rule pointing forward.
    probe = R.propose(SUP, "VA", "R", "Probe", f"See ({OLD_RULE}).", ["*"],
                      "m", "architect")["id"]
    R.approve(SUP, R.batch(SUP)["digest"])
    body = [d for d in R.list_rules(SUP, "architect")["rules"]
            if d["id"] == probe][0]["body"]
    assert f"superseded by {heir}" in body, body


case("approve activates the heir AND retires the victim, one transaction",
     approve_swaps_the_two_in_one_transaction)


def a_victim_retired_in_the_meantime_is_a_declared_noop():
    target = R.propose(SUP, "VA", "R", "To vanish early", "Body.", ["*"],
                       "m", "architect")["id"]
    R.approve(SUP, R.batch(SUP)["digest"])
    heir = R.propose(SUP, "VA", "R", "Late heir", "Body.", ["*"], "m",
                     "architect", supersedes=target)["id"]
    R.retire(SUP, target, reason="retired while the heir was pending")
    out = R.approve(SUP, R.batch(SUP)["digest"])
    assert out["supersede_skipped"] == [
        {"id": heir, "target": target, "why": "no longer in force"}], out
    assert _sup(target)["superseded_by"] is None, \
        "a rule somebody else retired is not rewritten behind their back"


case("a victim already retired at approval: declared no-op, nothing rewritten",
     a_victim_retired_in_the_meantime_is_a_declared_noop)


# =====================================================================
head("the consumer brief: identity travels with the rules, versioned")
# =====================================================================

# F1: the mandate that used to live in a role's memory file. rules_list
# returns it FIRST, before the rules, in the same call — "you are so-and-so,
# and these are your rules" in one round trip. Empty is not an error. It is
# written behind the admin code (an extension of consumers_add, not a new
# door), versioned by the same trigger mechanism as the rules, and for
# skills it stays empty by editorial discipline, not by a branch in the code.

BRF = "Br7f2Xk44mm"
BRF_NAME = "Brief bench"

case("create the brief bench, one consumer born WITH its brief", lambda: R.create_project(
    BRF, BRF_NAME,
    [{"name": "architect", "kind": "chat",
      "brief": "# Architect\n\nYou maintain the corpus."},
     ("worker", "skill")],
    {"VA": "vault"}))


def the_brief_arrives_first_and_empty_is_not_an_error():
    out = R.list_rules(BRF, "architect")
    assert out["brief"] == "# Architect\n\nYou maintain the corpus.", out.get("brief")
    keys = list(out)
    assert keys.index("brief") < keys.index("rules"), \
        f"the brief must come BEFORE the rules: {keys}"
    bare = R.list_rules(BRF, "worker")
    assert bare["brief"] == "", \
        f"a consumer without a brief gets an empty field, not an error: {bare.get('brief')!r}"


case("rules_list leads with the brief, and empty is not an error",
     the_brief_arrives_first_and_empty_is_not_an_error)


def the_brief_is_written_through_consumers_add_and_versioned():
    # On an EXISTING consumer, add_consumers with a brief updates it: the
    # extension of the door that already exists, not a new one.
    R.add_consumers(BRF, [{"name": "worker", "kind": "skill",
                           "brief": "worker now has a mandate"}])
    assert R.list_rules(BRF, "worker")["brief"] == "worker now has a mandate"
    rows = R.cx.execute(
        "SELECT version, action, brief FROM consumer_versions "
        "WHERE project=? AND consumer='worker' ORDER BY version",
        (BRF_NAME,)).fetchall()
    assert [r["action"] for r in rows] == ["created", "amended"], \
        [dict(r) for r in rows]
    assert rows[0]["brief"] is None and rows[-1]["brief"] == "worker now has a mandate"


case("the brief writes through consumers_add, and the triggers keep versions",
     the_brief_is_written_through_consumers_add_and_versioned)


def a_hand_edit_of_the_brief_still_lands_in_history():
    R.cx.execute("UPDATE consumers SET brief='edited by hand' "
                 "WHERE project=? AND name='worker'", (BRF_NAME,))
    last = R.cx.execute(
        "SELECT brief, action FROM consumer_versions "
        "WHERE project=? AND consumer='worker' ORDER BY version DESC LIMIT 1",
        (BRF_NAME,)).fetchone()
    assert last["brief"] == "edited by hand" and last["action"] == "amended", dict(last)


case("a brief edited by hand with sqlite3 is versioned too: the trigger writes",
     a_hand_edit_of_the_brief_still_lands_in_history)

refuses("a brief over the body ceiling",
        lambda: R.add_consumers(BRF, [{"name": "worker", "kind": "skill",
                                       "brief": "z" * (MAX_BODY_BYTES + 1)}]),
        "split", RulesError)

print(f"\n{OK} passed, {FAIL} failed")
if FAILURES:
    print("failed: " + "; ".join(FAILURES))
sys.exit(1 if FAIL else 0)
