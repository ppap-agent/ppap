---
name: product-design-loop
description: Design, prototype, review, implement, and validate user-facing product flows through an evidence-driven repair loop across UI stacks. Resolve only the project facts and evidence paths material to the task; not generic backend refactoring, deployment, or harness-runtime development.
---

# Product design loop

Deliver a usable product outcome, not merely a plausible screen or a green test
run. Keep the user's outcome, accepted project authority and required evidence
stable while letting the agent choose the implementation.

This skill defines procedure. It does not define a repository layout, design system,
runtime, verification stack or product authority.

## Establish the task

Read the repository/workspace instructions and the current authorities for the
changed boundary. Current source and runtime evidence establish what is actually
implemented; accepted project documents establish the contracts they own. Do not
invent a missing authority or treat this skill as one.

Write only when the request authorizes implementation/application or an editable
prototype. Design, proposal and review requests remain read-only unless the user
asks to apply them. When the request is explicitly exploratory, treat it as a
bounded probe/prototype: state the product, capability, workflow or architecture
question before editing and choose the smallest real scenario that can change that
decision.

For delivery, identify the user, action/decision, observable successful result and
product meanings that must remain unchanged. For a probe, also state what
observation would support, weaken or leave the hypothesis unresolved.

A probe may stop when its bounded question is answered. Its evidence does not
silently become production acceptance, universal capability proof or an architecture
mandate. Do not require unrelated production proof that cannot affect the probe's
decision.

## Resolve only the necessary context

Before editing, resolve only the facts needed to make and verify this change. These
may include the target surface, runtime/interaction model, shared UI owner,
token/style owner, design context, preview path, native checks and available
evidence. Do not build a complete environment inventory when the task does not need
one.

Finding facts is the agent's job. Inspect only the relevant authorities, source,
tooling, tests or real experience needed to resolve those facts; broaden the search
only when material uncertainty remains. Do not ask the user for discoverable facts.
Multiple surfaces or stacks may coexist; resolve the owner for the task's actual
boundary rather than inferring the whole repository from one dependency.

Distinguish:

- what an accepted project authority declares;
- what current source or tooling directly demonstrates;
- what is only an inference;
- what remains unresolved.

Unknown facts are allowed. They block only dependent actions or claims: an unknown
shared style owner prevents creating a competing shared style authority; unavailable
real-UI evidence prevents claiming that the experience was observed. Do not turn
unrelated uncertainty into a global blocker.

Ask the user only for a genuinely material decision after its prerequisite facts
are known. Give the relevant evidence and a recommended resolution. Do not ask
downstream questions early or require confirmation for ordinary implementation
choices already determined by project authority.

### Keep one owner for each design fact

For design-bearing work, use the project's accepted agent-facing design context
when one exists. A structured contract such as `DESIGN.md` can reduce context, but
this skill does not require one or create one merely to satisfy the workflow.

When the same design fact appears in several places, identify the canonical owner.
Treat other copies as generated/derived projections or reviewed guidance as
appropriate. A document may own prose guidance while projecting exact values from
another machine source.

If the project has adopted a structured design contract, validate it with the
project's owning/pinned tooling before relying on it. Let that tooling own the
format rules; do not copy its schema into this skill. An invalid or stale declared
design contract is a finding, not valid evidence.

Do not turn an agent-facing design document into a hand-maintained mirror of
product semantics, runtime architecture, verification policy, security rules or
implementation details that already have owners. Prefer links or generated
projections for repeated facts.

## Design and implement at the owner

Inspect the real current experience when an accepted preview/inspection path exists.
Trace affected data and actions to their owners. Establish relevant normal,
unavailable and interrupted states rather than designing only the happy screenshot.

Judge the design by whether the user can understand the object, current state,
allowed next action, consequence and recovery. Preserve useful density through
hierarchy and grouping rather than small text or hidden meaning. Exercise constrained
layouts according to the surface's actual layout model instead of assuming browser,
mobile or desktop conventions.

Choose the smallest coherent change that fixes the task. External design tools may
suggest layouts, but they cannot define permissions, invent product facts or replace
the project's accepted design authority. Send external tools only project-authorized
inputs; prefer synthetic or explicitly allowlisted material.

Implement shared decisions at their canonical owner. Reuse the owning shared UI
layer where one exists. Repair shared defects at that layer rather than compensating
with page-local overrides, and do not add a parallel token, theme or component
authority for one task. Do not hand-edit a generated design projection when its
source is elsewhere.

Keep runtime/measured geometry with its real owner; not every number is a token.
Preserve permissions, state truth, exact task/return targets where applicable,
truthful write outcomes, sensitive/one-time data handling, focus, consequences and
recovery. Preserve runtime-specific fallback or platform behavior only when the
accepted surface contract requires it.

Necessary domain fixes use their owning contract and tests. Do not expand into
unrelated backend work or repository-wide cleanup. Update an owning document only
when an accepted reusable contract changes, not to narrate the run.

## Verify, challenge and repair

Finish edits, formatting and generation before the evidence pass. Identify the
actual candidate, including relevant new/uncommitted files and configuration; a
repository HEAD alone may not identify a dirty working tree. Do not let parallel
writers mutate the candidate during verification or review.

Use the smallest sufficient native checks from the project's existing verification
path. Avoid duplicate generation or overlapping runners. Tests should protect the
changed contract rather than today's palette, prose, selectors or layout instance.

Operate the real UI when the changed outcome requires it. Inspect the relevant
state transitions, input/focus, layout/theme variants and failure/recovery behavior.
Use approved synthetic fixtures or captures and do not record secrets or private
data. A screenshot supports visual inspection but does not prove task completion;
source inference does not substitute for an available real-UI observation.

Keep machine results, observed user behavior and design judgment distinct. Record
what actually ran on which candidate and what was not observed. Missing tooling,
failed/cancelled checks or unavailable evidence cannot be reported as a pass. A
relevant source, policy or toolchain change invalidates affected evidence.

Where available and authorized, use one fresh read-only reviewer with the goal,
accepted contracts, candidate and relevant evidence. Ask what could still make the
user say the task is not done: unclear action/consequence, lost recovery,
inaccessible interaction, wrong owner, mismatched surface/runtime or unsupported
proof. A prompt alone does not establish read-only isolation.

If independent review is unavailable, perform an explicit self-review and disclose
that limit. For each material blocker, connect the finding to the violated outcome
or contract, repair it within scope, then repeat the affected evidence. Do not
weaken acceptance or rerun unchanged failures until green.

## Stop and report

For delivery, stop when the scoped user outcome is demonstrated with valid evidence
for the current candidate and no supported in-scope blocker remains. For a probe,
stop when the decision question has sufficient discriminating evidence. For review,
report the assessment without editing.

For any mode, if required authority, tooling, isolation or evidence is unavailable,
an applicable budget is exhausted, or no meaningful progress remains, stop blocked
and report the last proven boundary and remaining gap. Do not widen scope, weaken
acceptance or retry unchanged failures merely to obtain success.

Report the strongest result actually established:

- the outcome or hypothesis;
- the materially relevant owners/context and any unresolved fact;
- candidate identity and evidence that actually ran;
- review findings/disposition;
- unsupported or unverified limits.

For probes, separate observed facts, interpretation for the decision and untested
conditions. Keep local verification, CI/integration, merge and deployment distinct
when applicable. Approval to implement or prototype is not approval to publish,
merge, deploy or revise project authority.

## Recursive improvement: proposals only

When a run exposes a reusable process or architecture lesson, use
[RSI](references/rsi.md) to propose the smallest general improvement. Product fixes,
project-authority gaps, skill guidance and missing tooling are different owners.
Do not modify the skill, project architecture or trackers as an incidental side
effect. If there is no supported reusable lesson, stop without inventing one.
