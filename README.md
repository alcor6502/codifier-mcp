# Codifier MCP <img align="right" src="https://img.shields.io/badge/License-MIT-yellow.svg">

<img src="https://img.shields.io/badge/version-3.0.0-blue.svg"> <img src="https://img.shields.io/badge/python-3.12-3776AB.svg?logo=python&logoColor=white"> <img src="https://img.shields.io/badge/Unraid-7-F15A2C.svg"> <img src="https://img.shields.io/badge/MCP-22%20tools-8A63D2.svg">

**The rules your project runs on, in a registry instead of scattered Markdown —
so a chat can answer "which rules am I under?" in one call.**

Self-hosted. Nothing leaves your machine except towards the conversation that
asked. Rules are never deleted, IDs are never reused, and history is written by
the database itself.

🇮🇹 [Leggi in italiano](README.it.md)

---

## Why it exists

Give an LLM project a set of rules and they start in one file. Then a role needs
one of its own, then a second role, and eighteen months later there are 177 of
them across three documents plus the roles' own memories. Every chat opens three
files to use forty rules.

The context cost is the symptom. **The disease is that nobody can answer
quickly: this chat, right now, which rules is it under?** Answering it today
means reading three files, holding in your head which applies to whom, and
trusting that nobody wrote the same thing twice in two places. It is a reading
job, and because it is a reading job it gets done badly.

|  | Rules in Markdown | Codifier |
|---|---|---|
| "Which rules apply to me?" | open three files, filter by hand | one call, ordered |
| Changing one rule that lives in three memories | three edits, and you forget the third | one edit |
| Reusing a retired rule's number | nothing stops you | the database refuses |
| "Why is this rule here?" | ask whoever wrote it | the reason is mandatory, and kept |
| A rule that stopped being needed | stays forever | expires unless renewed |
| Two rules that say the same thing | somebody notices, eventually | flagged as a candidate pair |
| Someone edits the file by hand | invisible | recorded by a trigger |

The real leap is not the lookup: it is that **the database refuses things.** The
ID cannot be reused, the reason cannot be omitted, deletion does not exist, and
history is written by triggers — so a change made by hand with `sqlite3` is in
there too. What used to be discipline is now a constraint.

## The model in five sentences

**Consumers** are whoever downloads rules: chats *and* skills. A skill acts, and
what acts is under rules. A person is not a consumer — a rule that binds a
person says so in its body.

**Scopes** are named sets of consumers. There is no separate notion of "group":
a single consumer is a set with one element, and its singleton scope is created
by a trigger the moment the consumer is born. One kind of pointer, no branch to
get wrong.

**The reading order is the breadth of the scope.** A rule that reaches everyone
comes first, one that reaches only you comes last — and because breadth is a
`COUNT`, the order stays right by itself when a new consumer appears.

**A rule points to a set of scopes.** Widening it is one more row; the group it
already belonged to is untouched, because that group has other tenants.

**History is a photograph.** Each version records both what was declared
(`scopes`) and who was actually reached that day (`consumers`), so changing a
group tomorrow cannot rewrite what was true yesterday.

## How a rule gets in

    proposed ──(approved batch)──> active + provisional ──(promotion)──> permanent
        │                              │
        │                              └──> retired
        └──> denied  (with a reason, and the row STAYS)

Two mechanisms, and both exist because of the same diagnosis: a project went
from 63 rules to 172, not because anyone wrote without permission, but because
**adding costs a call and removing costs a decision nobody takes.**

**Expiry inverts that.** An approved rule is provisional and leaves the lists on
its own unless somebody decides to keep it. Staying costs a decision, going is
free.

**Approval is by batch, against its digest.** Proposals accumulate, and you see
them together, which is the only moment three near-duplicates are visible as
such. `rules_batch` returns the pending proposals — each with its reason — and
a digest over the whole; the lot page of the administration UI demands that
digest back, so what gets approved is provably the batch that was **read**: a
proposal arriving in between moves the digest and voids the stale approval.
Approval sits in the UI, behind the master — since v3.0.0 it is not a tool,
so no master-level secret ever travels in a conversation. (An ed25519
signature used to ride on top; it left in v2.0.0 — it was the clumsy way of
letting a person in instead of a chat, and the UI solves that at the root.)

Denial needs no digest: refusing cannot do harm. The denied row stays, with
its reason, and `rules_pending` shows a chat its own refusals — so the same idea
coming back through another chat in three weeks is something you can see,
rather than something the registry can block.

## The number is not yours to pick

`rules_propose` takes the **domain**, not the ID: the registry assigns the next
number in it, four digits, and hands it back. A number is not a choice, it is a
position in a sequence — and whoever cannot pass it cannot pick it. Four digits
because IDs are never reused, so a domain burns numbers even while only twenty
rules are alive.

There is no numbering-gap report, and that is the same decision seen from the
other side: with a counter a gap cannot happen, so a report of one could only
ever have meant somebody chose.

## Citations are marked, checked, and expanded

A citation is an ID in **round brackets**, `(VA-0002)`. An ordinary parenthesis
is ordinary prose — what makes a token a citation is the shape `XX-NNNN`, not
the bracket — so the vault's own `[[wiki links]]` stay free.

At the door the registry refuses a bare ID left outside a bracket of its own
(case does not save you), one that does not resolve, one pointing at a rule that
is **not approved yet**, and any note of your own written inside the brackets —
what is in there is not stored, and a registry that quietly dropped your words
would be worse than one that refuses them. Only the domains the project declared
are hunted, so a ticket number or a locale in a URL stays prose. That last is the one that shapes the work: file the cited
rule, have it approved, then file the rule that cites it. The number of a
proposal is not final until it is in, so a batch whose members cite each other
can be approved into a state where its pointers were right only while they were
being written.

On the way out every citation carries the current title of what it points at:

    (AL-0004)  →  (AL-0004 — alternative shares are not sold at a loss)

The gloss is generated, never stored — what goes into the database is the bare
pointer, which is why it cannot go stale — and a pointer at a retired rule
arrives already marked as such, in the text.

## What it looks like

```
rules_list(project="<code>", consumer="tax monitor")

  VA-0002  Re-read the sources       via _ALL_          breadth 7
  PE-0001  The method of the four    via deliberativi   breadth 4
  FI-0003  Estimating the bracket    via tax monitor    breadth 1
  ...
  38 rules in force · 132 outside your perimeter
```

`via` says *why* a rule is in your list, which is exactly what you need in order
to decide whether it belongs somewhere else.

## The administration page

Approving a rule is not the same act as writing one, and from v2.1 they no
longer happen in the same place. A chat proposes; a person approves, in a
browser, on the LAN.

The page is served by the same process, on a second port — 9443 by default —
because two processes on one SQLite database do not share the engine's lock.
It shows the pending batch **whole and side by side**, each proposal with the
reason it was filed: that is where three proposals saying the same thing
become visible as what they are. You tick what goes in, give a reason for what
does not, and type the master **once for the action** — four rules are not four
passwords, and a password typed four times is typed without looking.

A proposal that supersedes a rule says so **before** you decide, with the
victim's ID and its current title: approving it retires that rule in the same
transaction, and whoever approves reads both halves of the move.

The digest covers what you were **looking at**, not what you ticked. If a
proposal arrives while you read, the action comes back refused with the page as
it now is — the same digest contract the MCP tool used to carry.

Beside it, four readings that write nothing: the rules in force for a chosen
consumer, exactly as that consumer's chat reads them, brief first; a rule's
detail with its history and the diff between two versions; the pendings and the
expiring queue; and the state of the registry.

One master, from the template, and one hour of inactivity. A restart of the
service invalidates every session, deliberately: the session secret is
generated at boot and stored nowhere.

**The MCP surface moved in v3.0.0 — reconnect the connector and test in a
new conversation.** Seven tools left it: approve, renew and promote went to
the lot and pending pages, and the master operations — create, registry
index, rekey, backup — live in the UI behind the master. 22 tools remain,
and maintenance opens with the pair: project code plus that project's
architect key, generated on the project's receipt.

## Installing

Built for Unraid with the Tailscale plugin, but it is an ordinary container: a
mount for the database, one for state, and environment variables.

1. **A GitHub OAuth application of its own.** Homepage `BASE_URL`, callback
   `BASE_URL/auth/callback`. Do not recycle another service's, or the two will
   fight over the callback.
2. **`JWT_SIGNING_KEY`**: `openssl rand -hex 32`. Stable forever — change it and
   every issued token dies.
3. **The database directory must be local storage**, never a network share:
   SQLite in WAL needs real file locking.

The template in this repository **is** the configuration, and its field
descriptions are the real documentation of the deploy. Point Unraid at it, fill
the fields, Apply.

Everything else is checked at boot. The preflight is blocking — a failed check
exits 2 and the server is never reached, because a service that starts anyway
and warns is a service whose warnings nobody reads.

## Security

- **OAuth 2.1 with GitHub, restricted to one username.** That is the front door.
- **Source IP filter**, on top of OAuth and not instead of it. Both checks run
  on every MCP request, the handshake included — not only on tool calls. OAuth
  stops whoever is not authenticated; it does not stop whoever authenticates
  with their own GitHub account, and up to and including v1.1 such a stranger
  could still list every tool with its description. No rule ever left, but the
  shape of the surface did. Note that neither check covers the OAuth routes
  themselves: a stranger outside the allowed ranges can still complete a login.
  What they cannot do is speak MCP.
- **The architect key travels on every call** that writes, paired with the
  project code: no session, no mode left open by accident, and no
  container-wide code — the key is per project, kept as a hash on the
  project's own row. Reading your own rules and filing a proposal are both
  free — a working chat never needs the key.
- **One manual, with a stop line.** `reference_guide` takes no arguments at
  all — anyone the gate lets in reads it. The consumer part comes first and
  ends at a stop line; the maintenance tools past it want the key on every
  call. The separate legislator's manual of v1.4 was folded in: its door
  protected an hygiene that had no readers, since the manual is read by three
  chats and the skills do not read it at all.
- **A malformed call does not print what it carried.** FastMCP validates
  arguments before any tool runs and logs what it rejected, with the arguments
  in the line — a record that obeys no LOG_LEVEL of ours and leaves no
  `refused` line, so a clean log is no evidence it did not happen. Here those
  arguments are the project code and the architect key. From v2.1.1 the
  payload is redacted and the diagnosis is not: the tool, the parameter and the
  rule that was broken all survive.
- **The process runs as root and the database is 0644.** This is the opposite of
  the vault twin, deliberately: from the share you read and you do not touch,
  because a write by hand would bypass the triggers and break history in
  silence.
- **Project codes are not a security boundary.** They are opaque so projects
  cannot stumble into each other; no tool lists them and no error names one, and
  a wrong code answers exactly like a missing one. The real boundary is the
  OAuth gate in front.

## Testing

Three suites. No network, no FastMCP, no Docker.

```
python3 test_collaudo.py    # the engine, refusals included
python3 test_surface.py     # the seam, the image, the template
python3 test_crash.py       # SIGKILL mid-transaction, as Docker does
```

Each suite prints its own count, and no file repeats it. A number written down
in two places is two numbers, and this project has already paid for that once.

`test_surface.py` reads the source rather than running it: every call into the
engine must exist with a compatible signature, every tool that writes must pass
the maintenance gate, and no docstring may name a tool that does not exist.

## Sibling

[archivist-mcp](https://github.com/alcor6502/archivist-mcp) — a document vault
with per-dataset git versioning. Same architecture, same OAuth gate, same
blocking preflight. That one keeps files; this one keeps rules.

## Licence

MIT.
