#!/usr/bin/env python3
"""
test_crash.py — what happens if the container dies mid-write?

A child process opens a transaction, writes inside it, does NOT commit, and then
kills itself with SIGKILL — which is exactly what Docker does when it stops a
container the hard way. Then the database is reopened here.

Expected, with no manual intervention: the half-written change is not there, the
database is whole, and history has no ghost version. That last one is the point:
history is written by triggers, so a rolled-back write must leave no trace in
`rule_version` either — otherwise the story would record something that never
happened.

Since v4.0.0 the object under test is a PROJECT, not a registry: one folder, one
file, one connection. The registry above it is a text file and has nothing to
lose in a crash.

Run it with `python3 test_crash.py`. Exit code 0 means green.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import rules

ROOT = tempfile.mkdtemp(prefix="crash-")
P = rules.Project("Trial", ROOT, reference_code="r" * 16, admin_code="k" * 16)
DB = P.path
P.amend_project("domain", "VA", "create", {"reason": "vault and files"},
                actor="architect")
P.amend_project("consumer", "architect", "create", {"kind": "chat"}, actor="architect")
# Filed through the front door and approved, so the ID below is the one the
# COUNTER handed out: VA-0001, because it is the first of its domain.
RID = P.propose("VA", "R", "First rule", "Original body.", "setup", "all",
                "architect")["id"]
assert RID == "VA-0001", RID
LOT = P.batch()
P.decide(LOT["digest"], [r["id"] for r in LOT["pending"]], {})
BASE_VERSIONS = len(P.get_rules([RID], history=True)["rules"][0]["history"])
RULE_PK = P.cx.execute("SELECT rule_id, domain_id FROM rule").fetchone()
P.close()

CHILD = textwrap.dedent(f"""
    import os, signal, sqlite3, sys
    cx = sqlite3.connect({DB!r})
    cx.isolation_level = None
    cx.execute("PRAGMA foreign_keys=ON")
    cx.execute("BEGIN IMMEDIATE")
    cx.execute("UPDATE rule SET body='BODY NEVER COMMITTED', event='crash', "
               "updated_at='2026-01-01T00:00:00Z' WHERE rule_id=?",
               ({RULE_PK[0]!r},))
    cx.execute("INSERT INTO rule (domain_id, seq, type, title, body, status, "
               "reach, reason, created_at, updated_at) VALUES "
               "(?,9999,'R','ghost','never born','active','all','crash',"
               "'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')",
               ({RULE_PK[1]!r},))
    sys.stdout.write("written, not committed\\n"); sys.stdout.flush()
    os.kill(os.getpid(), signal.SIGKILL)
""")

p = subprocess.run([sys.executable, "-c", CHILD], capture_output=True, text=True)
print(f"  child said {p.stdout.strip()!r} — signal {-p.returncode} (9 = SIGKILL)")
assert p.returncode == -signal.SIGKILL, p

P2 = rules.Project("Trial", ROOT, reference_code="r" * 16, admin_code="k" * 16)
body = P2.cx.execute("SELECT body FROM rule WHERE rule_id=?", (RULE_PK[0],)).fetchone()[0]
ghosts = P2.cx.execute("SELECT COUNT(*) FROM rule WHERE seq=9999").fetchone()[0]
integrity = P2.cx.execute("PRAGMA integrity_check").fetchone()[0]
journal = P2.cx.execute("PRAGMA journal_mode").fetchone()[0]
generation = P2.cx.execute("PRAGMA user_version").fetchone()[0]
versions = len(P2.get_rules([RID], history=True)["rules"][0]["history"])
ghost_versions = P2.cx.execute(
    "SELECT COUNT(*) FROM rule_version WHERE title='ghost'").fetchone()[0]

print(f"  body after the crash : {body!r}")
print(f"  ghost rule rows      : {ghosts}")
print(f"  ghost version rows   : {ghost_versions}")
print(f"  integrity_check      : {integrity}")
print(f"  journal_mode         : {journal}")
print(f"  schema generation    : {generation}")
print(f"  versions in history  : {versions} (was {BASE_VERSIONS})")

assert body == "Original body.", "the uncommitted write survived"
assert ghosts == 0, "a ghost row survived"
assert ghost_versions == 0, "a ghost row left a version behind"
assert integrity == "ok"
assert journal == "wal"
assert generation == rules.SCHEMA_GENERATION, "the reopen moved the generation"
assert versions == BASE_VERSIONS, "a rolled-back write left a version in history"
assert P2.repaired == [], f"the reopen had to repair something: {P2.repaired}"
P2.close()

print("\nOK — rolled back on its own, database whole, history clean.")
