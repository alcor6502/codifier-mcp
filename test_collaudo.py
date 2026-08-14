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
        p.amend_project("project", "", "amend", profile, actor="architect",
                        auth_code=p.mint_auth_code()["auth_code"])
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


def e_needs(entity: str) -> bool:
    """The project itself is not retired from a tool at all, so its refusal is
    the older one and arrives first — the ladder never gets asked."""
    return entity != "project"


def code(p):
    """A live one-time auth code, the way the maintenance page hands one out.

    Every MODIFICATION in this suite pays the same price a real one pays, and
    that is the point of the helper: a suite that could modify without a code
    would be measuring a ladder nobody has to climb. Minting is not gated here
    because the gate on minting is the web UI's password, which no tool and no
    test carries."""
    return p.mint_auth_code()["auth_code"]


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


def _refusal(gesture) -> str:
    """The TEXT of a refusal, for the cases where what matters is not that it
    refused but WHAT IT SAYS — a refusal that names the heir is actionable and
    one that does not is a wall. Returns '' if the gesture went through, so the
    assertion fails instead of the run dying."""
    try:
        gesture()
        return ""
    except rules.RulesError as exc:
        return str(exc)


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
# The MIXED call, and it is the guarantee the surface used to carry alone —
# where no suite could reach it. What is refused is the shortcut of writing the
# part you are allowed and dropping the rest.
refused("mixed fields: refused WHOLE, naming the field that costs more",
        lambda: rules.Project.refuse_mixed("consumer", "amend",
                                           {"specs": "x", "brief": "y"}),
        "refused WHOLE")
refused("and it names the low field too, so the caller can split the call",
        lambda: rules.Project.refuse_mixed("consumer", "amend",
                                           {"specs": "x", "brief": "y"}), "specs")
allowed("specs alone is not mixed: the ordinary door answers",
        lambda: rules.Project.refuse_mixed("consumer", "amend", {"specs": "x"}))
allowed("and neither is a call where everything needs the higher gate",
        lambda: rules.Project.refuse_mixed("consumer", "amend",
                                           {"brief": "y", "name": "z"}))
allowed("nor a retirement, which carries no fields at all",
        lambda: rules.Project.refuse_mixed("consumer", "retire", {}))

equals("it answers for RULES too, so they do not grow a second scale",
       rules.Project.port_for("rule", "amend"), "auth")
equals("and ending one is a modification like the rest",
       rules.Project.port_for("rule", "retire"), "auth")


# =====================================================================
print("\n— THE SECOND FACTOR: it burns WITH the gesture, not before —")
p = project()
rid = rule(p, "targeted", groups=["deliberativi"], title="the perimeter to shrink")


def ver_of(prj, r):
    return prj.get_rules([r], history=True)["rules"][0]["history"][-1]["version"]


refused("a modification with no code at all",
        lambda: p.amend_rule(rid, "targeted", ["deliberativi"], [], ver_of(p, rid),
                             "why", "architect"), "one-time auth_code")
refused("a code invented rather than minted",
        lambda: p.amend_rule(rid, "targeted", ["deliberativi"], [], ver_of(p, rid),
                             "why", "architect", auth_code="X" * 12),
        "not one of this project's")
other = project()
refused("a code minted in ANOTHER project — it belongs to the database it was minted in",
        lambda: p.amend_rule(rid, "targeted", ["deliberativi"], [], ver_of(p, rid),
                             "why", "architect", auth_code=code(other)),
        "not one of this project's")
expired = code(p)
p.cx.execute("UPDATE auth_code SET expires_at='2000-01-01T00:00:00Z' "
             "WHERE spent_at IS NULL AND expires_at > '2000-01-02T00:00:00Z'")
refused("a code that ran out of minutes",
        lambda: p.amend_rule(rid, "targeted", ["deliberativi"], [], ver_of(p, rid),
                             "why", "architect", auth_code=expired), "expired on")

# The one that is not a confirmation: a refusal AFTER the gate must roll the
# burn back with everything else. Injected on purpose, because nothing in the
# ordinary path refuses that late — every argument is checked before the
# transaction opens, which is exactly why this property would otherwise be
# invisible until the day a trigger fires in production. If the burn is ever
# moved out of the gesture's transaction, the SECOND of these two goes red.
spendable = code(p)


def _refuse_late(*a, **kw):
    raise rules.RulesError("the write refused, after the gate")


_real_write = p._write_audience
p._write_audience = _refuse_late
refused("a gesture refused after the gate is still a refusal",
        lambda: p.amend_rule(rid, "targeted", ["deliberativi"], [], ver_of(p, rid),
                             "why", "architect", auth_code=spendable),
        "after the gate")
p._write_audience = _real_write
allowed("and the code it carried was NOT spent: the burn rolled back with it",
        lambda: p.amend_rule(rid, "targeted", [], ["architect"], ver_of(p, rid),
                             "the architect desk alone", "architect",
                             auth_code=spendable))
refused("but once the gesture succeeds, that code is nothing",
        lambda: p.amend_rule(rid, "targeted", [], ["architect"], ver_of(p, rid),
                             "again", "architect", auth_code=spendable),
        "already spent")
yields("and the spent row says what spent it",
       lambda: p.auth_codes()["spent"][0]["spent_action"], "rule.amend")
allowed("CREATING still takes no one-time code: a created thing is attached to nothing",
        lambda: p.amend_project("domain", "RL", "create", {"reason": "rules of the road"},
                                actor="architect"))

# One case per ENTITY, and not for symmetry: the burn rides on the gesture the
# handler opens, so a handler that went back to opening a plain transaction
# would let every modification of ITS entity through with no second factor and
# nothing else in this suite would notice. Four handlers, four cases.
for _entity, _fields in (("project", {"brief": "a new mandate"}),
                         ("domain", {"description": "a new gloss"}),
                         ("consumer", {"brief": "a new mandate"}),
                         ("group", {"members": ["architect"]})):
    refused(f"{_entity}: amended with no one-time code",
            (lambda e=_entity, f=_fields: p.amend_project(
                e, {"project": "", "domain": "VA", "consumer": "architect",
                    "group": "deliberativi"}[e], "amend", f, actor="architect")),
            "one-time auth_code")
    refused(f"{_entity}: retired with no one-time code",
            (lambda e=_entity: p.amend_project(
                e, {"project": "", "domain": "RL", "consumer": "advisory",
                    "group": "automatismi"}[e], "retire", {}, reason="done",
                actor="architect")),
            "one-time auth_code" if e_needs(_entity) else "catastrophic has no tool")

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
    "group", "automatismi", "amend", {"members": []},
    auth_code=code(p)), "retire it")
refused("reviving something that is not retired", lambda: p.amend_project(
    "consumer", "advisory", "revive", {}, auth_code=code(p)), "nothing to revive")
refused("the project created from a tool", lambda: p.amend_project(
    "project", "", "create", {}), "catastrophic has no tool")
refused("a negative queue cap", lambda: p.amend_project(
    "project", "", "amend", {"queue_cap": -1},
    auth_code=code(p)), "none of the three")

# A NAME IS ONE WORD, and the space is the mistake worth naming: it is the
# character the eye does not find. Both entities, because a group is quoted the
# way a consumer is — a rule that held for one and not the other would be a
# rule with an exception, which is a rule nobody can check.
refused("a consumer name with a space in it", lambda: p.amend_project(
    "consumer", "fidelity advisory", "create", {"kind": "chat"}), "ONE WORD")
refused("and the refusal names the space, not just the pattern",
        lambda: p.amend_project("consumer", "fidelity advisory", "create",
                                {"kind": "chat"}), "has a space in it")
refused("a GROUP name with a space in it", lambda: p.amend_project(
    "group", "i deliberativi", "create", {"members": ["architect"]}), "ONE WORD")
refused("and a rename cannot smuggle one back in", lambda: p.amend_project(
    "consumer", "advisory", "amend", {"name": "fidelity advisory"},
    auth_code=code(p)), "ONE WORD")
allowed("a dash is not a space", lambda: p.amend_project(
    "consumer", "fidelity-advisory", "create", {"kind": "chat"}, actor="architect"))
allowed("and neither is an underscore", lambda: p.amend_project(
    "group", "i_deliberativi", "create", {"members": ["architect"]}, actor="architect"))
# THE OTHER SIDE of the same rule, and it is the case narrowing `RE_NAME` to a
# single pattern would have taken away in silence: a PROJECT name has spaces BY
# DESIGN — the folder is the name as spelled, the file is the slug. Proved
# HERE, on the parser and not on a Registry, because when the project name
# stops being legal every registry this suite's twin builds fails at setup and
# the run dies before reaching the case that was supposed to notice.
yields("a PROJECT name may hold spaces — a consumer name is the narrow one",
       lambda: rules._registry_lines(
           f"Financial Portfolio | {'r' * 16} | {'k' * 16}", "registry")[0][1],
       "Financial Portfolio")

out = allowed("a rename goes through", lambda: p.amend_project(
    "consumer", "advisory", "amend", {"name": "advisor"}, actor="architect",
    auth_code=code(p)))
equals("and the verdict says the old name STOPS RESOLVING",
       "STOPS RESOLVING" in (out or {}).get("note", ""), True)
equals("and it names what lives outside the registry",
       all(w in (out or {}).get("note", "")
           for w in ("skill", "instructions", "scheduled")), True)
refused("the old name does not resolve any more",
        lambda: p.list_rules("advisory"), "not a consumer of this project")
allowed("the new one does", lambda: p.list_rules("advisor"))

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
print("\n— A CITATION POINTS AT WHAT CAN STILL BE USED —")
# The door the 4.0.1 closed. Until then a body could point at a rule that had
# been taken out of force, and the only sign was a mark added when somebody
# happened to read it.
p = project()
gone = rule(p, title="the withdrawn one")
p.retire(gone, "it stopped applying", "architect", auth_code=code(p))
refused("a rule that cites a RETIRED rule", lambda: p.propose(
    "VA", "R", "t", "see (VA-0001)", "why", "all", "architect"), "out of force")
yields("and the refusal says nothing replaced it, so the author knows to use words",
       lambda: "nothing replaced it" in _refusal(lambda: p.propose(
           "VA", "R", "t", "see (VA-0001)", "why", "all", "architect")), True)
p2 = project()
old = rule(p2, title="the old way")
new = p2.propose("VA", "R", "the new way", "b", "it changed", "all", "architect",
                 supersedes=old)
bt = p2.batch()
p2.decide(bt["digest"], [new["id"]], {})
refused("a rule that cites a SUPERSEDED rule", lambda: p2.propose(
    "VA", "R", "t", "see (VA-0001)", "why", "all", "architect"), "out of force")
yields("and THE HEIR IS NAMED, which is what makes the refusal actionable",
       lambda: f"superseded by {new['id']}" in _refusal(lambda: p2.propose(
           "VA", "R", "t", "see (VA-0001)", "why", "all", "architect")), True)
allowed("while the heir itself may be cited",
        lambda: p2.propose("VA", "R", "t", f"see ({new['id']})", "why", "all",
                           "architect"))

# AND THE ONE NOBODY DECIDED: a provisional term running out. `_in_force` says
# it binds nobody, `project_status` counts it out and reading writes `· expired`
# — the door was the only part of the system still treating it as law, because
# it filtered on the VERB `retired` instead of on force. Named apart from a
# retirement on purpose: a retirement is a gesture, this is a clock, and the
# rule comes back under the same ID when it is renewed.
p3 = project()
prov = rule(p3, title="a provisional one")
p3.cx.execute("UPDATE rule SET expires_at='2000-01-01T00:00:00Z' WHERE rule_id=?",
              (p3._rule_row(prov)["rule_id"],))
yields("the rule is still `active` and yet in force it is NOT — which is the "
       "whole trap: a status check and a force check disagree here",
       lambda: (p3._rule_row(prov)["status"], p3._in_force(p3._rule_row(prov))),
       ("active", False))
refused("a rule that cites a rule whose term EXPIRED", lambda: p3.propose(
    "VA", "R", "t", f"see ({prov})", "why", "all", "architect"), "EXPIRED")
refused("and a task cannot either", lambda: p3.task_add(
    "architect", "t", f"see ({prov})", "architect"), "EXPIRED")
yields("and the refusal points at the renewal, not at a rewrite: nobody took "
       "this rule out of force",
       lambda: "renew it" in _refusal(lambda: p3.task_add(
           "architect", "t", f"see ({prov})", "architect")).lower(), True)

# =====================================================================
print("\n— THE TASK LOG IS THE SAME DOOR, WITH TWO DIFFERENCES —")
# A task is not law, so what it may point at is not the same set. Until 4.0.1
# the task door ran the SANITISATION alone and followed no pointer at all.
p = project()
live = rule(p, title="in force")
queued = p.propose("VA", "R", "still in the queue", "b", "why", "all", "architect")
refused("a task citing a rule that was never defined", lambda: p.task_add(
    "architect", "t", "see (VA-9999)", "architect"), "does not resolve")
allowed("a task citing a rule IN FORCE",
        lambda: p.task_add("architect", "t", f"see ({live})", "architect"))
allowed("a task citing an OPEN PROPOSAL, which is the log doing its job",
        lambda: p.task_add("architect", "t", f"what of ({queued['id']})?", "architect"))
refused("a rule citing that same open proposal", lambda: p.propose(
    "VA", "R", "t", f"see ({queued['id']})", "why", "all", "architect"),
    "not in force yet")
p.retire(live, "it stopped applying", "architect", auth_code=code(p))
refused("a task citing a RETIRED rule", lambda: p.task_add(
    "architect", "t", f"see ({live})", "architect"), "out of force")
lot = p.batch()
p.decide(lot["digest"], [], {queued["id"]: "not now: it needs the tax desk"})
refused("a task citing a DENIED rule", lambda: p.task_add(
    "architect", "t", f"see ({queued['id']})", "architect"), "REFUSED")

# A task citing a TASK: refused only when it resolves to nothing. A CLOSED one
# stays citable, and that is the difference between force and history — a task
# never bound anybody, so being readable afterwards is the whole of its value.
p = project()
first = p.task_add("architect", "the first errand", "b", "architect")["id"]
refused("a task citing a task that was never opened", lambda: p.task_add(
    "architect", "t", "see (TK-9999)", "architect"), "not a task in this project")
allowed("a task citing an OPEN task",
        lambda: p.task_add("architect", "t", f"see ({first})", "architect"))
p.task_close(first, "architect", outcome="done")
allowed("a task citing a CLOSED task — history is not a broken pointer",
        lambda: p.task_add("architect", "t", f"as in ({first})", "architect"))
yields("and reading labels the state, which is why the door need not refuse it",
       lambda: " · completed" in p._expand(f"({first})"), True)
refused("the OUTCOME goes through the same door, and it is written once",
        lambda: p.task_close(
            p.task_add("architect", "t", "b", "architect")["id"],
            "architect", outcome="done, see (VA-9999)"), "does not resolve")

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
    universal, "targeted", ["deliberativi"], [], v, "", "architect",
    auth_code=code(p)), "reason is required")
refused("writing against a version that moved", lambda: p.amend_rule(
    universal, "targeted", ["deliberativi"], [], 99, "why", "architect",
    auth_code=code(p)), "somebody changed it after you read it")
narrowed = allowed("a UNIVERSAL rule narrowed onto a group — the gesture the DDL blocked",
                   lambda: p.amend_rule(universal, "targeted", ["deliberativi"], [],
                                        v, "only the deliberative desks now", "architect",
                                        auth_code=code(p)))
equals("and it says who it stopped reaching",
       (narrowed or {})["no_longer_reaches"], ["Alfredo", "news"])
yields("the perimeter is what the rule now shows",
       lambda: p.get_rules([universal])["rules"][0]["groups"], ["deliberativi"])
def ver(rid):
    return p.get_rules([rid], history=True)["rules"][0]["history"][-1]["version"]


refused("widening by one consumer", lambda: p.amend_rule(
    targeted, "targeted", ["deliberativi"], ["news"], ver(targeted), "one more",
    "architect", auth_code=code(p)), "it would newly bind news")
refused("widening all the way back to everyone", lambda: p.amend_rule(
    targeted, "all", [], [], ver(targeted), "everyone now", "architect",
    auth_code=code(p)), "not a narrowing")
refused("and the refusal carries the cure", lambda: p.amend_rule(
    targeted, "all", [], [], ver(targeted), "everyone now", "architect",
    auth_code=code(p)), "supersede")
p.amend_project("group", "deliberativi", "amend", {"members": ["architect"]},
                actor="architect", auth_code=code(p))
# A group whose members have all ENDED: the new perimeter is a subset of the
# old one — it has to be, it is empty — so containment says yes and only the
# empty guard can say no.
p.amend_project("group", "soli", "create", {"members": ["news"]}, actor="architect")
p.amend_project("consumer", "news", "retire", {}, reason="the skill was withdrawn",
                actor="architect", auth_code=code(p))
refused("a narrowing that leaves NOBODY is a retirement in disguise",
        lambda: p.amend_rule(targeted, "targeted", ["soli"], [], ver(targeted), "why",
                             "architect", auth_code=code(p)),
        "retirement in disguise")
refused("and it points at the door that gesture really goes through",
        lambda: p.amend_rule(targeted, "targeted", ["soli"], [], ver(targeted), "why",
                             "architect", auth_code=code(p)),
        "rules_retire")
refused("the content is not touched from here", lambda: p.amend_project(
    "rule", "x", "amend", {}), "entity 'rule'")
refused("a rule that is not in force has no perimeter to narrow", lambda: (
    p.propose("VA", "R", "still queued", "b", "why", "all", "architect"),
    p.amend_rule("VA-0003", "targeted", ["automatismi"], [], 1, "why", "architect",
                 auth_code=code(p))),
    "not in force")

# =====================================================================
print("\n— THE EMPTY GUARD: it NAMES the rules —")
p = project()
only_news = rule(p, "targeted", exceptions=["news"], title="the news rule")
refused("retiring the last consumer a rule in force reaches",
        lambda: p.amend_project("consumer", "news", "retire", {}, reason="finished",
                                actor="architect", auth_code=code(p)),
        "the news rule")
refused("and it says what to do about it",
        lambda: p.amend_project("consumer", "news", "retire", {}, reason="finished",
                                actor="architect", auth_code=code(p)),
        "binding nobody")
p2 = project()
grp = rule(p2, "targeted", groups=["automatismi"], title="the automatic rule")
refused("pulling out of a group the only people a rule in force reaches",
        lambda: p2.amend_project("group", "automatismi", "amend",
                                 {"members": ["architect"]}, actor="architect",
                                 auth_code=code(p2)),
        "the automatic rule")
refused("retiring that group outright, same guard",
        lambda: p2.amend_project("group", "automatismi", "retire", {},
                                 reason="done", actor="architect",
                                 auth_code=code(p2)),
        "the automatic rule")
allowed("taking ONE member out, when the rule still reaches somebody",
        lambda: p2.amend_project("group", "automatismi", "amend",
                                 {"members": ["advisory"]}, actor="architect",
                                 auth_code=code(p2)))
rule(p2, "targeted", groups=["deliberativi"], exceptions=["news"], title="mixed")
allowed("ADDING a member passes even when it covers an exception",
        lambda: p2.amend_project("group", "deliberativi", "amend",
                                 {"members": ["architect", "advisory", "news"]},
                                 actor="architect", auth_code=code(p2)))
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
refused("retiring without a reason", lambda: p.retire(rid, "", auth_code=code(p)),
        "price of a retirement")
refused("retiring something that was never defined",
        lambda: p.retire("VA-0099", "why", auth_code=code(p)), "never defined")
allowed("a rule in force ends", lambda: p.retire(rid, "the reason it stopped applying",
                                                 "architect", auth_code=code(p)))
refused("and it does not end twice", lambda: p.retire(rid, "again", auth_code=code(p)),
        "already retired")
p = project()
rule(p, title="in force")
refused("retiring a domain that still has rules in force",
        lambda: p.amend_project("domain", "VA", "retire", {}, reason="done",
                                actor="architect", auth_code=code(p)), "in force")
refused("and the refusal names them",
        lambda: p.amend_project("domain", "VA", "retire", {}, reason="done",
                                actor="architect", auth_code=code(p)), "in force")
# =====================================================================
print("\n— THE CREDENTIALS ARE ASKED FIRST, AND THE STATE SAYS NOTHING —")
# Observed on the live service: with the admin code and an INVENTED one-time
# code, retiring a rule that does not exist replied `PE-9999: never defined in
# this project`. The state came out of a door whose second lock was never
# opened. It was argued that this was harmless — whoever holds the admin code
# reads the whole corpus anyway — and that argument is true and beside the
# point: it makes the ordering something to be re-decided at every door, and
# the doors then disagree. The house rule holds instead: every parameter is
# validated, the credentials first, and the refusal says nothing about what was
# being reached for.
p = project()
alive = rule(p, title="a rule that does exist")
_absent = _refusal(lambda: p.retire("VA-0099", "why", "architect", auth_code="000000"))
_present = _refusal(lambda: p.retire(alive, "why", "architect", auth_code="000000"))
equals("an invented one-time code refuses the same way whether the rule exists "
       "or not — the refusal is BYTE FOR BYTE the same", _absent, _present)
yields("and it names the credential", lambda: "auth_code" in _absent, True)
yields("while saying nothing about the rule: not the ID, not 'never defined', "
       "not 'already retired'",
       lambda: not any(w in _absent for w in ("VA-0099", "never defined",
                                              "already retired", alive)), True)

# ⚠ VERIFYING IS NOT BURNING, and this is the case that guards the cure against
# itself. The early check only READS; the code is spent inside the transaction
# of the gesture that succeeded. Move the burn up with the check and a typo
# further down eats the code and sends the caller back to the maintenance page
# — which is the exact defect the late burn was designed to prevent, and it
# would arrive disguised as a security improvement.
live_code = code(p)
refused("a gesture that fails AFTER the code was verified",
        lambda: p.retire(alive, "", "architect", auth_code=live_code),
        "price of a retirement")
yields("and the code is still live: verified early, spent late",
       lambda: p.auth_codes()["count_live"], 1)
allowed("so the SAME code still works on the second attempt",
        lambda: p.retire(alive, "the reason it stopped applying", "architect",
                         auth_code=live_code))
yields("and only now is it spent", lambda: p.auth_codes()["count_live"], 0)
yields("by the gesture that succeeded, which is what the log says",
       lambda: p.auth_codes()["spent"][0]["spent_action"], "rule.retire")

# ONE CASE PER DOOR, and not for symmetry. The cure is three separate calls —
# `rules_amend`, `rules_retire`, `amend_project` — and a suite that exercised
# only one of them would keep 261 green while a refactor put the other two back
# to answering about the state first. `amend_project` is the one that matters
# most and the one it is easiest to forget: behind it sit four handlers, and
# `_amend_consumer` says `this project has no consumer by that name`.
p = project()
live = rule(p, title="a rule in force")
_v = p.get_rules([live], history=True)["rules"][0]["history"][-1]["version"]
for _what, _call in (
        ("rules_amend, on a rule that does not exist",
         lambda: p.amend_rule("VA-0099", "targeted", ["deliberativi"], [], 1, "why",
                              "architect", auth_code="000000")),
        ("amend_project, on a consumer that does not exist",
         lambda: p.amend_project("consumer", "nessuno", "amend", {"brief": "x"},
                                 actor="architect", auth_code="000000")),
        ("amend_project, on a group that does not exist",
         lambda: p.amend_project("group", "nessuno", "retire", {}, reason="done",
                                 actor="architect", auth_code="000000")),
        ("amend_project, on a domain that does not exist",
         lambda: p.amend_project("domain", "ZZ", "amend", {"description": "x"},
                                 actor="architect", auth_code="000000"))):
    _msg = _refusal(_call)
    yields(f"{_what}: the one-time code answers first",
           lambda m=_msg: "auth_code" in m, True)
    yields(f"{_what}: and the state stays quiet",
           lambda m=_msg: not any(w in m.lower() for w in
                                  ("no consumer", "no group", "no domain",
                                   "never defined", "nessuno", "zz")), True)

p = project()
rule(p, title="in force")
allowed("a domain with nothing under it retires",
        lambda: p.amend_project("domain", "ST", "retire", {}, reason="never used",
                                actor="architect", auth_code=code(p)))
refused("and nothing new is filed under it", lambda: p.propose(
    "ST", "R", "t", "b", "why", "all", "architect"), "was retired on")

# =====================================================================
print("\n— READING: project_info, technical and ALIVE —")
p = project(brief="the owner's book", specs="cash at 12%")
info = allowed("one call", lambda: p.project_info())
equals("no profile: brief and specs are rules_list's, and are not paid twice",
       "profile" in (info or {}), False)
# `yields` and not `equals` all the way down this section, and injection 6
# bought the difference: a key that VANISHES from a payload raises a KeyError
# out of the module body, the run is cut short, and every case after it stops
# measuring. A guard removed has to come back as a red line with a name.
yields("it hands back the NAMES",
       lambda: [c["name"] for c in info["consumers"]],
       ["Alfredo", "advisory", "architect", "news"])
yields("and the counts are the three the payload cannot yield",
       lambda: set(info["counts"]), {"rules_in_force", "proposed", "tasks_open"})
yields("on a fresh project all three are zero", lambda: info["counts"],
       {"rules_in_force": 0, "proposed": 0, "tasks_open": 0})
# COUNTED, not written. Three literals would satisfy the case above for ever,
# and the payload cannot be used to check them — that is the whole reason
# these three survived the cut while the `_live` ones did not.
rule(p, title="one in force")
p.propose("VA", "R", "one waiting", "b", "why", "all", "architect")
p.task_add("advisory", "one on a desk", "b", "architect")
yields("and every one of them MOVES with the database",
       lambda: p.project_info()["counts"],
       {"rules_in_force": 1, "proposed": 1, "tasks_open": 1})
yields("the note tells the reader to find ITSELF in the list",
       lambda: "YOUR consumer" in info["note"], True)
p.amend_project("consumer", "news", "retire", {}, reason="the run stopped",
                actor="architect", auth_code=code(p))
gone = allowed("after a retirement", lambda: p.project_info())
yields("a retired consumer is NOT in the list — the name missing IS the answer",
       lambda: [c["name"] for c in gone["consumers"]],
       ["Alfredo", "advisory", "architect"])
yields("and no retired_at survives anywhere in the payload, to be misread",
       lambda: any("retired_at" in x for x in (gone["consumers"] + gone["domains"]
                                               + gone["groups"])), False)
# One level down, and it is the door this rule could have been left open by:
# retiring deletes no junction row, so the membership is still sitting there.
yields("nor inside a GROUP it still belongs to by junction row",
       lambda: [x["members"] for x in gone["groups"] if x["name"] == "automatismi"],
       [["advisory"]])
p.amend_project("domain", "ST", "retire", {}, reason="never used",
                actor="architect", auth_code=code(p))
yields("a retired domain is gone from the legend too",
       lambda: [x["code"] for x in p.project_info()["domains"]], ["VA"])
# And the price of all that: the retired have to be readable SOMEWHERE, or the
# refusal below points at something invisible.
refused("the retired name is still TAKEN", lambda: p.amend_project(
    "consumer", "news", "create", {"kind": "skill"}), "already has a consumer")
allowed("and the admin report is where it is read", lambda: p.status())
yields("the retired consumer, with its date and its reason",
       lambda: [(c["name"], c["reason"])
                for c in p.status()["retired"]["consumers"]],
       [("news", "the run stopped")])
yields("and the retired domain with it",
       lambda: [x["code"] for x in p.status()["retired"]["domains"]], ["ST"])
allowed("so revive has a target somebody can see", lambda: p.amend_project(
    "consumer", "news", "revive", {}, actor="architect", auth_code=code(p)))
yields("and the name is back in the live list", lambda: sorted(
    c["name"] for c in p.project_info()["consumers"]),
    ["Alfredo", "advisory", "architect", "news"])

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
yields("the desk arrives as a LIST of open tasks, not two counters",
       lambda: set(start["desk"]), {"open", "open_count"})
yields("empty desk, empty list", lambda: start["desk"],
       {"open": [], "open_count": 0})
p.task_add("advisory", "the older one", "b", "architect")
p.task_add("advisory", "the urgent one", "b", "architect", urgent=True)
desk = allowed("with post on it", lambda: p.list_rules("advisory")["desk"])
yields("urgent first, then the oldest — the same order tasks_list uses",
       lambda: [t["title"] for t in desk["open"]],
       ["the urgent one", "the older one"])
yields("four fields and no more: id, title, urgent, age",
       lambda: set(desk["open"][0]), {"id", "title", "urgent", "age_days"})
# THE CONFINE, and it is the whole reason B7 could be superseded without being
# betrayed: the list comes in, the PROSE does not. A chat that will never open
# a task pays four fields, not a document.
yields("the BODY of a task does not come to a session start",
       lambda: any("body" in t for t in desk["open"]), False)
yields("and the bodies are still one call away, where the ceiling is",
       lambda: "b" in p.task_get([desk["open"][0]["id"]])["tasks"][0]["body"], True)
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

# The cut, and it has to be DECLARED against the real total: a truncated list
# that says nothing is a short list, and a session start would read it as an
# empty desk.
for _i in range(rules.TASKS_LIST_CAP + 1):
    p.task_add("news", f"task number {_i}", "b", "architect")
full = allowed("a desk past the ceiling", lambda: p.list_rules("news")["desk"])
yields("cuts at the cap", lambda: len(full["open"]), rules.TASKS_LIST_CAP)
equals("and DECLARES it", (full or {}).get("truncated"), True)
yields("against the REAL total, not the length of what came back",
       lambda: full["open_count"], rules.TASKS_LIST_CAP + 1)
equals("and the note carries that total in words",
       str(rules.TASKS_LIST_CAP + 1) in (full or {}).get("note", ""), True)

# =====================================================================
print("\n— READING: the detail, and the story —")
refused("more IDs than the ceiling is REFUSED, not trimmed",
        lambda: p.get_rules([u] * (rules.GET_IDS + 1)), "REFUSED and not trimmed")
yields("a short ID resolves on READ", lambda: p.get_rules(["VA-01"])["rules"][0]["id"], u)
yields("an ID that is not there is named, not invented",
       lambda: p.get_rules(["VA-0099"])["not_found"], ["VA-0099"])
allowed("the perimeter moves, and only the perimeter",
        lambda: p.amend_rule(g, "targeted", [], ["advisory"], 2,
                             "the architect desk stops carrying this", "architect",
                             auth_code=code(p)))
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
p.amend_project("consumer", "news", "amend", {"secret": "s3cret"}, actor="architect",
                auth_code=code(p))
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
# THE POINTER IS WRITTEN WHILE ITS TARGET IS IN FORCE, and since 4.0.1 that is
# the only way it can be written at all — the door refuses the other way round.
# Which is exactly why the sweep still has a job: it catches the pointer that
# goes bad LATER, and no door can do that.
p.propose("VA", "R", "points at a dead one", "see (VA-0001)", "why", "all", "architect")
orphan = p.task_add("architect", "t", "and so does (VA-0001)", "architect")["id"]
closed = p.task_add("architect", "t", "this one too: (VA-0001)", "architect")["id"]
p.task_close(closed, "architect", outcome="done")
p.retire(a, "it stopped applying", "architect", auth_code=code(p))
rep = allowed("one call", lambda: p.status())
equals("it counts, and says it counted", (rep or {})["counted"]["in_force"], 1)
equals("a citation towards a retired rule is reported, in the rule AND in the task",
       [(d["in"], d["cites"], d["state"]) for d in (rep or {})["dangling_citations"]],
       [("VA-0003", "VA-0001", "retired"), (orphan, "VA-0001", "retired")])
equals("and the CLOSED task is not, because nothing could ever clear it",
       [d["in"] for d in (rep or {})["dangling_citations"] if d["in"] == closed], [])
# The EXPIRED one is the case the door cannot take by construction — the rule
# was in force when the pointer was written and a clock did the rest, so if the
# sweep does not carry it, nothing does.
lapsing = p.task_add("architect", "t", f"and this points at ({b_})", "architect")["id"]
p.cx.execute("UPDATE rule SET expires_at='2000-01-01T00:00:00Z' WHERE rule_id=?",
             (p._rule_row(b_)["rule_id"],))
yields("a citation towards a rule whose term ran out is reported too",
       lambda: sorted((d["in"], d["cites"], d["state"])
                      for d in p.status()["dangling_citations"]
                      if d["state"] == "expired"),
       [(lapsing, b_, "expired")])
equals("a domain nothing was ever filed under is reported",
       (rep or {})["domains_with_no_rules"], ["ST"])
equals("and a consumer no rule reaches", (rep or {})["consumers_no_rule_reaches"],
       ["news"])
# A HUMAN IS A DESTINATION, NOT A SUBJECT — and this was true before it was
# said out loud: the assertion above proved it and named nothing, so a refactor
# that dropped the exclusion would have failed a case about something else.
# `Alfredo` and `news` are in exactly the same position — alive, and reached by
# no rule in force — and only one of them is reported. That contrast IS the
# rule, and it goes red the moment the exclusion goes.
equals("a human reached by no rule is NOT reported: they receive tasks, "
       "and no rule binds them through the registry",
       [c for c in (rep or {})["consumers_no_rule_reaches"] if c == "Alfredo"], [])
yields("and they are alive and unreached all the same, which is what makes the "
       "silence a decision instead of an absence",
       lambda: (p.cx.execute("SELECT kind FROM consumer WHERE name='Alfredo' "
                             "AND retired_at IS NULL").fetchone()[0],
                bool(p._reaching(p.cx.execute(
                    "SELECT consumer_id FROM consumer WHERE name='Alfredo'"
                ).fetchone()[0]))),
       ("human", False))
p.cx.execute("INSERT INTO rule_audience_group (rule_id, group_id) VALUES (3,1)")
yields("an audience row next to a universal rule is reported before it bites",
       lambda: p.status()["stray_audience_rows"][0]["rule"], "VA-0003")

# =====================================================================
_finished = True
