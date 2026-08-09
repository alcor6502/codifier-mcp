# Codifier MCP — manual

## THE MODEL

This is a **registry of rules**, and it exists to make one question a query:
*this chat, right now, which rules is it under?*

**One database, N projects.** A project is a column, not a table, so `VA-0002` of
one project and `VA-0002` of another coexist with separate histories. You address
a project by an opaque **CODE**, never by its name — the code sits at the top of
that project's instructions. No tool lists projects, and no error names one: a
missing code and a wrong code give the identical answer.

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

Each rule carries `via` — which scope it arrives through. When a rule appears in
your list and you cannot see why, `via` is the answer, and it is exactly what
the Architect needs to decide whether it belongs somewhere else.

## THE NUMBER IS NOT YOURS TO PICK

`rules_propose` has **no `id` parameter**. You give the **domain** — two
uppercase letters the project has declared — and the registry hands back the
next number in it, four digits: `VA-0001` for the first of its domain. The
assigned ID is in the verdict, and it is what other rules must cite.

A number is not a choice, it is a position in a sequence. Whoever does not pass
it cannot pick it, which is why there is no rule anywhere telling you not to.

Four digits because IDs are **never reused**: a domain that retires and rewrites
burns numbers even while only twenty are alive. Older text may still say
`VA-02`; wherever an ID is read it is padded, so `VA-02` and `VA-0002` are the
same rule.

If a rule had an identifier in the old Markdown, pass it as `legacy_id`. It is
recorded next to the new one so the citations can be mapped afterwards, and no
two rules may claim the same one. It is **read only**: there is no way to fetch
a rule by it, because one thing must have one name.

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

A rule filed before this format existed keeps its old text, and that is
deliberate: nobody rewrites prose by pattern. Such a rule can still be renamed,
retyped and retired — `rules_fix` only checks a body you actually change. What
is already stored is reported by `rules_check`, which is a report and not a door
slammed on unrelated work.

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
4. **A new rule reaches nobody until its batch is approved**, and the approval
   is signed. Which is why proposing needs no maintenance code.
5. **An approved rule is PROVISIONAL and expires.** Staying costs a decision,
   going is free.

Rule five is the load-bearing one. Rules did not pile up — 63 to 172 — because
somebody wrote them without permission. They piled up because adding costs a
call and removing costs a decision nobody takes. Expiry inverts that asymmetry;
authorisation alone never would have.

## THE LIFE OF A RULE

    proposed ──(signed batch)──> active + provisional ──(signature)──> permanent
        │                              │
        │                              └──> retired
        └──> denied  (with a reason, and the row STAYS)

- **`rules_propose`** files it. It needs only the project code: a proposal
  reaches nobody, so it cannot do harm — and it means you can stop keeping a
  note about it. It takes the **domain**, not the number, and gives the number
  back.
- **`rules_batch`** returns the pending proposals and a **digest**.
  **`rules_approve`** verifies an ed25519 signature over that digest. You sign
  the batch, never the single rule: at the twelfth signature in a row a person
  signs without reading, and three proposals that say the same thing only
  become visible side by side.
- **`rules_deny`** needs no signature — refusing cannot do harm. The row stays
  and the ID is burnt. The reason turns silence into an answer, and it is what
  `rules_pending` shows you: since the counter assigns the number, the registry
  can no longer recognise a re-proposal by its ID. Reading your own refusals is
  now a habit, not a guard rail.
- **`rules_renew`** and **`rules_promote`** are signed, because keeping a rule
  alive is letting it in again.
- **`rules_pending`** is your noticeboard: yours waiting, yours denied with the
  reason, yours expiring within 30 days.

The private key never enters a conversation. The registry holds only the public
half: even with the `.db` in hand, nobody can manufacture an approval.

## WHICH TOOL

| You want to | Use | Admin code? |
|---|---|---|
| know what this project contains | `rules_project_info` | no |
| get every rule in force for you | `rules_list` | no |
| read rules by ID, one or many | `rules_get` | no |
| find a phrase in your own rules | `rules_search` | no |
| know what became of your proposal | `rules_pending` | no |
| file a new rule | `rules_propose` | no |
| see the pending batch and its digest | `rules_batch` | yes |
| approve · deny · renew · promote | `rules_approve` · `rules_deny` · `rules_renew` · `rules_promote` | yes |
| fix a defect in place | `rules_fix` | yes |
| make a rule reach one more scope | `rules_widen` | yes |
| stop a rule reaching a scope | `rules_narrow` | yes |
| take a rule out of the lists | `rules_retire` | yes |
| audit a project | `rules_check` | yes |
| see how a rule changed, and why | `rules_history` · `rules_diff` | yes |
| snapshot to Markdown | `rules_export` | yes |
| create a project, consumer, domain, group | `rules_project_create` · `rules_consumers_add` · `rules_domains_add` · `rules_scope_create` | yes |
| change who is in a group | `rules_scope_edit` | yes |
| seed a project from the old Markdown | `rules_import` | yes |
| copy the database off-site | `rules_backup` | yes |

`rules_get` takes a **list**, and asking for the batch at once is what turns a
stumble into an audit: broken citations are worth far more seen together.

## THE THREE ANSWERS OF `rules_get`

    found          the rules, whole
    not_yours      they exist, outside your perimeter — with who holds them
    never_defined  never defined here

`never_defined` means one of two things, and both are worth acting on: a
**broken citation** to report to the Architect, or you are using another
project's code. It never means "it exists somewhere else" — the registry will
not tell you that, by design.

## DO NOT IMPROVISE

Each of these has a right answer that already exists.

- **Do not write yourself a rule.** A chat does not decide its own rules, it
  receives them. The legislator is the Architect; the registry is the code; the
  chat applies. If you spot something worth codifying, `rules_propose` it and
  forget it — `rules_pending` will have the answer when you come back.
- **Do not re-propose something that was denied.** The registry no longer stops
  you — it cannot, now that the number is not yours to choose, so the same text
  filed again simply takes a new one. The refusals are still there, with their
  reasons, in `rules_pending`: read them before proposing. This one is on you.
- **Do not write the gloss inside a citation** by hand. Write `(VA-0002)` and
  nothing else; the title is added on reading, from the rule itself, which is
  precisely why it cannot go out of date. Pasting back a body you read expanded
  is fine — that one is stripped for you.
- **Do not try to cite a rule that is still a proposal.** It is refused, and the
  cure is to wait for it to be approved, not to work around it.
- **Do not fix a superseded DECISION with `rules_fix`.** `rules_fix` is for
  defects: a wrong number, a broken pointer, a sentence that says something
  false — things that never were right. A decision that *was* right and stopped
  being so gets a new rule, and the old one is retired pointing at it. Collapse
  the two and history stops being able to tell you which happened.
- **Do not widen a group to make one rule travel further.** `rules_scope_edit`
  changes the perimeter of *every* rule pointing at that group. For one rule,
  `rules_widen`.
- **Do not rename a consumer.** The database refuses it. A renamed consumer is a
  different consumer, and the rules that reached it need reviewing, not dragging
  along behind a name. Create the new one, retire the old.
- **Do not edit the Markdown export and expect the registry to notice.** The
  export is a derivative; the truth is the database, and it regenerates.
- **Do not guess the admin code.** Ask for it.

## WHEN SOMETHING REFUSES

The errors here are meant to be read: each says what happened *and* what to do.
Three that surprise people:

- *"project not specified"* — for a missing code **and** for a wrong one. The
  message is identical on purpose: one that told them apart would be an oracle.
- *"you read version N, it is at M"* — somebody wrote between your read and your
  write. Re-read, reconcile, retry. Nothing was lost.
- *"that digest is not the current one"* — a proposal arrived after you read the
  batch. Ask for the batch again and re-sign. You cannot sign one batch and have
  another approved.

## THE RULE THAT IS NOT IN HERE

*"You will never write yourself a rule: the registry gives you your rules."*

That one cannot live in the registry, because you would read it only after
already having queried it. It sits in the project instructions, next to the
code, and it stays there. The reason is **sequence**, not importance.
