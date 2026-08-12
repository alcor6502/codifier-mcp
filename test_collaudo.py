#!/usr/bin/env python3
"""The engine suite: what the ENGINE refuses, and what it must let through.

The division of labour with `test_schema.py` is deliberate and worth keeping:
that file writes raw SQL and proves what the DATABASE refuses, because the file
is readable from the share. This one goes through the methods and proves what
only the engine can know — a perimeter expanded and compared, a queue counted,
a name resolved, a ladder answered.

If a case in here could be made to fail with sqlite3 alone, it belongs in
test_schema.py. If a case in there needs a method to fail, it belongs here.

Every refusal has to NAME the culprit, so each case declares the words it
expects. And every section carries `allowed` cases as well as `refused` ones:
a suite made only of refusals gets greener with every new guard, and the day a
guard blocks something legitimate nothing goes red — which is exactly how
`all -> targeted` stayed unreachable through forty green cases.

Runs on the standard library alone: no network, no FastMCP, no web stack.
"""

import atexit
import os
import shutil
import sys
import tempfile

import rules

_passed = 0
_failed: list[str] = []
_ROOTS: list[str] = []
_finished = False


@atexit.register
def _summary():
    """Printed WHATEVER happens, including a crash halfway.

    A suite that dies on an unguarded setup call prints its green sections and
    then a traceback, and the eye reads the green. This says out loud that the
    run stopped early, and it still exits non-zero."""
    for r in _ROOTS:
        shutil.rmtree(r, ignore_errors=True)
    if not _finished:
        _failed.append("THE RUN WAS CUT SHORT: a call outside a case raised, so "
                       "everything after it measured nothing")
        print("\n  FAIL  the run was CUT SHORT — a setup call raised")
    print(f"\n{_passed} cases, {len(_failed)} failed")
    for f in _failed:
        print("  -", f)
    # FLUSHED, and then the hard exit. os._exit skips the interpreter's own
    # flush, so without this line the summary is written into a buffer nobody
    # empties and the run ends with its last `ok` on screen — a suite whose
    # verdict can disappear is a suite that reports success by omission.
    sys.stdout.flush()
    os._exit(1 if _failed else 0)


def project(**profile):
    """A fresh project with a little anagrafica already in it.

    Two groups sharing a member on purpose — `advisory` is in `deliberativi`
    and in `automatismi` — because group-with-group overlap is ALLOWED and
    everything downstream has to survive it.
    """
    root = tempfile.mkdtemp(prefix="codifier-collaudo-")
    _ROOTS.append(root)
    p = rules.Project("Financial Portfolio", root,
                      reference_code="r" * 16, admin_code="k" * 16)
    p.amend_project("domain", "VA", "create",
                    {"reason": "the house doctrine", "description": "values"},
                    actor="architect")
    p.amend_project("domain", "ST", "create", {"reason": "strategy"}, actor="architect")
    for name, kind in (("architect", "chat"), ("advisory", "chat"),
                       ("news", "skill"), ("Alfredo", "human")):
        p.amend_project("consumer", name, "create", {"kind": kind}, actor="architect")
    p.amend_project("group", "deliberativi", "create",
                    {"members": ["architect", "advisory"]}, actor="architect")
    p.amend_project("group", "automatismi", "create",
                    {"members": ["advisory", "news"]}, actor="architect")
    if profile:
        p.amend_project("project", "", "amend", profile, actor="architect")
    return p


def rule(p, reach="all", groups=(), exceptions=(), domain="VA", title="a title",
         body="a body", approve=True, **kw):
    """Propose and, unless told otherwise, take it through the page."""
    out = p.propose(domain, kw.pop("rtype", "R"), title, body,
                    kw.pop("reason", "because it was decided"), reach, "architect",
                    groups=list(groups), exceptions=list(exceptions), **kw)
    if approve:
        b = p.batch()
        p.decide(b["digest"], [x["id"] for x in b["pending"]], {})
    return out["id"]


def refused(name, gesture, expect):
    global _passed
    try:
        gesture()
        _failed.append(f"{name}: NOT REFUSED — the engine let it through")
        print(f"  FAIL  {name}: not refused")
    except rules.RulesError as exc:
        msg = str(exc)
        if expect.lower() in msg.lower():
            _passed += 1
            print(f"  ok    {name}")
        else:
            _failed.append(f"{name}: refused, but never says {expect!r} — {msg}")
            print(f"  FAIL  {name}: refusal does not name it — {msg[:70]}")
    except Exception as exc:                       # noqa: BLE001
        # A traceback is not a refusal. A suite that accepted one would be
        # counting a crash as a guarantee — and a crash stops the run, so the
        # cases after it stop measuring anything at all.
        _failed.append(f"{name}: TRACEBACK instead of a named refusal — "
                       f"{type(exc).__name__}: {exc}")
        print(f"  FAIL  {name}: traceback — {type(exc).__name__}: {str(exc)[:60]}")


def allowed(name, gesture):
    """The gesture must go THROUGH, and its value comes back to the caller."""
    global _passed
    try:
        out = gesture()
        _passed += 1
        print(f"  ok    {name}")
        return out
    except Exception as exc:                       # noqa: BLE001
        _failed.append(f"{name}: REFUSED, and it should not have been — {exc}")
        print(f"  FAIL  {name}: refused — {str(exc)[:70]}")
        return None


def equals(name, got, want):
    global _passed
    if got == want:
        _passed += 1
        print(f"  ok    {name}")
    else:
        _failed.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


def yields(name, gesture, want):
    """A value that arrives from a call which MIGHT refuse. Without this the
    refusal escapes as a traceback, the run dies at case nine and every case
    after it measures nothing."""
    try:
        equals(name, gesture(), want)
    except Exception as exc:                       # noqa: BLE001
        _failed.append(f"{name}: raised instead of answering — {exc}")
        print(f"  FAIL  {name}: raised — {str(exc)[:70]}")


# =====================================================================
print("\n— THE LADDER, and it is answered in ONE place —")
# port_for is the only place the scale is written. The surface asks it; if it
# were repeated at each door, one door would be out of step and nobody would
# know which.
equals("creating takes the admin code",
       rules.Project.port_for("consumer", "create", {"kind": "chat"}), "admin")
equals("amending anything that exists takes the one-time code too",
       rules.Project.port_for("consumer", "amend", {"brief": "x"}), "auth")
equals("a rename is a modification like any other",
       rules.Project.port_for("consumer", "amend", {"name": "x"}), "auth")
equals("group membership is a modification",
       rules.Project.port_for("group", "amend", {"members": ["a"]}), "auth")
equals("queue_cap is a modification",
       rules.Project.port_for("project", "amend", {"queue_cap": 5}), "auth")
equals("retiring is a modification", rules.Project.port_for("domain", "x", {}), "auth")
equals("specs alone is the one exception downward",
       rules.Project.port_for("consumer", "amend", {"specs": "x"}), "project")
equals("and it holds for the project's own specs",
       rules.Project.port_for("project", "amend", {"specs": "x"}), "project")
equals("MIXED answers with the HIGHEST port it contains",
       rules.Project.port_for("consumer", "amend", {"specs": "x", "brief": "y"}), "auth")
equals("a brief is never the low door",
       rules.Project.port_for("project", "amend", {"brief": "x"}), "auth")
equals("and a domain has no specs to travel low",
       rules.Project.port_for("domain", "amend", {"description": "x"}), "auth")

# =====================================================================
print("\n— THE ANAGRAFICA —")
p = project()
refused("a consumer with no kind", lambda: p.amend_project(
    "consumer", "tax", "create", {}), "kind")
refused("a kind that is not one of the three", lambda: p.amend_project(
    "consumer", "tax", "create", {"kind": "robot"}), "one of chat, skill, human")
refused("a consumer that already exists", lambda: p.amend_project(
    "consumer", "ARCHITECT", "create", {"kind": "chat"}), "already has a consumer")
refused("a domain code that is not two letters", lambda: p.amend_project(
    "domain", "VALUE", "create", {"reason": "x"}), "two uppercase letters")
refused("TK claimed as a domain of rules", lambda: p.amend_project(
    "domain", "TK", "create", {"reason": "x"}), "RESERVED")
refused("a domain with no reason", lambda: p.amend_project(
    "domain", "QQ", "create", {}), "reason to exist")
refused("a field that belongs to another entity", lambda: p.amend_project(
    "consumer", "advisory", "amend", {"description": "x"}), "not a field of consumer")
refused("the domain code, amended", lambda: p.amend_project(
    "domain", "VA", "amend", {"code": "VB"}), "not a field of domain")
refused("an amendment with nothing in it", lambda: p.amend_project(
    "consumer", "advisory", "amend", {}), "nothing to amend")
refused("a retirement with a change smuggled in", lambda: p.amend_project(
    "consumer", "news", "retire", {"brief": "x"}, reason="done"), "takes no fields")
refused("retiring without a reason", lambda: p.amend_project(
    "consumer", "news", "retire", {}), "costs a reason")
refused("a group with no members", lambda: p.amend_project(
    "group", "vuoti", "create", {"members": []}), "at least one consumer")
refused("a group emptied instead of retired", lambda: p.amend_project(
    "group", "automatismi", "amend", {"members": []}), "retire it")
refused("reviving something that is not retired", lambda: p.amend_project(
    "consumer", "advisory", "revive", {}), "nothing to revive")
refused("the project created from a tool", lambda: p.amend_project(
    "project", "", "create", {}), "catastrophic has no tool")
refused("a negative queue cap", lambda: p.amend_project(
    "project", "", "amend", {"queue_cap": -1}), "none of the three")

out = allowed("a rename goes through", lambda: p.amend_project(
    "consumer", "advisory", "amend", {"name": "fidelity advisory"}, actor="architect"))
equals("and the verdict says the old name STOPS RESOLVING",
       "STOPS RESOLVING" in (out or {}).get("note", ""), True)
equals("and it names what lives outside the registry",
       all(w in (out or {}).get("note", "")
           for w in ("skill", "instructions", "scheduled")), True)
refused("the old name does not resolve any more",
        lambda: p.list_rules("advisory"), "not a consumer of this project")
allowed("the new one does", lambda: p.list_rules("fidelity advisory"))

# =====================================================================
print("\n— THE PERIMETER: declared, never deduced —")
p = project()
refused("a reach that is neither", lambda: p.propose(
    "VA", "R", "t", "b", "why", "some", "architect"), "declared and never deduced")
refused("targeted with nothing to aim at", lambda: p.propose(
    "VA", "R", "t", "b", "why", "targeted", "architect"), "reaches NOBODY")
refused("universal WITH a perimeter", lambda: p.propose(
    "VA", "R", "t", "b", "why", "all", "architect", groups=["deliberativi"]),
    "takes no group")
refused("a group nobody declared", lambda: p.propose(
    "VA", "R", "t", "b", "why", "targeted", "architect", groups=["fantasmi"]),
    "not a group of this project")
refused("a consumer named where a group goes", lambda: p.propose(
    "VA", "R", "t", "b", "why", "targeted", "architect", groups=["news"]),
    "A single consumer is not a group")
refused("the same group twice", lambda: p.propose(
    "VA", "R", "t", "b", "why", "targeted", "architect",
    groups=["deliberativi", "DELIBERATIVI"]), "named twice")
refused("an exception already inside this rule's groups", lambda: p.propose(
    "VA", "R", "t", "b", "why", "targeted", "architect",
    groups=["deliberativi"], exceptions=["advisory"]), "already inside")
allowed("an exception that belongs to OTHER groups is its own business",
        lambda: p.propose("VA", "R", "t1", "b", "why", "targeted", "architect",
                          groups=["deliberativi"], exceptions=["news"]))
allowed("group with group overlap is allowed",
        lambda: p.propose("VA", "R", "t2", "b", "why", "targeted", "architect",
                          groups=["deliberativi", "automatismi"]))

# =====================================================================
print("\n— PROPOSING —")
p = project(queue_cap=3)
refused("a type that is not R, M or F", lambda: p.propose(
    "VA", "X", "t", "b", "why", "all", "architect"), "R binding, M method")
refused("no title", lambda: p.propose("VA", "R", "", "b", "why", "all", "architect"),
        "needs a title")
refused("no body", lambda: p.propose("VA", "R", "t", "", "why", "all", "architect"),
        "needs a body")
refused("no reason", lambda: p.propose("VA", "R", "t", "b", "", "all", "architect"),
        "reason is mandatory")
refused("unsigned", lambda: p.propose("VA", "R", "t", "b", "why", "all", ""),
        "proposed_by is required")
refused("a domain nobody declared", lambda: p.propose(
    "QQ", "R", "t", "b", "why", "all", "architect"), "not declared by this project")
first = allowed("a proposal goes in", lambda: p.propose(
    "VA", "R", "cite sources", "Always name the source.", "because it was decided",
    "all", "architect"))
equals("it is born proposed", (first or {}).get("status"), "proposed")
equals("and the verdict says who it would reach", (first or {}).get("reaches"), 4)
p.propose("VA", "M", "b", "body", "why", "all", "architect")
p.propose("VA", "F", "c", "body", "why", "all", "architect")
refused("the queue at its ceiling", lambda: p.propose(
    "VA", "R", "d", "body", "why", "all", "architect"), "ceiling is 3")
refused("and the refusal lists what is waiting", lambda: p.propose(
    "VA", "R", "d", "body", "why", "all", "architect"), "cite sources")

p = project(queue_cap=0)
refused("a queue declared closed", lambda: p.propose(
    "VA", "R", "t", "b", "why", "all", "architect"), "CLOSED")

# =====================================================================
print("\n— THE SANITISATION, at every door —")
p = project()
rid = rule(p, title="the first one")
refused("a short ID in a body", lambda: p.propose(
    "VA", "R", "t", "see (VA-01)", "why", "all", "architect"), "sanitisation failed")
refused("a short ID in the REASON, which can never be repaired", lambda: p.propose(
    "VA", "R", "t", "b", "after (VE-05)", "all", "architect"), "sanitisation failed")
refused("a short ID in the title", lambda: p.propose(
    "VA", "R", "about (VA-1)", "b", "why", "all", "architect"), "sanitisation failed")
refused("a bare ID outside its brackets", lambda: p.propose(
    "VA", "R", "t", "see VA-0001 for this", "why", "all", "architect"), "bare ID")
refused("brackets holding a sentence instead of a pointer", lambda: p.propose(
    "VA", "R", "t", "(see VA-0001)", "why", "all", "architect"), "bare ID")
refused("a citation that does not resolve", lambda: p.propose(
    "VA", "R", "t", "see (VA-0099)", "why", "all", "architect"), "does not resolve")
refused("a citation towards something still in the queue", lambda: (
    p.propose("VA", "R", "in the queue", "b", "why", "all", "architect"),
    p.propose("VA", "R", "t", "see (VA-0002)", "why", "all", "architect")),
    "not in force yet")
refused("a gloss of your own inside the brackets", lambda: p.propose(
    "VA", "R", "t", "see (VA-0001 — my own note)", "why", "all", "architect"),
    "not that rule's title")
allowed("the gloss the registry itself writes, pasted straight back",
        lambda: p.propose("VA", "R", "t", "see (VA-0001 — the first one)", "why",
                          "all", "architect"))
yields("and it is STORED compact, so the title cannot go stale",
       lambda: p.cx.execute("SELECT body FROM v_rule WHERE title='t' "
                            "ORDER BY rule_id DESC").fetchone()[0],
       "see (VA-0001)")
yields("while READING puts the current title back in",
       lambda: p._expand("see (VA-0001)"), "see (VA-0001 — the first one)")
refused("a rule that cites a TASK", lambda: p.propose(
    "VA", "R", "t", "see (TK-0001)", "why", "all", "architect"),
    "cites a rule, never a task")

# =====================================================================
print("\n— THE SUPERSEDE —")
p = project()
victim = rule(p, title="the old way")
heir = allowed("a proposal can claim a rule in force", lambda: p.propose(
    "VA", "R", "the new way", "b", "it changed", "all", "architect",
    supersedes=victim))
refused("a second heir for the same rule", lambda: p.propose(
    "VA", "R", "a third way", "b", "why", "all", "architect", supersedes=victim),
    "already claimed")
refused("superseding something that was never defined", lambda: p.propose(
    "VA", "R", "t", "b", "why", "all", "architect", supersedes="VA-0099"),
    "never defined")
b = p.batch()
allowed("approving the heir retires the victim in the same decision",
        lambda: p.decide(b["digest"], [heir["id"]], {}))
yields("and the victim points forward",
       lambda: p.get_rules([victim])["rules"][0]["superseded_by"], heir["id"])
yields("and the citation of the victim reads as retired, in the text",
       lambda: "retired → superseded by" in p._expand(f"({victim})"), True)

# =====================================================================
print("\n— THE PAGE: one turn, two verdicts —")
p = project()
a = p.propose("VA", "R", "one", "b", "why", "all", "architect")["id"]
b_ = p.propose("VA", "R", "two", "b", "why", "targeted", "architect",
               groups=["deliberativi"])["id"]
lot = p.batch()
equals("the lot is the WHOLE queue", lot["count"], 2)
equals("row one: the perimeter as DECLARED",
       lot["pending"][1]["declared"], {"reach": "targeted",
                                       "groups": ["deliberativi"], "exceptions": []})
equals("row two: the consumers it EFFECTIVELY reaches, expanded and counted",
       (lot["pending"][1]["reaches"], lot["pending"][1]["reaches_count"]),
       (["advisory", "architect"], 2))
equals("a universal proposal reaches everyone alive",
       lot["pending"][0]["reaches_count"], 4)
refused("a digest that went stale",
        lambda: p.decide("deadbeef", [a], {b_: "a complete lot, so only the digest "
                                               "can refuse this"}),
        "changed between the reading and this post")
refused("an unticked proposal with no reason", lambda: p.decide(lot["digest"], [a], {}),
        "a denial costs a sentence")
refused("a tick for something outside the lot",
        lambda: p.decide(lot["digest"], ["VA-0099"], {}), "not in this lot")
done = allowed("ticked in, unticked out, in one turn",
               lambda: p.decide(lot["digest"], [a], {b_: "not now: it needs the tax desk"}))
equals("the yes is recorded", [x["id"] for x in (done or {})["approved"]], [a])
equals("and so is the NO, with its reason",
       (done or {})["denied"], [{"id": b_, "reason": "not now: it needs the tax desk"}])
yields("the denial is in the decision log, not lost",
       lambda: p.cx.execute("SELECT verdict, reason FROM decision_rule "
                            "ORDER BY rule_id").fetchall()[1][1],
       "not now: it needs the tax desk")
yields("an approved rule is provisional and dated",
       lambda: p.get_rules([a])["rules"][0]["permanence"], "provisional")
refused("deciding an empty queue", lambda: p.decide("x", [], {}), "nothing to decide")

p = project(queue_cap=2)
p.propose("VA", "R", "one", "b", "why", "all", "architect")
p.propose("VA", "R", "two", "b", "why", "all", "architect")
lot = p.batch()
allowed("the ceiling lets a full lot through", lambda: p.decide(
    lot["digest"], [x["id"] for x in lot["pending"]], {}))

# =====================================================================
print("\n— NARROWING: downwards only, and never to nobody —")
p = project()
universal = rule(p, title="binds everyone")
targeted = rule(p, "targeted", groups=["deliberativi"], title="binds the deliberative")
v = p.get_rules([universal], history=True)["rules"][0]["history"][-1]["version"]
refused("a narrowing with no reason", lambda: p.amend_rule(
    universal, "targeted", ["deliberativi"], [], v, "", "architect"), "reason is required")
refused("writing against a version that moved", lambda: p.amend_rule(
    universal, "targeted", ["deliberativi"], [], 99, "why", "architect"),
    "somebody changed it after you read it")
narrowed = allowed("a UNIVERSAL rule narrowed onto a group — the gesture the DDL blocked",
                   lambda: p.amend_rule(universal, "targeted", ["deliberativi"], [],
                                        v, "only the deliberative desks now", "architect"))
equals("and it says who it stopped reaching",
       (narrowed or {})["no_longer_reaches"], ["Alfredo", "news"])
yields("the perimeter is what the rule now shows",
       lambda: p.get_rules([universal])["rules"][0]["groups"], ["deliberativi"])
def ver(rid):
    return p.get_rules([rid], history=True)["rules"][0]["history"][-1]["version"]


refused("widening by one consumer", lambda: p.amend_rule(
    targeted, "targeted", ["deliberativi"], ["news"], ver(targeted), "one more",
    "architect"), "it would newly bind news")
refused("widening all the way back to everyone", lambda: p.amend_rule(
    targeted, "all", [], [], ver(targeted), "everyone now", "architect"),
    "not a narrowing")
refused("and the refusal carries the cure", lambda: p.amend_rule(
    targeted, "all", [], [], ver(targeted), "everyone now", "architect"), "supersede")
p.amend_project("group", "deliberativi", "amend", {"members": ["architect"]},
                actor="architect")
# A group whose members have all ENDED: the new perimeter is a subset of the
# old one — it has to be, it is empty — so containment says yes and only the
# empty guard can say no.
p.amend_project("group", "soli", "create", {"members": ["news"]}, actor="architect")
p.amend_project("consumer", "news", "retire", {}, reason="the skill was withdrawn",
                actor="architect")
refused("a narrowing that leaves NOBODY is a retirement in disguise",
        lambda: p.amend_rule(targeted, "targeted", ["soli"], [], ver(targeted), "why", "architect"),
        "retirement in disguise")
refused("and it points at the door that gesture really goes through",
        lambda: p.amend_rule(targeted, "targeted", ["soli"], [], ver(targeted), "why", "architect"),
        "rules_retire")
refused("the content is not touched from here", lambda: p.amend_project(
    "rule", "x", "amend", {}), "entity 'rule'")
refused("a rule that is not in force has no perimeter to narrow", lambda: (
    p.propose("VA", "R", "still queued", "b", "why", "all", "architect"),
    p.amend_rule("VA-0003", "targeted", ["automatismi"], [], 1, "why", "architect")),
    "not in force")

# =====================================================================
print("\n— THE EMPTY GUARD: it NAMES the rules —")
p = project()
only_news = rule(p, "targeted", exceptions=["news"], title="the news rule")
refused("retiring the last consumer a rule in force reaches",
        lambda: p.amend_project("consumer", "news", "retire", {}, reason="finished",
                                actor="architect"), "the news rule")
refused("and it says what to do about it",
        lambda: p.amend_project("consumer", "news", "retire", {}, reason="finished",
                                actor="architect"), "binding nobody")
p2 = project()
grp = rule(p2, "targeted", groups=["automatismi"], title="the automatic rule")
refused("pulling out of a group the only people a rule in force reaches",
        lambda: p2.amend_project("group", "automatismi", "amend",
                                 {"members": ["architect"]}, actor="architect"),
        "the automatic rule")
refused("retiring that group outright, same guard",
        lambda: p2.amend_project("group", "automatismi", "retire", {},
                                 reason="done", actor="architect"),
        "the automatic rule")
allowed("taking ONE member out, when the rule still reaches somebody",
        lambda: p2.amend_project("group", "automatismi", "amend",
                                 {"members": ["advisory"]}, actor="architect"))
rule(p2, "targeted", groups=["deliberativi"], exceptions=["news"], title="mixed")
allowed("ADDING a member passes even when it covers an exception",
        lambda: p2.amend_project("group", "deliberativi", "amend",
                                 {"members": ["architect", "advisory", "news"]},
                                 actor="architect"))
yields("and the overlap it created is REPORTED, not hidden",
       lambda: bool(p2.status()["overlaps"]), True)

p3 = project()
rule(p3, "targeted", exceptions=["news", "advisory"], title="two by name")
refused("a group CREATED to mirror a rule's exceptions",
        lambda: p3.amend_project("group", "ricalco", "create",
                                 {"members": ["news", "advisory"]}, actor="architect"),
        "two by name")

# =====================================================================
print("\n— RETIRING A RULE —")
p = project()
rid = rule(p, title="on the way out")
refused("retiring without a reason", lambda: p.retire(rid, ""), "price of a retirement")
refused("retiring something that was never defined", lambda: p.retire("VA-0099", "why"),
        "never defined")
allowed("a rule in force ends", lambda: p.retire(rid, "the reason it stopped applying",
                                                 "architect"))
refused("and it does not end twice", lambda: p.retire(rid, "again"), "already retired")
p = project()
rule(p, title="in force")
refused("retiring a domain that still has rules in force",
        lambda: p.amend_project("domain", "VA", "retire", {}, reason="done",
                                actor="architect"), "in force")
refused("and the refusal names them",
        lambda: p.amend_project("domain", "VA", "retire", {}, reason="done",
                                actor="architect"), "in force")
allowed("a domain with nothing under it retires",
        lambda: p.amend_project("domain", "ST", "retire", {}, reason="never used",
                                actor="architect"))
refused("and nothing new is filed under it", lambda: p.propose(
    "ST", "R", "t", "b", "why", "all", "architect"), "was retired on")

# =====================================================================
print("\n— READING: the session start —")
p = project(brief="the owner's book", specs="cash at 12%")
u = rule(p, title="everyone")
g = rule(p, "targeted", groups=["deliberativi"], title="the deliberative")
d = rule(p, "targeted", exceptions=["advisory"], title="by name")
start = allowed("one call", lambda: p.list_rules("advisory"))
equals("the PROJECT comes first", (start or {})["profile"]["brief"], "the owner's book")
equals("then the consumer's own", (start or {})["consumer"]["name"], "advisory")
equals("then the rules that reach it", [r["id"] for r in (start or {})["rules"]],
       [u, g, d])
equals("universal first, then the group, then the name",
       [r["reaches_you"] for r in (start or {})["rules"]],
       ["everyone", "deliberativi", "by name"])
equals("and the desk summary is TWO counters and nothing else",
       set((start or {})["desk"]), {"open", "urgent"})
yields("a rule that reaches nobody here is not listed",
       lambda: [r["id"] for r in p.list_rules("news")["rules"]], [u])
yields("query filters, and hands back the fragment",
       lambda: p.list_rules("advisory", query="deliberative")["rules"][0]["id"], g)
yields("the queue is the same call",
       lambda: p.list_rules("advisory", pending=True)["count"], 0)
refused("a consumer nobody declared", lambda: p.list_rules("nobody"),
        "not a consumer of this project")
refused("and the refusal lists the ones that exist", lambda: p.list_rules("nobody"),
        "architect")

# =====================================================================
print("\n— READING: the detail, and the story —")
refused("more IDs than the ceiling is REFUSED, not trimmed",
        lambda: p.get_rules([u] * (rules.GET_IDS + 1)), "REFUSED and not trimmed")
yields("a short ID resolves on READ", lambda: p.get_rules(["VA-01"])["rules"][0]["id"], u)
yields("an ID that is not there is named, not invented",
       lambda: p.get_rules(["VA-0099"])["not_found"], ["VA-0099"])
allowed("the perimeter moves, and only the perimeter",
        lambda: p.amend_rule(g, "targeted", [], ["advisory"], 2,
                             "the architect desk stops carrying this", "architect"))
hist = allowed("the history comes as dated gestures",
               lambda: p.get_rules([g], history=True)["rules"][0]["history"])
equals("version 1 photographs the perimeter it was born with",
       (hist or [{}])[0].get("reaches"), ["advisory", "architect"])
equals("the verbs the database can derive, it derives",
       [h["action"] for h in (hist or [])], ["created", "approved", "amended"])
equals("a gesture that moved only the perimeter carries no scalar field at all",
       (hist or [{}, {}, {}])[2]["changed"], {})
equals("the audience diff is in names, not in keys",
       ((hist or [{}, {}, {}])[2].get("joined"), (hist or [{}, {}, {}])[2].get("left")),
       ([], ["architect"]))

# =====================================================================
print("\n— THE TASK LOG —")
p = project()
rid = rule(p, title="the rule a task points at")
refused("a task with no body", lambda: p.task_add("advisory", "t", "", "architect"),
        "needs a body")
refused("a task nobody signed", lambda: p.task_add("advisory", "t", "b", ""),
        "created_by is required")
refused("a task for a desk that does not exist",
        lambda: p.task_add("nobody", "t", "b", "architect"), "not a consumer")
t1 = allowed("opening a task for ANOTHER desk is free",
             lambda: p.task_add("advisory", "check the drift",
                                "Against (VA-0001).", "architect"))
h = allowed("and one for a human, which does not notify them",
            lambda: p.task_add("Alfredo", "sign the form", "b", "architect"))
equals("and it says so", "does NOT notify" in (h or {}).get("note", ""), True)
same = allowed("the same idem_key on the same desk absorbs the repeat",
               lambda: (p.task_add("advisory", "x", "b", "architect", idem_key="k1"),
                        p.task_add("advisory", "x", "b", "architect", idem_key="k1"))[1])
equals("it is the task that was already there", (same or {}).get("already_open"), True)
yields("a citation in a task body expands on read",
       lambda: p.task_get([t1["id"]])["tasks"][0]["body"],
       "Against (VA-0001 — the rule a task points at).")
refused("closing somebody else's task",
        lambda: p.task_close(t1["id"], "architect", outcome="done"),
        "takes the admin code")
allowed("with the admin code it goes through",
        lambda: p.task_close(t1["id"], "architect", outcome="done", admin=True))
refused("and closed is closed",
        lambda: p.task_amend(t1["id"], "advisory", title="t"), "closed is closed")
t2 = p.task_add("advisory", "another", "b", "architect")["id"]
refused("closing with both an outcome and a reason",
        lambda: p.task_close(t2, "advisory", outcome="done", reason="not done"),
        "exactly one of the two")
refused("closing with neither", lambda: p.task_close(t2, "advisory"),
        "exactly one of the two")
allowed("dropping costs a reason, and that is the whole gesture",
        lambda: p.task_close(t2, "advisory", reason="the desk that owns it changed"))
t3 = p.task_add("advisory", "to reassign", "b", "architect")["id"]
moved = allowed("a reassignment is named",
                lambda: p.task_amend(t3, "advisory", consumer="news"))
equals("and it keeps both owners in sight",
       ((moved or {}).get("reassigned_from"), (moved or {}).get("owner")),
       ("advisory", "news"))
refused("a rule read as a task is NAMED, not reported missing",
        lambda: p.task_get([rid]), "is a RULE, not a task")
yields("what I opened on other desks, with its outcome",
       lambda: sorted(t["id"] for t in
                      p.task_list("architect", authored=True)["closed_recent"]),
       sorted([t1["id"], t2]))
yields("and the outcome is on it, which is why the sender stops re-sending",
       lambda: [t["outcome"] for t in
                p.task_list("architect", authored=True)["closed_recent"]
                if t["id"] == t1["id"]], ["done"])
yields("the overview sees every desk, humans included",
       lambda: sorted(d["consumer"] for d in p.task_overview()["desks"]),
       ["Alfredo", "advisory", "architect", "news"])
yields("and it declares its ceilings",
       lambda: set(p.task_overview()["caps"]),
       {"list", "get_ids", "get_bytes", "stale_days"})
# Every number handed out so far, read BEFORE the prune and kept in Python: the
# expectation must not come from the same arithmetic the engine is about to do,
# or the case proves nothing. In 3.1.0 the prune DELETED, MAX(seq) walked
# backwards, and TK-0004 came back after TK-0007.
_handed_out = {r[0] for r in p.cx.execute("SELECT display_id FROM v_task")}
allowed("the prune archives what is closed", lambda: p.prune_tasks("2099-01-01"))
yields("and leaves the open ones alone, saying how many",
       lambda: p.prune_tasks("2099-01-01")["left_open"] > 0, True)
_after = allowed("a task opened after the prune",
                 lambda: p.task_add("advisory", "after the prune", "b", "architect"))
equals("takes a number NEVER handed out before",
       (_after or {}).get("id") not in _handed_out, True)
equals("and it is past the highest one ever handed out",
       int((_after or {"id": "TK-0000"})["id"][3:])
       > max(int(x[3:]) for x in _handed_out), True)

# =====================================================================
print("\n— THE SECRET, when a consumer has one —")
p = project()
p.amend_project("consumer", "news", "amend", {"secret": "s3cret"}, actor="architect")
refused("a gesture in that consumer's name with no key",
        lambda: p.task_add("advisory", "t", "b", "news"), "signs its gestures")
allowed("with the key it goes through",
        lambda: p.task_add("advisory", "t", "b", "news", consumer_key="s3cret"))
allowed("and the admin code goes over the top of it",
        lambda: p.task_add("advisory", "t", "b", "news", admin=True))
allowed("a consumer with no secret is taken at its name",
        lambda: p.task_add("advisory", "t", "b", "architect"))

# =====================================================================
print("\n— THE REPORT —")
p = project()
a = rule(p, title="the first")
b_ = rule(p, "targeted", groups=["deliberativi"], title="the second")
p.retire(a, "it stopped applying", "architect")
p.propose("VA", "R", "points at a dead one", "see (VA-0001)", "why", "all", "architect")
rep = allowed("one call", lambda: p.status())
equals("it counts, and says it counted", (rep or {})["counted"]["in_force"], 1)
equals("a citation towards a retired rule is reported",
       [(d["in"], d["cites"], d["state"]) for d in (rep or {})["dangling_citations"]],
       [("VA-0003", "VA-0001", "retired")])
equals("a domain nothing was ever filed under is reported",
       (rep or {})["domains_with_no_rules"], ["ST"])
equals("and a consumer no rule reaches", (rep or {})["consumers_no_rule_reaches"],
       ["news"])
p.cx.execute("INSERT INTO rule_audience_group (rule_id, group_id) VALUES (3,1)")
yields("an audience row next to a universal rule is reported before it bites",
       lambda: p.status()["stray_audience_rows"][0]["rule"], "VA-0003")

# =====================================================================
_finished = True
