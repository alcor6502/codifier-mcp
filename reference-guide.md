# Codifier MCP — manual

## THE MODEL

This is a **registry of rules**, and it exists to make one question a query:
*this chat, right now, which rules is it under?*

**One database, N projects.** A project is a column, not a table, so `VA-02` of
one project and `VA-02` of another coexist with separate histories. You address
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
  note about it.
- **`rules_batch`** returns the pending proposals and a **digest**.
  **`rules_approve`** verifies an ed25519 signature over that digest. You sign
  the batch, never the single rule: at the twelfth signature in a row a person
  signs without reading, and three proposals that say the same thing only
  become visible side by side.
- **`rules_deny`** needs no signature — refusing cannot do harm. The row stays
  and the ID is burnt, so the same idea cannot come back through another chat
  in three weeks. The reason turns silence into an answer.
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
- **Do not re-propose something that was denied.** The registry refuses it and
  tells you the date and the reason. If circumstances really changed, say so to
  Alfredo — do not try another door.
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
