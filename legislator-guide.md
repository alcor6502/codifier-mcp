# Codifier MCP — the legislator's manual

This page is for whoever decides **what deserves to be a rule**. It is not the
manual for using the registry; that one is `reference_guide`, it is open to
every consumer, and it describes mechanics the code enforces.

Nothing here is enforced. That is the whole difference, and it is why this page
is written as **tests** rather than as principles. A test is applied to a line
and returns an answer. A principle is approved and changes nothing: *"rules
should be few"* is a slogan, and a slogan decides nothing at rule number eighty.

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

---

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

Both things live inside the same document, so the gate is not applied to the
document. It is applied to the **line**:

> **Would it still be true if the procedure changed?**
> Yes → it is a rule. No → it is a step.

    "Read the inbox first, then the aggregator"     dies with the procedure  → step
    "You do not touch the alternatives folder"      survives any rewrite     → rule

The bulk of the volume is in the first category. Expect to throw away most of
what you started with, and do not read that as having done the job badly.

### The second question, for the lines that look like steps and are not

The **boundaries** of a skill — *"does NOT touch 1 - Alternatives/"*, *"does
NOT open the PDFs in 03 Products/"*, *"is NOT investment analysis"* — sit in
the middle of a list of steps and read exactly like one. Do not wave them
through on the strength of the paragraph they are in; ask:

> **Who decided this line: whoever wrote the procedure, or somebody outside
> it?**
> Outside → it is a rule, however much it reads like a step.

That is the same thing gate one asks, from the other end. A boundary imposed
from outside survives a rewrite of the procedure by construction — the author
of the rewrite was not the one who put it there. And it is precisely what an
actor can violate **in good faith while executing**, which is the shape gate
four looks for.

---

## GATE 2 — Is it a rule, or a missing manual?

> **A rule that describes how a tool works is the symptom of a missing manual.**

And the cure is to write the manual, not to codify the tool. The reason is
mechanical, not aesthetic: **a manual travels with the version of the tool.**
`reference_guide` ships inside the image, so it cannot describe a version that
is not running. A rule about how a tool behaves ages on its own, in silence, and
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

---

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
> order of work — `reference_guide` has it: the cited rule is filed and
> approved first.
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

---

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

---

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

### Where to find the reason

`reason` is mandatory on `rules_propose`, and it is the piece the whole scheme
rests on: **without the why, a rule cannot be defended, and at the first
opportunity it is reopened.**

`reason` is immutable: written at the proposal, and no event rewrites it.
What happens to a rule afterwards — approved, denied, renewed, promoted, the
why of a fix or of a retirement — lands in a column of its own, `event`, and
in the history. So the why is readable exactly where the deciding happens:
**`rules_batch` carries it on every proposal**, which is what makes a
signature worth signing, and **`rules_export` carries it on every rule**,
which is what a renewal pass reads. Version 1 of `rules_history` keeps it
too, as it always did. One caveat on a database migrated from before this:
rows whose `reason` an event had already overwritten stay overwritten — the
migration converts nothing, and for those rows version 1 of the history is
still where the truth survives.

Read the why before deciding to renew — the alternative is deciding on the
title, which is how a corpus keeps everything.

> **Could somebody who was not in the room use this reason to decide whether to
> keep the rule?**

*"Because it is right"* is not a reason. *"Because the aggregator reports the
trade date and we anchor on sessions, and we got it wrong twice"* is one. A
reason that records only **that** a decision was taken has thrown away the one
thing renewal has to work with.

### Why the corpus goes back in by hand

`rules_import` exists and it is the wrong door, for reasons that are worth
having in front of you while the temptation is live. It writes one identical
`reason` across every rule in the batch — the field is decorative from birth,
and the first renewal has nothing to decide on. It files them **permanent** by
default, so nothing expires and rule five never starts. And it asks you
nothing: not whether the rule is still needed, not whether another one already
says it, not whether that cross-reference survives gate three.

The passage by hand is not the *price* of the migration. It is its
**content** — it is the only moment at which every line of the corpus gets
looked at once, and that moment does not come round again.

### Fixing versus superseding

`rules_fix` is for **defects**: a wrong number, a broken pointer, a sentence
that says something false — things that were never right.

A decision that **was** right and stopped being so is not a defect. It gets a
new rule, and the old one is retired pointing at it. Collapse the two and the
history can no longer tell you which of the two happened, which is the one thing
the history was for.

> **Was this ever true?**
> No → `rules_fix`. Yes → new rule, retire the old.

---

## WHAT DOES NOT GO IN THE REGISTRY AT ALL

*"You will never write yourself a rule: the registry gives you your rules."*
That one cannot be a rule, because it would be read only after the registry had
already been queried. It lives in the project instructions, with the project
code, and `reference_guide` explains to the reader why it is not in their list.
The reason is **sequence**, not importance.

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

---

## HOW TO TELL THIS PAGE HAS ROTTED

`reference_guide` describes mechanics the code imposes: when the two disagree
the code wins, and it shows immediately. This page describes judgment, which
nothing imposes, so it can go stale in silence. Three things to run, and each
gives an answer:

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
