# Codifier MCP <img align="right" src="https://img.shields.io/badge/License-MIT-yellow.svg">

<img src="https://img.shields.io/badge/version-4.0.1-blue.svg"> <img src="https://img.shields.io/badge/python-3.12-3776AB.svg?logo=python&logoColor=white"> <img src="https://img.shields.io/badge/Unraid-7-F15A2C.svg"> <img src="https://img.shields.io/badge/MCP-16%20tools-8A63D2.svg">

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
| Two rules that say the same thing | somebody notices, eventually | the queue is read whole before anything is approved |
| Someone edits the file by hand | invisible | recorded by a trigger |

The real leap is not the lookup: it is that **the database refuses things.** The
ID cannot be reused, the reason cannot be omitted, deletion does not exist, and
history is written by triggers — so a change made by hand with `sqlite3` is in
there too. What used to be discipline is now a constraint.

## The model in six sentences

**A project is one database**, named in a text registry the owner writes by
hand. Projects do not see each other and no tool lists them: a project is a
file, not a column, so a backup, a restore and a corruption are each one
project's business.

**Consumers** are whoever downloads rules: chats *and* skills. A skill acts, and
what acts is under rules. A person is not a consumer — a rule that binds a
person says so in its body. A consumer's name is ONE WORD, because that name is
quoted by hand in chat instructions and skill files, and a space is the mistake
nobody sees.

**The audience is MIXED, and declared rather than deduced.** `reach` is `all` —
everybody, no audience rows at all — or `targeted`, and then the audience is the
**groups** UNION the **exceptions**: single consumers standing next to the
groups, only ever adding. A rule that says `targeted` and names nobody is
refused by a trigger, and so is one that says `all` and names somebody.

**The reading order is the breadth of the door you came through.** Universal
first, then groups from the widest, then what was aimed at you by name — and
because breadth is a `COUNT` of live members computed now, the order stays right
by itself when a consumer appears or a group empties out.

**Widening binds somebody new, so it is promulgation**, not an edit: `rules_amend`
narrows a perimeter and refuses to widen one. To widen, you propose a supersede
and a person approves it.

**History is a photograph.** Each version records both what was declared and who
was actually reached that day, by name, so changing a group tomorrow cannot
rewrite what was true yesterday.

**Tasks live in the same registry and are modelled as the opposite of a rule**:
no audience, no approval, no expiry. Rules bind; tasks wait.

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
such. The lot page of the administration UI shows the queue whole, each proposal
with its reason, and computes a digest over what it displayed; the action must
hand that digest back, so what gets approved is provably the batch that was
**read** — a proposal arriving in between moves the digest and voids the stale
approval. Approval has not been a tool since v3.0.0: it happens in a browser,
behind the UI's own password, so no secret of that level ever travels in a
conversation. (An ed25519 signature used to ride on top; it left in v2.0.0 — it
was the clumsy way of letting a person in instead of a chat, and the UI solves
that at the root.)

Denial needs no digest: refusing cannot do harm. It does cost a sentence, one
per proposal. The denied row stays, with its reason, and
`rules_list(pending=True)` shows a chat its own refusals — so the same idea
coming back through another chat in three weeks is something you can see,
rather than something the registry can block.

How many proposals may wait at once is `queue_cap`, and it belongs to the
**project**, kept in the project's own database — not to the container, which
serves several. NULL is unlimited, 0 closes the queue, N is N.

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
rules_list(project="<code>", consumer="tax")

  VA-0002  Re-read the sources       reach all        reaches you: everyone
  PE-0001  The method of the four    reach targeted   reaches you: deliberativi
  FI-0003  Estimating the bracket    reach targeted   reaches you: by name
  ...
  38 rules · and your open tasks at the foot
```

`reaches_you` says *why* a rule is in your list — everybody, a group you belong
to, or your own name — which is exactly what you need in order to decide whether
it belongs somewhere else. The same call carries the project's brief, your own,
and the tasks open on your desk: one call, because the alternative was four, and
a chat that must make four calls before it can work gets three of them wrong
once.

## The administration page

Approving a rule is not the same act as writing one, and from v2.1 they no
longer happen in the same place. A chat proposes; a person approves, in a
browser, on the LAN.

The page is served by the same process, on a second port — 9443 by default —
because two processes on one SQLite database do not share the engine's lock.
The home page lists the projects the registry serves, by NAME: the person has
already proved who they are with the password, and a URL may carry a name where
a chat may only carry a code. Everything below it is per project.

The lot page shows the pending batch **whole and side by side**, each proposal
with the reason it was filed: that is where three proposals saying the same
thing become visible as what they are. You tick what goes in, give a reason for
what does not, and type the password **once for the action** — four rules are
not four passwords, and a password typed four times is typed without looking.

A proposal that supersedes a rule says so **before** you decide, with the
victim's ID and its current title: approving it retires that rule in the same
transaction, and whoever approves reads both halves of the move.

The digest covers what you were **looking at**, not what you ticked. If a
proposal arrives while you read, the action comes back refused with the page as
it now is — the same digest contract the MCP tool used to carry.

Beside it, readings that write nothing: the rules in force for a chosen
consumer, exactly as that consumer's chat reads them, brief first; a rule's
detail with its history and the diff between two versions; the renewals and the
expiring queue; and the state of the project. And one page that writes without
touching a rule: **codes**, where a one-time authorisation code is minted.

The password is asked for again on every gesture that WRITES — deciding the lot,
renewing, promoting, minting a code — because a session alone is a browser left
open on the iPad. It is *not* asked again for the backup or the log: a
`VACUUM INTO` changes nothing and the log is a ring in memory, and a password
retyped where it defends nothing only teaches the hand to type it without
looking. One password, from the template, and one hour of inactivity. A restart
of the service invalidates every session, deliberately: the session secret is
generated at boot and stored nowhere.

**What is not here any more, since v4.0.0: the deployment page.** It created
projects, rekeyed them and printed their codes, and all three died with the
declarative registry — a project is now a line in `projects.txt`, written from
Unraid by the person who chooses its codes. What took its place is the codes
page: minting one-time codes is the one thing the design gives to this UI and to
nothing else.

**The MCP surface moved again in v4.0.0 — reconnect the connector and test in a
new conversation.** It went from 32 tools to 16, and the names moved with it:
what a chat needs is `reference_guide`, `project_info`, `rules_list`,
`rules_get`, `rules_propose` and the five `tasks_*` calls, and everything an
administrator does went into six — `project_amend`, `rules_amend`,
`rules_retire`, `project_status`, `rules_export`, `tasks_overview`. A connector
left on the old surface does not degrade: it lists tools that are not there.

## The task log

Rules are what BINDS a consumer. Tasks are what is WAITING for it — a
different thing, and modelled as one: no audience, no approval, no signature,
no expiry. The log exists so that *what is open for me?* is a single call,
and so is *what did I do lately?*, because closing a task costs a written
outcome. It replaces both the per-role changelog and the "pending" section
role memories used to keep.

IDs are `TK-NNNN`, never reused, cited like a rule: `(TK-0012)`. `TK` cannot
be declared as a domain of rules — the registry refuses — because the code
has to mean one thing.

**Anybody may open a task for anybody**, which is how an audit hands each
correction to the role that owns it. `created_by` is mandatory. ⚠ Opening a task
for a **human** notifies nobody: humans call no tools, and their post is seen by
whoever reads the overview or the UI. **Closing costs a sentence**: `tasks_close`
takes an `outcome` that completes it or a `reason` that drops it, exactly one of
the two, and the refusal is in the schema as well as at the door. **Closed is
closed** — an open task is amended freely, its owner included, and a closed one
not at all.

**`urgent` belongs to whoever created the task** and cannot be changed by
anyone afterwards, because the receiver is the party with an interest in
clearing it. There are no levels; the guard against inflation is that
`tasks_overview` counts urgent tasks by CREATOR.

**Tasks do not expire.** One open past thirty days comes back marked, and
that is all: an automatic expiry would be a drop with no reason, written by
the clock. Lists are the short form and the server orders them — urgent
first, then oldest first — so when a ceiling bites the cut falls on the
fresh work and never on what has been waiting. Truncation is always
declared, with the real total.

## Installing

Built for Unraid with the Tailscale plugin, but it is an ordinary container: a
mount for the databases, one for state, and environment variables.

1. **A GitHub OAuth application of its own.** Homepage `BASE_URL`, callback
   `BASE_URL/auth/callback`. Do not recycle another service's, or the two will
   fight over the callback.
2. **`JWT_SIGNING_KEY`**: `openssl rand -hex 32`. Stable forever — change it and
   every issued token dies.
3. **`WEB_UI_PASSWORD`**, twelve characters or more. It opens the page that
   promulgates rules, there is no second account and no recovery.
4. **The database directory must be local storage**, never a network share:
   SQLite in WAL needs real file locking.

The template in this repository **is** the configuration, and its field
descriptions are the real documentation of the deploy. Point Unraid at it, fill
the fields, Apply.

**Then declare a project.** The service serves nothing until you do. In the
database directory the first boot writes `projects.txt`, root-only, with the
instructions inside it; you add one line per project:

    Financial Portfolio | <reference code> | <admin code>

Name, reference code, admin code. The two codes are placeholders there rather
than plausible digits on purpose, and it is the same decision that keeps an
example row out of the template inside `projects.txt`: a line with believable
codes in it is a line somebody copies. Both codes are 8 to 32 letters and digits, you
generate them (`openssl rand -hex 12`), and no code may appear twice in the file
— the same code on two projects, or a reference code equal to its own admin
code, is refused by name and line number. The name is a **folder** next to the
file, in that spelling, holding the project's `.db`; renaming a project means
editing the line *and* renaming the folder. A line with no database creates one,
empty and current, and says so in the log; a database with no line is not
served. The file is re-read whenever its mtime changes, so adding a project
needs no restart, and a file that will not parse stops everything with the
offending line quoted rather than serving half a truth.

The reference code goes at the top of that project's chat instructions; the
admin code goes to whoever administers it, and nowhere else.

**Updating from 3.x is not a migration.** There is none, by design: a database
of a different schema generation is refused at boot, naming the file and the two
numbers, never silently upgraded. The v4 registry starts empty.

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
- **Three credentials, and the scale is flat.** The **reference code** opens
  every read of a project and lets a chat file a proposal — a proposal reaches
  nobody until a person approves it, so it cannot do harm, and asking a working
  chat for anything stronger would put that stronger thing in every chat.
  The **admin code** creates: a domain, a consumer, a group. Modifying anything
  that already exists — a perimeter, a retirement, a rename, a brief, a group's
  membership — takes the admin code **plus a one-time code**, minted on that
  project's page in the browser, shown once, burned inside the transaction of
  the gesture that succeeded. A refusal rolls back and does not consume it;
  alone it elevates nobody. The role does not elevate: the key elevates.
- **Two manuals, in two files.** `reference_guide()` bare serves the consumer's
  half; the administration half needs the project code *and* the admin code.
  They are two files rather than one text cut at a marker, so "the admin manual
  served without a key" is not a failure to test for — it is one that cannot
  happen.
- **The registry file is the safe.** `projects.txt` holds every reference and
  admin code in clear, which is the decision, and it is the one file here that
  is root-only, 0600. The mode is re-imposed at every re-read, not only at
  creation: it is edited from a share, and an editor that writes a new file and
  renames it over the old one brings its own mode with it.
- **A malformed call does not print what it carried.** FastMCP validates
  arguments before any tool runs and logs what it rejected, with the arguments
  in the line — a record that obeys no LOG_LEVEL of ours and leaves no
  `refused` line, so a clean log is no evidence it did not happen. Here those
  arguments are the project's codes. From v2.1.1 the payload is redacted and the
  diagnosis is not: the tool, the parameter and the rule that was broken all
  survive.
- **The process runs as root and the database is 0644.** This is the opposite of
  the vault twin, deliberately: from the share you read and you do not touch,
  because a write by hand would bypass the triggers and break history in
  silence.
- **Project codes are not a security boundary.** They are opaque so projects
  cannot stumble into each other; no tool lists them and no error names one, and
  a wrong code answers exactly like a missing one. The real boundary is the
  OAuth gate in front.

## Testing

Five suites. No network, no FastMCP, no Docker.

```
python3 test_schema.py      # the DDL: triggers, constraints, generation
python3 test_registry.py    # projects.txt, the router, the refusals it raises
python3 test_collaudo.py    # the engine, refusals included
python3 test_surface.py     # the seam, the image, the template
python3 test_crash.py       # SIGKILL mid-transaction, as Docker does
```

Each suite prints its own count, and no file repeats it. A number written down
in two places is two numbers, and this project has already paid for that once.

`test_surface.py` reads the source rather than running it: every call into the
engine must exist with a compatible signature, every tool that writes must pass
the gate it claims, no docstring may name a tool that does not exist, and every
variable the template declares must have a reader in the code — that last one
because four dead knobs survived three grains in a form a person fills in with
care.

## The icon, and where it is actually seen

`codifier-icon.png` is pointed at by its raw GitHub URL from two files: the
Unraid template, which puts it on the container, and `server.py`, which passes
it to FastMCP as `icons=[…]`. A check compares the two URLs, because two hand
copies of one string have an expiry date.

Passing `icons` buys **the OAuth consent page** — the page seen when the
connector is added or reconnected — where FastMCP renders it in place of its
own logo.

It does **not** buy the icon in Claude's connector list. That surface ignores
`serverInfo.icons`, which the MCP spec has carried since revision `2025-11-25`
(SEP-973); serving `/favicon.ico` and a root page with `<link rel="icon">` are
ignored as well. The tracking issue is
[anthropics/claude-ai-mcp#152](https://github.com/anthropics/claude-ai-mcp/issues/152).
Under a Tailscale Funnel the list shows Tailscale's icon, which is consistent
with that surface deriving the icon from the DOMAIN — nothing in this
repository can reach it. The field is sent anyway: the day the client reads it,
the list follows with no change here.

## Sibling

[archivist-mcp](https://github.com/alcor6502/archivist-mcp) — a document vault
with per-dataset git versioning. Same architecture, same OAuth gate, same
blocking preflight. That one keeps files; this one keeps rules.

## Licence

MIT.
