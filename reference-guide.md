# Codifier MCP — the manual

One manual, two depths. **Part one** is for every consumer — chat or skill —
and ends at a stop line: a working session can stop there. Below the line live
the maintenance tools, which want the code, and the craft of deciding what
deserves to be a rule. One file, one truth.

---

# PART ONE — USING THE REGISTRY

## THE MODEL

This is a **registry of rules**, and it exists to make one question a query:
*this chat, right now, which rules is it under?*

**One database, N projects.** A project is a column, not a table, so `VA-0002` of
one project and `VA-0002` of another coexist with separate histories. You address
a project by an opaque **CODE**, never by its name — the code sits at the top of
that project's instructions. No tool you can reach without the maintenance code
lists projects, and no error names one: a missing code and a wrong code give the
identical answer.

**Consumers** are whoever downloads rules: chats *and* skills. A skill is not a
chat, but it acts, and what acts is under rules — calling `rules_list` is the
only requirement. **A person is not a consumer**: a rule that binds a person
says so in its body.

**Scopes** are named sets of consumers. There is no separate notion of "group":
a single consumer is a set with one element, and its singleton scope is made by
a database trigger the moment the consumer is born. One kind of pointer, no
branch. `_ALL_` is a scope too, and the only one whose membership is computed:
it must reach consumers that do not exist yet.

**A rule points to a SET of scopes.** Widening it is one more row — the group it
already belonged to is not touched, because that group has other tenants who
have nothing to do with this rule.

## THE ORDER IS THE BREADTH

`rules_list` hands back everything in force for you, in one call, ordered from
the most widespread to the most specific: first what binds everyone, then your
group's, last your own.

That order is not a convention somebody maintains. It is the **cardinality of
the scope** a rule reaches you through, so it stays right by itself: the day a
fifth consumer is born, `_ALL_` widens and stays on top with nobody touching
anything.

Read it that way too. The first lines are the foundations, the last are the
particulars, and the position tells you the weight.

The answer LEADS with your **brief** — your mandate, who you are — before the
rules: identity and law in one round trip. A consumer without a brief gets an
empty field, not an error; skills leave it empty on purpose. The brief is
written by maintenance (through `rules_consumers_add`) and versioned by the
database like everything else here: a silent change to a role's identity is
exactly the class of change this registry exists to record.

Next to the brief rides the **legend of the domains present** in your list,
each two-letter domain with its gloss (`VA — vault and files · VE —
verification`): two letters age badly in human memory, and the glosses
already live in the project's declarations — surfaced, not new state. The
same legend leads `rules_export`, wherever IDs are listed in bulk.

Then the rules, in the CONSUMER reading: each rule as
**the ID and the body, and nothing else** — the citations expanded.
The title, the dates, the perimeter and
the why are administration; they live in the maintenance reading
(`rules_batch`, `rules_export`), because this registry exists to spend less
context, not more. When a rule appears in your list and you cannot see why,
the answer — `via`, which scope it arrives through — sits in the maintenance
reading too, which is where that question gets decided: whether the rule
belongs somewhere else is the Architect's call.

## THE NUMBER IS NOT YOURS TO PICK

`rules_propose` has **no `id` parameter**. You give the **domain** — two
uppercase letters the project has declared — and the registry hands back the
next number in it, four digits: `VA-0001` for the first of its domain. The
assigned ID is in the verdict, and it is what other rules must cite.

Four digits because IDs are **never reused**: a domain that retires and rewrites
burns numbers even while only twenty are alive. Older text may still say
`VA-02`; wherever an ID is read it is padded, so `VA-02` and `VA-0002` are the
same rule.

The identifiers of the old Markdown do not enter the registry at all: the
old->new mapping is kept in the migration files, outside, because one thing
must have one name and a relic conserved in the clean system is how the old
corpus grows back.

## CITATIONS: `(VA-0002)`

A citation is the ID **alone** inside round brackets. An ordinary parenthesis is
ordinary prose — what makes a token a citation is the **shape** `XX-NNNN`, not
the bracket — so `(see the note below)` is prose, `(VA-0002)` is a citation, and
the vault's own `[[wiki links]]` stay free for whatever you may want them for.

Alone means alone: `(see VA-0002)` is refused, because there the brackets hold a
sentence and not a pointer. Write *"see (VA-0002)"*.

`rules_propose` and `rules_fix` check citations, and refuse four things:

- a bare `VA-0002` left **outside** a bracket of its own. That is a forgotten
  bracket, and a typo must not be able to turn into a citation nobody sees. Case
  does not save you: `va-0002` and `Va-0002` are the same mistake;
- a citation that does not **resolve** — so a pointer cannot be invented;
- a citation towards a rule that is **not approved yet**, whether it is still a
  proposal or was denied;
- **anything of your own inside the brackets.** `(VA-0002 — a note of mine)` is
  refused. The only text allowed there is the title the registry itself put
  there when you read it, because what is inside the brackets is not stored —
  and a registry that quietly dropped your words would be worse than one that
  refuses them.

Only the domains **this project has declared** are hunted, so a ticket number,
a locale in a URL or a standard like `ISO-9001` are prose and stay prose. Within
those domains there is **no exception**, not even inside backticks: a forgotten
bracket is always around a domain that exists, which is exactly the case worth
catching.

### You may only cite a rule that is already approved

This is the one that shapes how you work, so it is worth the paragraph.

Citing something still in the batch looks convenient and is a trap: **the number
of a proposal is not final until it is in**, so a batch whose members cite each
other can be approved into a state where the pointers were right only while they
were being written. The registry refuses it.

The order of work is therefore forced, and it is simple:

1. file the rule that will be cited;
2. have it approved — the ID it comes back with is final;
3. file the rule that cites it.

A rule that needs one which does not exist yet **waits**. That is not a delay to
work around, it is the shape of the job.

### Reading expands them

On the way back, every citation arrives **expanded** with the current title of
the rule it points at:

    (AL-0004)  →  (AL-0004 — alternative shares are not sold at a loss)

You understand the reference without a second call, and a pointer to a retired
rule arrives marked as such. The gloss is **generated, never stored**: what goes
into the database is the bare pointer, which is exactly why it cannot go stale.
So you may paste an expanded body straight back into `rules_fix` — the title is
stripped on the way in, and pasting back what you read counts as no change at
all.

## THE FIVE RULES OF THIS REGISTRY

1. **An ID is a pointer and is never reused** — not by a retired rule, not by a
   denied one. Citations must keep resolving forever.
2. **Nothing is deleted.** A rule is retired: it leaves the lists, the row
   stays, and it still resolves by ID.
3. **History is written by the database TRIGGERS, not by the tools.** A change
   made by hand with `sqlite3` is in there too. Whole versions are kept, not
   diffs.
4. **A new rule reaches nobody until its batch is approved.** Which is why
   proposing needs no maintenance code, and why you can stop keeping a note
   about a proposal you filed: `rules_pending` has the answer.
5. **An approved rule is PROVISIONAL and expires.** Staying costs a decision,
   going is free.

What rule five does to you in practice: a provisional rule carries an expiry
date, `rules_pending` shows you yours from thirty days out, and on the day it
passes the rule leaves the lists on its own. Renewal puts it back for another
term and promotion makes it permanent — both are maintenance decisions. Nobody
has to do anything for a rule to go; somebody has to decide for it to stay.

## PROPOSING

`rules_propose` files a proposal, and needs no maintenance code: a proposal
reaches nobody, so it cannot do harm. It takes the **domain**, not the number,
and gives the number back. Seven things are required: `domain`, `type` (`R`
binding, `M` method, `F` technical fact), `title`, `body`, `scopes`, `reason`
— one sentence saying why the rule should exist, which is what somebody will
have to decide on when it comes up for renewal — and `proposed_by`, your own
consumer name: it is what makes the proposal YOURS. Omitted, the proposal
would be an orphan `rules_pending` could never show you, so the door refuses
it.

The project holds a **limited number of pending proposals** — a deployment
knob, default 5. Whoever approves reads in small batches, and that rhythm is
enforced here: the refusal says the ceiling and lists what is in the queue.
Approval and denial free the slots by themselves; there is no override.

`supersedes` names the rule this proposal REPLACES — a dedicated field, never
a citation in the body. The target must be **in force**, and only one pending
proposal may claim it. At approval the swap is one transaction: the heir goes
active and the named rule is retired pointing at it — there is no window with
both in force, and no third step anybody can forget. Declare the heir's
scopes yourself: the supersede is the moment the perimeter gets re-decided,
not inherited. In reading, the retired rule expands pointing forward:
`(VA-0002 — its title · retired → superseded by VA-0009)`.

`rules_pending` is your noticeboard: yours waiting, yours denied with the
reason why, and your rules expiring within thirty days. A denial burns the ID
and keeps the reason; since the counter assigns the numbers, the registry
cannot recognise a re-proposal — reading your own refusals before proposing is
on you, and on nobody else.

## THE THREE ANSWERS OF `rules_get`

    found          the ID and the expanded body. A rule NOT in force says so —
                   retired, denied, expired — because a body handed back as if
                   it bound you would be a lie by omission
    not_yours      they exist, outside your perimeter — with who holds them
    never_defined  never defined here

`never_defined` means one of two things, and both are worth acting on: a
**broken citation** to report to the Architect, or you are using another
project's code. It never means "it exists somewhere else" — the registry will
not tell you that, by design.

`rules_get` takes a **list**, and asking for the batch at once is what turns a
stumble into an audit: broken citations are worth far more seen together.

## THE TOOLS THAT NEED NO CODE

`rules_project_info` (what the project contains — also the proof the registry
answers) · `rules_list` · `rules_get` · `rules_search` · `rules_pending` ·
`rules_propose` · `reference_guide`, which is this page.

## THE CEILINGS, AND THE WAY PAST EACH

| Ceiling | | Way past |
|---|---|---|
| IDs per `rules_get` | 50 | ask in batches; the answer is a dict you can merge |
| body of one rule | 64000 bytes | it is two rules: split it |
| numbers in one domain | 9999 | a domain that burns these needs splitting, not widening |

A ceiling refuses before it writes anything, and says which one it was. None of
them truncates: there is no call here that silently gives you part of an answer.
There is no bulk import: a corpus is seeded by hand, one rule at a time,
through the same propose/approve door as any other rule.

The expiry term is not a ceiling and is not written here: it is set by the
deployment, and `rules_project_info` reports it in `approval.provisional_days`.
The date for one rule is its own `expires_at`, which comes back with the rule;
`rules_pending` is the shortcut for the ones inside thirty days.

## DO NOT IMPROVISE

Each of these has a right answer that already exists.

- **Do not write yourself a rule.** A chat does not decide its own rules, it
  receives them. The legislator is the Architect; the registry is the code; the
  chat applies. If you spot something worth codifying, `rules_propose` it and
  forget it — `rules_pending` will have the answer when you come back.
- **Do not re-propose something that was denied.** Nothing stops you: the
  number is not yours to choose, so the same text filed again simply takes a
  new one and the registry cannot recognise it. The refusals are there, with
  their reasons, in `rules_pending`.
- **Do not write the gloss inside a citation** by hand. Write `(VA-0002)` and
  nothing else; the title is added on reading, from the rule itself, which is
  precisely why it cannot go out of date. Pasting back a body you read expanded
  is fine — that one is stripped for you.
- **Do not try to cite a rule that is still a proposal.** It is refused, and the
  cure is to wait for it to be approved, not to work around it.
- **Do not guess the admin code.** Ask for it.

## WHEN SOMETHING REFUSES

The errors here are meant to be read: each says what happened *and* what to do.
Two that surprise people:

- *"project not specified"* — for a missing code **and** for a wrong one. The
  message is identical on purpose: one that told them apart would be an oracle.
- *"VA-0002 is at version 3, you read 2: someone wrote in the meantime"* —
  exactly that: somebody wrote between your read and your write. Re-read,
  reconcile, retry.

## THE RULE THAT IS NOT IN HERE

*"You will never write yourself a rule: the registry gives you your rules."*

That one cannot live in the registry, because you would read it only after
already having queried it. It sits in the project instructions, next to the
code, and it stays there. The reason is **sequence**, not importance.

---

> **⛔ STOP — everything below requires the maintenance code. A consumer can
> stop here.**

---

# PART TWO — MAINTENANCE

The maintenance code travels **on every call**: there is no session and no
"unlocked" state anybody can leave open. What it opens is everything below.

## THE LIFE OF A RULE

    proposed ──(approved batch)──> active + provisional ──(promotion)──> permanent
        │                              │
        │                              └──> retired
        └──> denied  (with a reason, and the row STAYS)

**`rules_batch`** returns the pending proposals — each with its `reason` — and
a **digest** over the whole. **`rules_approve`** demands that digest back: it
proves the approval covers the batch that was **read**, not the batch that
exists now. If a proposal arrives in between, the digest moves and the stale
one is refused — *"that digest is not the current one"* means exactly that:
ask for the batch again and re-read it. You approve the batch, never the
single rule: seen side by side, three proposals that say the same thing become
visible as what they are.

**`rules_deny`** refuses one or more proposals, with a reason — no digest,
because refusing cannot do harm. The row stays, the ID is burnt, and
`rules_pending` shows the refusal to whoever filed it.

**`rules_renew`** pushes the expiry forward: keeping a rule alive is letting it
in again, which is why renewal is where the corpus is governed. Its verdict
hands back each rule's ORIGINAL reason, and `rules_pending` carries the same
reason on the expiring list — the renewal question is undecidable without the
why in front of you, and the tool puts it there instead of prescribing a
habit. **`rules_promote`** makes a rule permanent — rare and deliberate: a
permanent rule is one you promise to notice when it goes stale, because
nothing else will notice for you.

## WHERE THE WHY LIVES

`reason` is immutable: written at the proposal, and no event rewrites it.
What happens to a rule afterwards — approved, denied, renewed, promoted, the
why of a fix or of a retirement — lands in a column of its own, `event`, and
in the history. So the why is readable exactly where the deciding happens:
**`rules_batch` carries it on every proposal**, which is what makes an
approval worth deciding, and **`rules_export` carries it on every rule**,
which is what a renewal pass reads. Version 1 of `rules_history` keeps it
too, as it always did. One caveat on a database migrated from before this:
rows whose `reason` an event had already overwritten stay overwritten — the
migration converts nothing, and for those rows version 1 of the history is
still where the truth survives.

## CHANGING THE CORPUS

- **`rules_fix`** — defects only: a wrong number, a broken pointer, a sentence
  that says something false — things that were never right. Same ID, the rule
  stays in force, a new version is born. `expected_version` is the number you
  read: if somebody wrote in the meantime the change is refused and you are
  told the current version. **It checks the body only if you pass one.** Read
  that literally: what is exempt is the field you leave out, not the text that
  happens to be unchanged. A rule written before the citation format existed
  can still be renamed or retyped — omit `body` and nothing about it is
  checked; what is already stored is reported by `rules_check`, which is a
  report and not a door slammed on unrelated work.
- **A decision that WAS right and stopped being so is not a defect.** Propose
  the new rule with `supersedes` naming the old one: at approval the swap is
  one transaction, heir active and victim retired pointing forward. Collapse
  fix and supersede into one gesture and the history can no longer tell you
  which happened — the one thing it was for. (`rules_retire` with
  `superseded_by` remains the manual path for a retirement decided after the
  heir was already in.)
- **`rules_widen` / `rules_narrow`** — one rule, one more/one fewer scope.
  **`rules_scope_edit`** changes the perimeter of *every* rule pointing at that
  group: use it only when the group itself is wrong. A rule narrowed to no
  scope at all is not retired, it is invisible — the verdict warns, and the
  audit lists it.
- **Do not rename a consumer** — the database refuses it. A renamed consumer is
  a different consumer: create the new one, retire the old, review what
  reached it.
- **`rules_retire`** — out of the lists, row stays, ID resolves forever. The
  verdict lists the active rules that still cite the retired one: those need
  fixing, and `rules_check` keeps listing them until they are.

## WATCHING THE REGISTRY

- **`rules_check`** — broken pointers, citations from in-force rules towards
  retired, denied or still-proposed ones (the door can only judge a citation
  the day it is written), rules without a perimeter, redundancy candidates.
  The candidates are a suspicion, not a verdict: two rules in force, same
  perimeter, same citations — deciding they say the same thing stays yours.
- **`rules_status`** — database integrity, counts by domain and consumer,
  expired-not-retired, batches approved. Two paths to the same numbers, so a
  disagreement is a defect.
- **`rules_history` / `rules_diff`** — how one rule changed, and why. Whole
  versions are kept, so the comparison is computed between any two.
- **`rules_export`** — the Markdown snapshot, reasons included. A DERIVATIVE:
  editing it changes nothing, the truth is the database, and it regenerates.
  With a consumer it is that perimeter, widest first; without, the whole
  project, retired rules included.
- **`rules_registry`** — every project, codes included: the only door codes
  come out of. **`rules_project_create` · `rules_project_rekey` ·
  `rules_consumers_add` · `rules_domains_add` · `rules_scope_create`** —
  creation and custody. Consumers and domains are data: a new project does not
  need a new container. A consumer may be born WITH its `brief` — its
  mandate, returned at the head of its `rules_list` — and on a consumer that
  already exists, `rules_consumers_add` with a brief updates it: that is the
  door briefs are written through. Versioned by trigger, hand edits included;
  a mandate is not a rule — it is not violable and not shared, and modelling
  it as one would fatten the corpus the expiry mechanism keeps small.
- **`rules_backup`** — a quiescent copy of the whole database (`VACUUM INTO`):
  it opens without recovery, and it is the one to take off-site. In WAL the
  database is three files, so copying one by hand is a corrupt backup.

---

# PART THREE — THE LEGISLATOR'S CRAFT

This part is for whoever decides **what deserves to be a rule**. Nothing here
is enforced — that is the whole difference, and it is why it is written as
**tests** rather than as principles. A test is applied to a line and returns
an answer. A principle is approved and changes nothing: *"rules should be
few"* is a slogan, and a slogan decides nothing at rule number eighty.

**How to use it.** You have one candidate line in front of you — a sentence out
of a skill, an instruction out of a project brief, something you were about to
write down. Put it through the four gates below, in order. Most candidates die
at gate one, and that is the point: the gates exist to keep things **out**.

**Keep the tally as you go.** For every candidate you reject, write down which
gate killed it. Two things come out of that list and out of nothing else: a
gate that has stopped rejecting anything is decoration and can go, and a gate
that kills half of everything is telling you where the corpus was coming from.
Nothing in the registry records a candidate that never became a rule, so if you
do not write it down it is not anywhere.

## GATE 1 — Is it a rule, or a step?

> **A procedure is read because you are already doing that thing. A rule has to
> be able to reach you even when you do not know it exists.**

A procedure does not need to be *discovered*: you are inside the task and the
text is in front of you. A rule does, by definition — if you already knew it
applied, codifying it was unnecessary.

From which: **if a text is only ever read by the actor already performing the
task that text describes, the registry adds one read and removes nothing.** That
is pure friction. The limit case is a skill: a skill *is* a written procedure,
and putting its steps in the registry means it has to call `rules_list` to learn
how to be itself.

### The test, applied line by line

> **Would it still be true if the procedure changed?**
> Yes → it is a rule. No → it is a step.

    "Read the inbox first, then the aggregator"     dies with the procedure  → step
    "You do not touch the alternatives folder"      survives any rewrite     → rule

The bulk of the volume is in the first category. Expect to throw away most of
what you started with, and do not read that as having done the job badly.

### The second question, for the lines that look like steps and are not

The **boundaries** of a skill — *"does NOT touch 1 - Alternatives/"*, *"is NOT
investment analysis"* — sit in the middle of a list of steps and read exactly
like one. Do not wave them through on the strength of the paragraph they are
in; ask:

> **Who decided this line: whoever wrote the procedure, or somebody outside
> it?**
> Outside → it is a rule, however much it reads like a step.

That is the same thing gate one asks, from the other end. A boundary imposed
from outside survives a rewrite of the procedure by construction — the author
of the rewrite was not the one who put it there. And it is precisely what an
actor can violate **in good faith while executing**, which is the shape gate
four looks for.

## GATE 2 — Is it a rule, or a missing manual?

> **A rule that describes how a tool works is the symptom of a missing manual.**

And the cure is to write the manual, not to codify the tool. The reason is
mechanical, not aesthetic: **a manual travels with the version of the tool.**
This page ships inside the image, so it cannot describe a version that is not
running. A rule about how a tool behaves ages on its own, in silence, and
nothing ever tells it that the tool moved.

### The test

> **Does this sentence stop being true the day the tool ships a new version?**
> Yes → it belongs in that tool's manual, not here.

### The second test, and it is the cheaper one

> **Try to violate it. Does the tool refuse on its own?**
> Yes → do not write it. The rule is redundant the day it is filed.

This registry has a narrow, rigid surface, which makes the test easy to run:
you cannot pick an ID, you cannot cite a proposal, you cannot leave a bare ID
outside its brackets, you cannot reuse a number. **Every structural constraint
of the surface is a rule that does not have to exist.** Where the tool refuses
by itself, a rule saying the same thing only adds a line everybody reads and
nobody needs.

When you find a cluster of rules that all describe one tool, do not rewrite the
cluster. Go and see whether that tool has a manual, and write it if it has not:
what those rules were compensating for is the gap, and the gap is what you can
actually close.

### What survives this gate

*"Do not abuse the vault"* survives, and the reason names the boundary exactly:
it does not describe a mechanism. It constrains a **judgment**, and no manual
takes a judgment away.

## GATE 3 — Is it a rule, or a reminder?

> **A recall is not a norm. It is evidence that something else is not
> arriving.**

The corpus this registry is replacing was measured before the work started:
roughly **40% of it existed to govern the rules themselves** — recalls,
pointers, instructions about reading the instructions. That is not bureaucracy.
It is an **immune reaction** to a corpus grown too large to hold in mind, and
it disappears on its own once there is nothing left for it to defend.

Which means: a recall is a **diagnosis to run**, never a rule to file.

### The test

> **Delete the reference. Does the rule stop saying what it has to say?**
> Yes → the reference is load-bearing. Keep it, cite properly, and mind the
> order of work — part one has it: the cited rule is filed and approved first.
> No → it was a reminder. Do not file it. Ask why the first rule is not
> arriving.

### The three answers, and each has a tool

- **It is not reaching that consumer.** The scope is wrong, not the corpus.
  `rules_widen` — one rule, one more row.
- **It reaches them and is not understood.** The body is the defect.
  `rules_fix`, and the fix is the sentence, not a second rule reinforcing the
  first.
- **It reaches them, is understood, and is drowned.** Then the corpus is the
  defect. `rules_retire` on what nobody needs any more, until the ones that
  matter can be seen. This is the answer nobody wants and it is usually the
  right one.

Filing the recall instead does all the damage at once: it adds a line to every
reading of the corpus, it leaves the original defect in place, and it makes the
corpus fractionally harder to hold — which is the condition that produced the
recall.

## GATE 4 — Who could violate it?

A rule goes to whoever **could break it while doing their own job in good
faith**. Not to whoever might find it interesting, and not to everybody because
it seems important.

### The test

> **Name the actor who could violate this line without noticing. That set is
> the scope.**
> If you cannot name one, the line is not a rule for anybody. It may be a fact,
> a preference, or a piece of a manual.

`_ALL_` is not "important": it is the answer when the actor is *anybody,
including the ones that do not exist yet*. Reserve it for that and the reading
order in `rules_list` keeps meaning something — breadth first, particulars last.

### When somebody has to know it but cannot break it

That happens, and there is no second kind of link in the registry to express
it: `rules_widen` is the only pointer there is, and using it makes a reader
look, from the inside, exactly like somebody bound.

> **If this consumer ignores the line, does anything go wrong that somebody
> else would not already catch?**
> Yes → it belongs in the scope.
> No → keep it out of the scope, and say in the **body** who else the rule
> concerns. A rule is allowed to name an audience it does not bind.

When you cannot keep somebody out without breaking something, write down the
rule ID, the consumer and the date. That list — not an estimate, not a
recollection — is the only thing that could ever justify a second kind of link,
and it costs one line while you are already there.

## THE SECOND JOB — the corpus you already have

Getting a rule in is the small half. The corpus is kept alive by what leaves it.

### Why norms expire and procedures do not

**A procedure verifies itself, because it gets executed.** A defect in it shows
up at use and gets mended, like code — and code does not expire. A rule is
never "executed" by anything. It sits there, and there is no moment at which
the world tells it that it has stopped being useful.

Then the asymmetry that makes it structural:

> **A dead rule is paid for by whoever reads it. A dead procedure is read by
> nobody.**

Rules are read **in bulk and in advance**: `rules_list` hands over all of them
before anyone knows which will be needed. An obsolete rule costs attention to
every consumer, in every session, forever. A procedure is read **on demand**: if
it is obsolete because nobody does that task any more, it opens no file.

That is a test too, and it is the one to run when gate one leaves you unsure:

> **How many of the consumers that receive this line will read it in a session
> where it does not apply?**
> All of them, every time → it is a norm, and it has to be able to expire.
> Only whoever is doing that one task → it is a procedure. It does not belong
> in the registry at all.

### What is left of the worry, and it is much narrower

**A procedure executed rarely has no continuous test**, because the test *is*
the execution. It does not need an expiry date; it needs running.

> **When was this procedure last executed end to end?**
> You know → nothing to do. You do not know → run it now. Until it has run, it
> is a hypothesis, and what it says about the world is unverified.

### The test at renewal

`rules_renew` is where the corpus is actually governed, and the question there
is not the obvious one.

> **Not: "is it still true?"** — almost everything is still true.
> **But: "would I file this today, for the reason it was filed for?"**

If the answer needs a *new* reason, it is a new rule: file it and retire the old
one pointing at the successor. If the answer is "I would not bother", let it
expire. Doing nothing is a decision here and it is the cheap one, on purpose.

The `reason` is in front of you — `rules_batch` and `rules_export` carry it —
and the test of a good one is:

> **Could somebody who was not in the room use this reason to decide whether to
> keep the rule?**

*"Because it is right"* is not a reason. *"Because the aggregator reports the
trade date and we anchor on sessions, and we got it wrong twice"* is one. A
reason that records only **that** a decision was taken has thrown away the one
thing renewal has to work with.

### Why the corpus goes back in by hand

There is no bulk door, and that is a decision, not a gap. An import writes
one identical `reason` across every rule in the batch — the field is
decorative from birth, and the first renewal has nothing to decide on. It
files everything without asking whether the rule is still needed, whether
another one already says it, whether that cross-reference survives gate
three. The engine used to have one; it was removed rather than guarded.

The passage by hand is not the *price* of the migration. It is its
**content** — it is the only moment at which every line of the corpus gets
looked at once, and that moment does not come round again.

### Fixing versus superseding

`rules_fix` is for **defects**: a wrong number, a broken pointer, a sentence
that says something false — things that were never right.

A decision that **was** right and stopped being so is not a defect. It gets a
new rule proposed with `supersedes` naming the old one, and the approval does
both moves in one transaction. Collapse the two and the history can no longer
tell you which of the two happened, which is the one thing the history was
for.

> **Was this ever true?**
> No → `rules_fix`. Yes → new rule with `supersedes`, and approval retires the old.

## WHAT DOES NOT GO IN THE REGISTRY AT ALL

*"You will never write yourself a rule: the registry gives you your rules."*
That one cannot be a rule, because it would be read only after the registry had
already been queried. It lives in the project instructions, with the project
code — part one says the same thing to the consumer reading it. The reason is
**sequence**, not importance.

Which sets the ceiling on those instructions, and this is where it goes wrong:
the temptation is to put a summary of the model up there. **Every line of
summary is a copy of a manual, and copies diverge**, in the one place that holds
everything else up.

> **Does this line in the project instructions state a fact that could become
> false?**
> Yes → it does not belong there. It belongs in a manual, or in a rule that can
> expire.

What passes that test is short: the project **code**, the rule above, and
*"before you write, read the manual"*. Three things that cannot age, because
none of them asserts anything about the world.

## HOW TO TELL THIS PAGE HAS ROTTED

Parts one and two describe mechanics the code imposes: when they disagree with
the code, the code wins, and it shows immediately. This part describes
judgment, which nothing imposes, so it can go stale in silence. Three things to
run, and each gives an answer:

- **Take the last five candidates you rejected and the last five you filed.
  Which gate decided each one?** A gate that decided none of the ten is not
  earning its place — either it has been absorbed by the surface, or it was
  never a test.
- **Take the three rules you renewed most recently and read version 1 of each
  with `rules_history`.** If the reason there could not have carried the
  renewal, the defect is upstream of this page: the reasons are being written
  as formalities.
- **Watch the count, not the content.** `rules_status` gives it. A corpus grows
  in one direction unless somebody decides otherwise, and the direction of
  travel tells you more than any single rule: the count going up between two
  renewal rounds means the second job is not being done.
