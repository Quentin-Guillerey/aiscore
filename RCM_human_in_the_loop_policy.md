# RCM Human-in-the-Loop Policy

## Rule

**`auto_zero` results require human confirmation before the score is final.
`partial_fail` results do not — they can flow through automated.**

That's the whole policy. Everything below is why, and how it plugs into what's
already built.

---

## Why the line sits there, not somewhere else

`auto_zero` and `partial_fail` aren't just two severities of the same thing.

- `partial_fail` caps a score at 50% (or lower, if the base score already was).
  It's a penalty. If the automation gets the category wrong, the damage is a
  score that's off by some amount — reviewable and correctable in a normal QA
  audit, same as any other criterion.
- `auto_zero` doesn't cap the score. It replaces it with zero, full stop,
  regardless of how the rest of the interaction went. That's not "score
  reduced," it's "this interaction failed for a reason unrelated to its
  quality." Categories that carry `auto_zero` — Fraud, Unprofessional_Behavior,
  Chat_Avoidance, Call_Avoidance — are also the ones where a false positive
  costs the agent the most and where a false negative (missing real fraud)
  matters the most. Both failure directions are expensive enough that neither
  side gets to run unattended.

Automating "not critical" work with human review as the error-catching layer
is the right tradeoff when errors are recoverable in the ordinary course of QA.
`auto_zero` isn't that — it needs the check before publication, not after.

### On PCI_Compliance specifically

PCI_Compliance is the highest-stakes category on the list by subject matter,
and it sits outside the gate. That is deliberate, and worth stating plainly
because it looks wrong at first glance.

The gate is not a severity ranking. It does not ask "how bad is this
category." It asks one question: **is this outcome recoverable through normal
QA process if the flag turns out to be wrong?**

- A `partial_fail` caps the score at 50%. If the flag was raised in error,
  the score is wrong by a bounded amount, the interaction still carries its
  full criterion-level detail, and an ordinary audit or dispute corrects it.
- An `auto_zero` discards the base score entirely. There is nothing left to
  inspect. The interaction's actual quality becomes unrecoverable from the
  score, and the agent absorbs a total failure for a reason unrelated to how
  they handled the customer.

PCI violations are severe as *conduct*. They are not severe as *scoring
outcomes*, because the rubric assigns them `partial_fail`, which is
recoverable. Gating them would be gating on how the topic feels rather than
on what the score does.

Two things this reasoning explicitly does not claim:

1. It does not claim PCI matters less. A PCI violation should trigger
   whatever compliance and security escalation the organization requires.
   That path runs alongside QA scoring, not through it. This document governs
   score publication only, and score publication is not the control that
   protects cardholder data.
2. It does not claim the line is permanent. If overturn data later shows PCI
   flags being rejected at a high rate, that is evidence the category or its
   violation definitions are unclear, and it would justify gating PCI on its
   own merits rather than by category severity. The `override_superseded`
   field exists to make that measurable.

---

## What actually happens at each step

**RCM flag selection stays human, unchanged.** Nothing in this policy
automates *detecting* fraud, unprofessional behavior, or the rest — a person
still selects the RCM category and violation, same as today. This policy is
about what happens *after* a category has already been flagged, not about
whether the flag should exist.

**Given a flagged category:**

| Resolved `override_type` | What happens |
|---|---|
| `partial_fail` | Score computed (`min(0.5, base_score)`), published automatically. Logged for normal calibration/audit sampling — no blocking gate. |
| `auto_zero` | Score computed, but held as **pending** — not published. Requires confirm or reject before it becomes final. |
| unresolved / unknown category | Already blocks today — `load_rcm_override_types()` refuses to load if a category has no valid `override_type`. This policy doesn't change that; it's the existing "fail loud, not silent" behavior. |

**On reviewer decision:**
- Confirm → score locks in at 0.0, same as if it had run through automatically.
- Reject → `final_score` reverts to `base_score` and `override_superseded` is
  set to true. **The RCM flag itself stays on the record.** It is not cleared.
  Which categories get overturned, and how often, is the single best signal
  that a category or its violation definitions need work, and clearing the
  flag on reject would delete the only record that it was ever raised. An
  appeals path with no record of rejected flags is not an appeals path.

---

## Where override_type comes from

`override_type` is derived from `rcm_violations.csv` at load time, keyed off
the selected category. Callers do not choose it.

This is deliberate. A common pattern in spreadsheet-based scorecards is to
make the override outcome an independently selected field with no enforced
link to the chosen category, so an evaluator can flag one thing and apply the
outcome of another. Deriving it removes that class of error entirely.

One exception, and only one: the **goodwill promotion**. A category whose
resolved type is `partial_fail` may be promoted to `auto_zero` when goodwill
was issued at or above `GOODWILL_AUTOZERO_LIMIT` without approval. Human
confirmed in v1, so it arrives as an explicit caller decision. Enforced in
`app.main._resolve_override_type`:

- promotion is accepted only for categories in `PROMOTABLE_CATEGORIES`
- promotion is accepted only in the direction `partial_fail` → `auto_zero`
- any demotion attempt is rejected with 422
- a promoted flag inherits the `auto_zero` gate, so it is held pending review

---

## Implementation status

Built. Reflected in the code as follows:

- `ScoreResult.status` (`final` vs `pending_human_review`) is distinct from
  `final_score`. The engine still computes the auto_zero result so a reviewer
  has something concrete to confirm or reject; it just isn't treated as
  committed until confirmed.
- The gate keys off `override_type`, not category name, so a future category
  assigned `auto_zero` gets the gate automatically without editing gate logic.
- `override_type` itself is derived from `rcm_violations.csv` at load time,
  not hardcoded, so the policy above and the code cannot drift apart.
- The unresolved-category behavior is unchanged and still applies: the
  service refuses to start if any category has no valid `override_type`.
- `override_superseded` records a rejected flag without erasing it.

Covered by tests in `tests/test_api.py`, including the reject-preserves-flag
case and the demotion-rejected case specifically.

### Not enforced by this prototype

Stated here so the gap is explicit rather than implied by the paragraphs above.

- **Separation of reviewer from evaluator.** The intended control is that an
  `auto_zero` flag is reviewed by someone other than the person who raised it.
  Nothing implements this. There is no authentication on any endpoint, and
  `reviewed_by` is a free-text string the caller asserts. Anyone who can reach
  the API can confirm their own flag under any name. Enforcing this requires
  an identity layer and an evaluator field on the score row, neither of which
  exists yet.
- **Audit integrity.** Review fields are mutable columns on the score row, not
  append-only events. A second review cannot currently overwrite a completed
  one (the endpoint returns 409), but nothing at the storage layer prevents
  direct modification.

Both belong in the "before this touches real interactions" list, alongside
redaction, retention, and kappa calibration.

---

## What this means for `criteria.csv` / `automation_method`

Unrelated axis, same turn's decision: `automation_method` for the 14 base
criteria is filled in now (`rule` where a deterministic check exists, `model`
where it's a judgment call) rather than left `not_automatable`. Errors there
are caught by ordinary human review of scores, which is a materially lower
stakes situation than an unreviewed `auto_zero` — that asymmetry is exactly
why the two axes get different treatment in this project.

Note that this supersedes the guidance in the v3 project instructions, which
states that `not_automatable` is the expected value for judgment criteria such
as empathy and tone. Update v3 or this note, but do not leave both standing.
