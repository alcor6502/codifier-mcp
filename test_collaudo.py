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

import base64
import hashlib
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cryptography.hazmat.primitives import serialization as ser
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from rules import (ALL, FILE_MODE, MAX_BODY_BYTES, MAX_GET_IDS, MAX_IMPORT,
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


# ed25519 signer. The private half lives here only because this is a test: in
# real life it never enters a conversation.
SK = Ed25519PrivateKey.generate()
PUB = base64.b64encode(
    SK.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)).decode()
OTHER_SK = Ed25519PrivateKey.generate()


def sign(msg: str) -> str:
    return base64.b64encode(SK.sign(msg.encode())).decode()


def sign_other(msg: str) -> str:
    return base64.b64encode(OTHER_SK.sign(msg.encode())).decode()


def digest_of(kind: str, project: str, ids: list[str]) -> str:
    return hashlib.sha256(f"{kind}|{project}|{','.join(sorted(ids))}".encode()).hexdigest()


D = tempfile.mkdtemp(prefix="collaudo-")
DB = os.path.join(D, "rules.db")
R = Registry(DB, public_key=PUB, provisional_days=90)

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
head("proposing: a proposal reaches nobody")
# =====================================================================

case("propose a rule for everyone", lambda: R.propose(
    FP, "VA-02", "R", "Re-read the sources",
    "SOURCE data is re-read right before writing the derivative. See ST-07.",
    ["*"], "initial import", "architect"))
case("propose a rule for the group", lambda: R.propose(
    FP, "PE-01", "M", "The method of the four", "The four deliberative chats agree first.",
    ["deliberativi"], "initial import", "architect"))
case("propose a rule for one consumer", lambda: R.propose(
    FP, "FI-03", "M", "Estimating the bracket",
    "The bracket is estimated from the rollup by character. Cross-check with VE-03.",
    ["tax"], "initial import", "tax"))
case("propose a rule for a skill", lambda: R.propose(
    FP, "ST-03", "F", "Where the vault lives", "The vault root is read, never assumed.",
    ["update-tax"], "initial import", "architect"))

ok(R.list_rules(FP, "tax")["count"] == 0, "a proposal reaches nobody before approval")
ok(R.pending(FP, "tax")["waiting"][0]["id"] == "FI-03",
   "the noticeboard shows the consumer's own proposal")
ok(len(R.pending(FP)["waiting"]) == 4, "without a consumer the noticeboard shows them all")

refuses("an undeclared domain",
        lambda: R.propose(FP, "ZZ-01", "R", "x", "y", ["*"], "m"), "not declared", RulesError)
refuses("another project's domain",
        lambda: R.propose(FP, "MS-01", "R", "x", "y", ["*"], "m"), "not declared", RulesError)
refuses("another project's consumer as a scope",
        lambda: R.propose(FP, "VA-03", "R", "x", "y", ["coach"], "m"),
        "neither a consumer nor a scope", RulesError)
refuses("type X",
        lambda: R.propose(FP, "VA-03", "X", "x", "y", ["*"], "m"),
        "R binding, M method", RulesError)
refuses("no reason",
        lambda: R.propose(FP, "VA-03", "R", "x", "y", ["*"], ""), "reason is mandatory", RulesError)
refuses("no title",
        lambda: R.propose(FP, "VA-03", "R", "", "y", ["*"], "m"), "needs a title", RulesError)
refuses("no body",
        lambda: R.propose(FP, "VA-03", "R", "x", "", ["*"], "m"), "needs a body", RulesError)
refuses("empty perimeter",
        lambda: R.propose(FP, "VA-03", "R", "x", "y", [], "m"), "reaches nobody", RulesError)
refuses("malformed ID",
        lambda: R.propose(FP, "VA2", "R", "x", "y", ["*"], "m"), "malformed ID", RulesError)
refuses("a body over the ceiling",
        lambda: R.propose(FP, "VA-03", "R", "x", "z" * (MAX_BODY_BYTES + 1), ["*"], "m"),
        "split the rule", RulesError)
refuses("proposed_by must be a consumer",
        lambda: R.propose(FP, "VA-03", "R", "x", "y", ["*"], "m", "alfredo"),
        "unknown consumer", RulesError)
refuses("an ID already taken",
        lambda: R.propose(FP, "VA-02", "R", "x", "y", ["*"], "m"),
        "never reused", RulesError)


def deferred_fk_photographs_a_full_perimeter():
    """The engine writes rule_scopes BEFORE the rule, inside one transaction, so
    the AFTER INSERT trigger sees a complete perimeter. If the FK were not
    DEFERRED this would not be possible — and version 1 would say 'no scope'."""
    v1 = R.history(FP, "PE-01")["versions"][0]
    assert v1["action"] == "created"
    assert v1["scopes"] == "deliberativi", v1["scopes"]
    assert set(v1["consumers"].split(",")) == {"architect", "advisory", "alt-funds", "tax"}


case("version 1 already carries the perimeter (deferred FK)",
     deferred_fk_photographs_a_full_perimeter)

# =====================================================================
head("the batch, and the signature on it")
# =====================================================================

B = R.batch(FP)
ok(B["count"] == 4, "the batch holds the four proposals", B["ids"])
ok(B["digest"] == R.batch(FP)["digest"], "the digest is stable while the batch is")
refuses("a digest that is not the current one",
        lambda: R.approve(FP, "deadbeef", sign("deadbeef")), "not the current one", RulesError)
refuses("a signature made over another message",
        lambda: R.approve(FP, B["digest"], sign("something else")),
        "does not match this digest", RulesError)
refuses("a signature from another key",
        lambda: R.approve(FP, B["digest"], sign_other(B["digest"])),
        "does not match this digest", RulesError)
refuses("a signature that is not base64",
        lambda: R.approve(FP, B["digest"], "not base64 at all!"),
        "not valid base64", RulesError)

A = R.approve(FP, B["digest"], sign(B["digest"]))
ok(A["signed"] and A["count"] == 4, "batch approved with a valid signature")
ok(R.batch(FP)["count"] == 0, "the batch is empty afterwards")
refuses("nothing to approve on an empty batch",
        lambda: R.approve(FP, R.batch(FP)["digest"], sign(R.batch(FP)["digest"])),
        "batch is empty", RulesError)


def approved_means_provisional():
    row = R._row(NAME_FP, "VA-02")
    assert row["status"] == "active" and row["permanence"] == "provisional"
    assert row["expires_at"], "an approved rule expires: staying costs a decision"


case("approved is ACTIVE and PROVISIONAL, with an expiry", approved_means_provisional)


def the_batch_changes_under_you():
    R.propose(FP, "VE-01", "R", "Late arrival", "Proposed after you read the batch.",
              ["*"], "test")
    b2 = R.batch(FP)
    assert b2["digest"] != B["digest"], "one more proposal must move the digest"
    R.deny(FP, ["VE-01"], "only here to move the digest")


case("a proposal arriving later changes the digest", the_batch_changes_under_you)

# =====================================================================
head("the order IS the breadth")
# =====================================================================

L = R.list_rules(FP, "tax")
ok([x["id"] for x in L["rules"]] == ["VA-02", "PE-01", "FI-03"],
   "order: _ALL_, then the group, then the singleton", [x["id"] for x in L["rules"]])
ok(L["rules"][0]["via"] == [ALL] and L["rules"][0]["breadth"] == 6,
   "list_rules reports via and breadth")
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

V_BEFORE = R.history(FP, "PE-01")["count"]
W = R.widen(FP, "PE-01", ["market-news"])
ok("market-news" in W["reaches"], "PE-01 now reaches market-news too")
ok(R.history(FP, "PE-01")["count"] == V_BEFORE + 1, "widening writes a version")
ok(R._members(NAME_FP, "deliberativi") == ["advisory", "alt-funds", "architect", "tax"],
   "the group is untouched", R._members(NAME_FP, "deliberativi"))


def via_differs_by_consumer():
    for r in R.list_rules(FP, "market-news")["rules"]:
        if r["id"] == "PE-01":
            assert r["via"] == ["market-news"], r["via"]
    for r in R.list_rules(FP, "architect")["rules"]:
        if r["id"] == "PE-01":
            assert r["via"] == ["deliberativi"], r["via"]


case("`via` says where the rule reaches you FROM", via_differs_by_consumer)


def widest_scope_decides_the_position():
    ids = [x["id"] for x in R.list_rules(FP, "architect")["rules"]]
    assert ids.index("VA-02") < ids.index("PE-01"), ids
    r = [x for x in R.list_rules(FP, "architect")["rules"] if x["id"] == "PE-01"][0]
    assert r["breadth"] == 4


case("a rule appears ONCE, positioned by its widest scope",
     widest_scope_decides_the_position)

ok(R.widen(FP, "PE-01", ["market-news"])["added"] == [], "widening twice adds nothing")
refuses("widening onto something that is not a scope",
        lambda: R.widen(FP, "PE-01", ["nobody"]), "neither a consumer nor a scope", RulesError)
refuses("widening a rule that was never defined",
        lambda: R.widen(FP, "VE-99", ["tax"]), "never defined", RulesError)

# =====================================================================
head("history is a photograph, not a pointer")
# =====================================================================


def history_keeps_the_consumers_of_that_day():
    before = [v for v in R.history(FP, "PE-01")["versions"] if v["action"] == "created"][0]
    assert "alt-funds" in before["consumers"]
    R.edit_scope(FP, "deliberativi", remove=["alt-funds"])
    after = [v for v in R.history(FP, "PE-01")["versions"] if v["action"] == "created"][0]
    assert after["consumers"] == before["consumers"], \
        f"the past was rewritten: {before['consumers']} -> {after['consumers']}"
    assert "alt-funds" in after["consumers"]
    # and the present did move
    assert "alt-funds" not in R._members(NAME_FP, "deliberativi")
    assert "PE-01" not in [x["id"] for x in R.list_rules(FP, "alt-funds")["rules"]]


case("a version written BEFORE a scope changed still reports the consumers of then",
     history_keeps_the_consumers_of_that_day)

case("alt-funds goes back into the group",
     lambda: R.edit_scope(FP, "deliberativi", add=["alt-funds"]))
ok(R._breadth(NAME_FP, "deliberativi") == 4, "the group is back to four")


def history_separates_intention_from_effect():
    v = R.history(FP, "PE-01")["versions"][-1]
    assert "deliberativi" in v["scopes"], v["scopes"]
    assert "market-news" in v["consumers"], v["consumers"]


case("scopes says the intention, consumers says the effect",
     history_separates_intention_from_effect)

refuses("history of an ID never defined",
        lambda: R.history(FP, "VE-98"), "never defined", RulesError)

# =====================================================================
head("amending: same ID, a defect fixed")
# =====================================================================


def check_finds_the_broken_pointer():
    v = R.check(FP)
    assert {"from": "VA-02", "cites": "ST-07"} in v["broken_pointers"], v["broken_pointers"]
    assert not v["coherent"]
    assert R.check(HT)["broken_pointers"] == [], "references do not spill between projects"


case("check finds ST-07 broken, and stays inside the project",
     check_finds_the_broken_pointer)


def amend_rewrites_the_refs():
    v = R.get_rules(FP, "VA-02", "tax")["found"][0]["version"]
    R.amend(FP, "VA-02", v, reason="dropped the broken pointer",
            body="SOURCE data is re-read right before writing the derivative.")
    assert R.get_rules(FP, "VA-02", "tax")["found"][0]["version"] == v + 1
    broken = R.check(FP)["broken_pointers"]
    assert {"from": "VA-02", "cites": "ST-07"} not in broken
    assert {"from": "FI-03", "cites": "VE-03"} in broken, "the others stay"


case("amend: a new version, and the citations recomputed", amend_rewrites_the_refs)

refuses("a stale version is refused (compare-and-swap)",
        lambda: R.amend(FP, "VA-02", 1, reason="m", body="z"), "someone wrote", RulesError)
refuses("amend without a reason",
        lambda: R.amend(FP, "VA-02", R._version(NAME_FP, "VA-02"), reason=""),
        "reason is mandatory", RulesError)
refuses("amend an ID never defined",
        lambda: R.amend(FP, "VE-97", 1, reason="m"), "never defined", RulesError)


def history_reads_like_a_story():
    s = R.history(FP, "VA-02")
    actions = [x["action"] for x in s["versions"]]
    assert actions[0] == "created" and "amended" in actions, actions
    assert s["versions"][-1]["reason"] == "dropped the broken pointer"


case("history: the actions and the reasons are the right ones", history_reads_like_a_story)


def compare_shows_what_changed():
    n = R.history(FP, "VA-02")["count"]
    c = R.compare(FP, "VA-02", 1, n)
    assert "ST-07" in c["diff"] and not c["identical"]
    assert R.compare(FP, "VA-02", n, n)["identical"]


case("compare: the diff shows what moved", compare_shows_what_changed)

refuses("comparing against a version that does not exist",
        lambda: R.compare(FP, "VA-02", 1, 99), "does not exist", RulesError)

# =====================================================================
head("reading: three different answers, and no oracle")
# =====================================================================

ok(R.get_rules(FP, "FI-03", "tax")["found"][0]["id"] == "FI-03", "your own rule: you read it")
ok(R.get_rules(FP, "FI-03-M", "tax")["found"][0]["id"] == "FI-03",
   "a citation carrying the type suffix is tolerated")


def three_answers_kept_apart():
    g = R.get_rules(FP, ["VA-02", "FI-03", "VE-99"], "market-news")
    assert [x["id"] for x in g["found"]] == ["VA-02"]
    assert g["not_yours"][0]["id"] == "FI-03" and "tax" in g["not_yours"][0]["held_by"]
    assert g["never_defined"] == ["VE-99"]
    assert "BROKEN CITATION" in g["warning"]


case("found · not_yours · never_defined, one call", three_answers_kept_apart)


def no_oracle_across_projects():
    g = R.get_rules(HT, ["FI-03"], "coach")      # FI-03 exists, but in the other project
    assert g["never_defined"] == ["FI-03"]
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
    assert s["count"] == 1 and s["hits"][0]["id"] == "FI-03"
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

case("a duplicate proposal arrives", lambda: R.propose(
    FP, "FI-04", "R", "Duplicate", "Says the same as FI-03.", ["tax"], "worth a try", "tax"))
case("denial needs no signature — refusing cannot do harm",
     lambda: R.deny(FP, ["FI-04"], "single case, not a pattern"))
refuses("an ID already denied cannot come back",
        lambda: R.propose(FP, "FI-04", "R", "Duplicate", "x", ["tax"], "y", "tax"),
        "already DENIED", RulesError)
refuses("denying without a reason",
        lambda: R.deny(FP, ["VA-02"], ""), "say why", RulesError)
refuses("denying something that is not a proposal",
        lambda: R.deny(FP, ["VA-02"], "because"), "not a pending proposal", RulesError)
refuses("denying an ID that does not exist",
        lambda: R.deny(FP, ["VE-96"], "because"), "no such proposal", RulesError)


def the_noticeboard_replaces_the_chat_memory():
    p = R.pending(FP, "tax")
    assert len(p["denied"]) == 1
    assert p["denied"][0]["denied_reason"].startswith("single case")
    assert p["waiting"] == []


case("the noticeboard shows the denial WITH its reason",
     the_noticeboard_replaces_the_chat_memory)

# =====================================================================
head("expiry, renewal, promotion")
# =====================================================================


def an_expired_provisional_leaves_by_itself():
    R.cx.execute("UPDATE rules SET expires_at=? WHERE project=? AND id='FI-03'",
                 ("2020-01-01T00:00:00Z", NAME_FP))
    assert "FI-03" not in [x["id"] for x in R.list_rules(FP, "tax")["rules"]]
    assert R.status(FP)["rules"]["expired_not_retired"] == 1


case("an expired provisional leaves the lists on its own",
     an_expired_provisional_leaves_by_itself)


def renewal_is_signed_because_it_lets_it_back_in():
    msg = digest_of("renew", NAME_FP, ["FI-03"])
    out = R.renew(FP, ["FI-03"], sign(msg))
    assert out["signed"] and out["digest"] == msg
    assert "FI-03" in [x["id"] for x in R.list_rules(FP, "tax")["rules"]]


case("renew brings it back, and it is signed", renewal_is_signed_because_it_lets_it_back_in)

refuses("renewing with the wrong signature",
        lambda: R.renew(FP, ["FI-03"], sign("whatever")), "does not match", RulesError)
refuses("renewing a rule that is not active",
        lambda: R.renew(FP, ["FI-04"], sign(digest_of("renew", NAME_FP, ["FI-04"]))),
        "not an active rule", RulesError)
refuses("renewing nothing", lambda: R.renew(FP, []), "no ID to renew", RulesError)


def promotion_makes_it_permanent():
    msg = digest_of("promote", NAME_FP, ["VA-02"])
    out = R.promote(FP, ["VA-02"], sign(msg))
    row = R._row(NAME_FP, "VA-02")
    assert out["signed"] and row["permanence"] == "permanent" and row["expires_at"] is None


case("promote: permanent, no expiry, signed", promotion_makes_it_permanent)

refuses("promoting with the wrong signature",
        lambda: R.promote(FP, ["PE-01"], sign("nope")), "does not match", RulesError)


def the_noticeboard_warns_before_the_expiry():
    R.cx.execute("UPDATE rules SET expires_at=? WHERE project=? AND id='PE-01'",
                 (_plus_days(10), NAME_FP))
    p = R.pending(FP, "architect")
    assert [x["id"] for x in p["expiring_within_30_days"]] == ["PE-01"], p["expiring_within_30_days"]
    R.renew(FP, ["PE-01"], sign(digest_of("renew", NAME_FP, ["PE-01"])))
    assert R.pending(FP, "architect")["expiring_within_30_days"] == []


case("the noticeboard lists what expires within 30 days",
     the_noticeboard_warns_before_the_expiry)

# =====================================================================
head("retiring: out of the lists, still resolvable")
# =====================================================================


def retire_with_a_successor():
    R.propose(FP, "FI-05", "M", "Estimating the bracket (rev)", "New method.",
              ["tax"], "superseded decision: FI-03 underestimated")
    b = R.batch(FP)
    R.approve(FP, b["digest"], sign(b["digest"]))
    out = R.retire(FP, "FI-03", reason="superseded by FI-05", superseded_by="FI-05")
    assert out["superseded_by"] == "FI-05"
    assert "FI-03" not in [x["id"] for x in R.list_rules(FP, "tax")["rules"]]
    assert R.get_rules(FP, "FI-03", "tax")["found"][0]["status"] == "retired", \
        "by ID it still resolves: citations must keep working"


case("retire + superseded_by: out of the active list, still there by ID",
     retire_with_a_successor)

refuses("a retired ID is not reused",
        lambda: R.propose(FP, "FI-03", "R", "x", "y", ["*"], "m"), "never reused", RulesError)
refuses("retiring twice",
        lambda: R.retire(FP, "FI-03", reason="m"), "already retired", RulesError)
refuses("retiring without a reason",
        lambda: R.retire(FP, "PE-01", reason=""), "reason is mandatory", RulesError)
refuses("superseded_by pointing at nothing",
        lambda: R.retire(FP, "PE-01", reason="m", superseded_by="FI-99"),
        "does not exist", RulesError)
refuses("retiring an ID never defined",
        lambda: R.retire(FP, "VE-95", reason="m"), "never defined", RulesError)


def check_sees_citations_to_a_retired_rule():
    v = R.check(FP)
    assert {"from": "FI-05", "cites": "FI-03"} not in v["citations_to_retired"], \
        "FI-05 does not cite FI-03"
    R.amend(FP, "FI-05", R._version(NAME_FP, "FI-05"), reason="cite the ancestor",
            body="New method. Supersedes FI-03.")
    v2 = R.check(FP)
    assert {"from": "FI-05", "cites": "FI-03"} in v2["citations_to_retired"], v2


case("check flags an active rule citing a retired one",
     check_sees_citations_to_a_retired_rule)

# =====================================================================
head("narrowing, and a rule left with no perimeter")
# =====================================================================


def narrow_to_nothing_is_reported():
    R.propose(FP, "VE-02", "R", "Orphan to be", "Only here to be narrowed to nothing.",
              ["market-news"], "test")
    b = R.batch(FP)
    R.approve(FP, b["digest"], sign(b["digest"]))
    out = R.narrow(FP, "VE-02", ["market-news"])
    assert out["scopes"] == [] and "reaches nobody" in out["warning"]
    assert "VE-02" in R.check(FP)["rules_without_perimeter"]
    R.widen(FP, "VE-02", ["market-news"])            # put it back
    assert R.check(FP)["rules_without_perimeter"] == []


case("narrowed to nothing: the tool warns and the audit lists it",
     narrow_to_nothing_is_reported)

ok(R.narrow(FP, "VE-02", ["tax"])["removed"] == [],
   "removing a scope that was not there is quiet")

# =====================================================================
head("the audit: gaps, redundancy candidates")
# =====================================================================


def numbering_gaps_are_reported():
    gaps = {g["domain"]: g["missing"] for g in R.check(FP)["numbering_gaps"]}
    assert 1 in gaps.get("VA", []), gaps          # VA-02 exists, VA-01 does not
    assert 1 in gaps.get("FI", []), gaps


case("numbering gaps are named, not fixed", numbering_gaps_are_reported)


def redundancy_is_a_suspicion_not_a_verdict():
    R.propose(FP, "ST-05", "R", "One", "Body one, see VA-02.", ["market-news"], "test")
    R.propose(FP, "ST-06", "R", "Two", "Body two, see VA-02.", ["market-news"], "test")
    b = R.batch(FP)
    R.approve(FP, b["digest"], sign(b["digest"]))
    pairs = [c["pair"] for c in R.check(FP)["redundancy_candidates"]]
    assert ["ST-05", "ST-06"] in pairs, pairs


case("two rules, same perimeter, same citations: a candidate pair",
     redundancy_is_a_suspicion_not_a_verdict)

# =====================================================================
head("the triggers hold even against a hand at the sqlite3 prompt")
# =====================================================================


def a_manual_delete_still_lands_in_history():
    R.cx.execute("DELETE FROM rules WHERE project=? AND id='ST-06'", (NAME_FP,))
    assert R.history(FP, "ST-06")["versions"][-1]["action"] == "DELETED"


case("a DELETE by hand is recorded anyway", a_manual_delete_still_lands_in_history)


def a_dropped_trigger_is_rebuilt_AND_declared():
    """A vanished trigger raises no error: it just stops writing history. So the
    repair is announced, or nobody would ever learn it happened."""
    R.close()
    cx = sqlite3.connect(DB)
    cx.execute("DROP TRIGGER trg_rules_upd")
    cx.commit()
    cx.close()
    r2 = Registry(DB, public_key=PUB)
    assert "trg_rules_upd" in r2.repaired, r2.repaired
    assert r2.status(FP)["repaired_at_open"] == ["trg_rules_upd"]
    r2.close()


case("a trigger dropped by hand is rebuilt, and the repair is DECLARED",
     a_dropped_trigger_is_rebuilt_AND_declared)

R = Registry(DB, public_key=PUB, provisional_days=90)
ok(R.repaired == [], "a clean reopen repairs nothing")

# =====================================================================
head("projects stay apart")
# =====================================================================


def same_id_two_projects_two_histories():
    R.propose(HT, "VA-02", "R", "Namesake but different", "Body of Health Tracking.",
              ["*"], "initial import")
    b = R.batch(HT)
    R.approve(HT, b["digest"], sign(b["digest"]))
    assert R._row(NAME_HT, "VA-02")["title"] == "Namesake but different"
    assert R._row(NAME_FP, "VA-02")["title"] == "Re-read the sources"
    assert R.history(HT, "VA-02")["count"] < R.history(FP, "VA-02")["count"]


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
    R.propose(HT, "AL-01", "R", "New domain", "Body.", ["nutritionist"], "test")
    b = R.batch(HT)
    R.approve(HT, b["digest"], sign(b["digest"]))
    assert [x["id"] for x in R.list_rules(HT, "nutritionist")["rules"]] == ["VA-02", "AL-01"]


case("a domain and a consumer added later work immediately",
     new_domain_and_consumer_work_at_once)

ok(R.add_domains(HT, {"AL": "food again"})["added"] == [], "adding a domain twice is quiet")
refuses("a malformed domain added later",
        lambda: R.add_domains(HT, {"food": ""}), "two uppercase", RulesError)


def all_aliases_all_mean_all():
    for i, alias in enumerate(["_all_", "*", "all", "tutti", "chiunque"], start=2):
        rid = f"MS-{i:02d}"
        R.propose(HT, rid, "F", f"Universal via {alias}", "Binds everyone.", [alias], "test")
        assert R._scopes_of(NAME_HT, rid) == [ALL], alias
    b = R.batch(HT)
    R.approve(HT, b["digest"], sign(b["digest"]))


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
head("import: once, on an empty project")
# =====================================================================

refuses("import onto a project that already holds rules",
        lambda: R.import_rules(FP, [{"id": "VA-08", "title": "x", "body": "y"}], "m"),
        "only on an empty project", RulesError)


def import_reports_what_it_refused():
    R.create_project(CASA, NAME_CASA, [("architect", "chat")], {"CA": "house"})
    out = R.import_rules(CASA, [
        {"id": "CA-01", "type": "R", "title": "First", "body": "See CA-02.",
         "scopes": ["architect"]},
        {"id": "CA-01", "type": "R", "title": "duplicate", "body": "z", "scopes": ["*"]},
        {"id": "CA-03", "type": "X", "title": "bad type", "body": "z", "scopes": ["*"]},
        {"id": "CA-04", "type": "R", "title": "no body", "body": "", "scopes": ["*"]},
    ], reason="migration from the Markdown files")
    assert out["imported"] == 1 and out["ids"] == ["CA-01"], out
    assert len(out["rejected"]) == 3, out["rejected"]
    assert out["audit"]["broken_pointers"] == [{"from": "CA-01", "cites": "CA-02"}]


case("import: what goes in, what is refused, and the audit in its wake",
     import_reports_what_it_refused)


def imported_rules_are_active_and_permanent():
    row = R._row(NAME_CASA, "CA-01")
    assert row["status"] == "active" and row["permanence"] == "permanent"
    assert row["expires_at"] is None
    assert R.list_rules(CASA, "architect")["count"] == 1


case("seeded rules are active and permanent: a migration is not a proposal",
     imported_rules_are_active_and_permanent)

EMPTY = "Ee11Ff22Gg33"
case("an empty project to try the refusals on", lambda: R.create_project(
    EMPTY, "Empty", [("architect", "chat")], {"CA": "house"}))
refuses("import over the ceiling",
        lambda: R.import_rules(EMPTY, [{"id": "CA-01"}] * (MAX_IMPORT + 1), "m"),
        "the ceiling is", RulesError)
refuses("import without a reason",
        lambda: R.import_rules(EMPTY, [{"id": "CA-01", "type": "R", "title": "x",
                                        "body": "y", "scopes": ["*"]}], ""),
        "reason is mandatory", RulesError)
refuses("import of nothing at all",
        lambda: R.import_rules(EMPTY, [], "m"), "nothing to import", RulesError)

# =====================================================================
head("grace: a lock that closes by itself")
# =====================================================================


def grace_lets_an_unsigned_batch_through():
    g = Registry(os.path.join(D, "grace.db"), grace_until="2099-12-31")
    g.create_project("Gg11Hh22Ii", "Grace", [("architect", "chat")], {"VA": "x"})
    g.propose("Gg11Hh22Ii", "VA-01", "R", "During grace", "Body.", ["*"], "test")
    b = g.batch("Gg11Hh22Ii")
    assert b["approval_required"] is False
    out = g.approve("Gg11Hh22Ii", b["digest"])
    assert out["signed"] is False, "it went through, and it is recorded as UNSIGNED"
    row = g.cx.execute("SELECT signed FROM approvals").fetchone()[0]
    assert row == 0
    g.close()


case("inside the grace window an unsigned batch passes, and says so",
     grace_lets_an_unsigned_batch_through)


def a_closed_grace_window_with_no_key_approves_nothing():
    g = Registry(os.path.join(D, "closed.db"), grace_until="2000-01-01")
    g.create_project("Jj11Kk22Ll", "Closed", [("architect", "chat")], {"VA": "x"})
    g.propose("Jj11Kk22Ll", "VA-01", "R", "After grace", "Body.", ["*"], "test")
    b = g.batch("Jj11Kk22Ll")
    assert b["approval_required"] is True
    try:
        g.approve("Jj11Kk22Ll", b["digest"])
        raise AssertionError("it should have refused")
    except RulesError as e:
        assert "APPROVAL_PUBKEY" in str(e), e
    g.close()


case("grace closed and no key: nothing can be approved, and the message says which knob",
     a_closed_grace_window_with_no_key_approves_nothing)

# =====================================================================
head("derivatives: export and backup")
# =====================================================================


def export_full_and_per_consumer():
    e = R.export(FP)
    assert "VA-02" in e["markdown"] and "_retired_" in e["markdown"]
    assert "Health Tracking" not in e["markdown"], "only its own project"
    ex = R.export(FP, "tax")
    assert ex["consumer"] == "tax"
    assert "FI-05" in ex["markdown"] and "VA-02" in ex["markdown"]
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
    assert cx.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 4
    assert cx.execute("SELECT COUNT(DISTINCT project) FROM rules").fetchone()[0] == 3
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
    assert s["rules"]["denied"] == 2, "VE-01 and FI-04: a refusal is KEPT"
    assert s["rules"]["proposed"] == 0
    assert s["rules"]["retired"] == 1
    assert s["rules"]["permanent"] == 1
    assert s["approval"]["public_key_configured"] is True
    assert s["approval"]["required"] is True
    assert s["registry_version"] == VERSION
    for consumer, n in s["by_consumer"].items():
        assert n == R.list_rules(FP, consumer)["count"], consumer
    assert sum(s["by_domain"].values()) == s["rules"]["in_force"], \
        (s["by_domain"], s["rules"]["in_force"])


case("status: two paths to the same number, and they agree",
     status_counts_agree_with_the_lists)


def the_registry_lists_projects_only_here():
    e = R.projects()
    assert e["count"] == 4
    assert {p["code"] for p in e["projects"]} == {FP, HT, CASA, EMPTY}
    assert {p["name"]: p["active_rules"] for p in e["projects"]}["Empty"] == 0


case("projects(): the only door codes come out of — gated in the server",
     the_registry_lists_projects_only_here)

# =====================================================================
head("reopening")
# =====================================================================


def reopen_finds_everything_where_it_was():
    versions = R.history(FP, "VA-02")["count"]
    listed = [x["id"] for x in R.list_rules(FP, "tax")["rules"]]
    R.close()
    r3 = Registry(DB, public_key=PUB)
    s = r3.status(FP)
    assert s["database"]["integrity"] == "ok" and s["database"]["journal_mode"] == "wal"
    assert r3.projects()["count"] == 4
    assert r3.history(FP, "VA-02")["count"] == versions
    assert [x["id"] for x in r3.list_rules(FP, "tax")["rules"]] == listed
    r3.close()


case("reopen: WAL, whole, three projects, history and lists intact",
     reopen_finds_everything_where_it_was)

# =====================================================================
head("the engine is used from a THREAD POOL, not from here")
# =====================================================================

# The hole this suite had. Everything above runs on one thread; the server does
# not. FastMCP hands sync tools to anyio.to_thread.run_sync, so the connection
# is opened on the import thread and used from a worker — and sqlite3 refuses
# that outright. The first call that touched the database in production died
# with "SQLite objects created in a thread can only be used in that same
# thread", and no test here could have seen it. Now one can.

R = Registry(DB, public_key=PUB, provisional_days=90)    # the reopen test closed the last one


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
            rid = f"ST-{40 + n}"
            R.propose(FP, rid, "F", f"Concurrent {n}", f"Body {n}.",
                      ["market-news"], "thread safety")
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
    # And the writes are all there, none lost and none half-written.
    waiting = {r["id"] for r in R.pending(FP)["waiting"]}
    assert {f"ST-{40 + n}" for n in range(8)} <= waiting, sorted(waiting)
    assert R.status(FP)["database"]["integrity"] == "ok"


case("eight threads proposing and reading at once: nothing lost, nothing torn",
     many_threads_reading_and_writing)


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

print(f"\n{OK} passed, {FAIL} failed")
if FAILURES:
    print("failed: " + "; ".join(FAILURES))
sys.exit(1 if FAIL else 0)
