# 0014. Triage rules live in packs and are evaluated on read

Status: accepted (November 2026). Phase 3.

## Context

The specification asks for "action-driven triage": operators should not have to find the drone whose actuators are failing; the platform should put it in front of them with the action to take. The ingredients exist: object state, live telemetry, governed actions. What is missing is a definition of "needs attention" and a place to see it.

## Decision

**Rules are ontology definitions.** A pack's `triage_rules` name an object type, a list of conditions that must all hold, a severity, and the actions to offer. They are applied, versioned, audited and isolated by tenant like object, link and action types (migration 0004). A rule that could never work is refused when the pack is applied: an unknown property, an ordering test on text, a value outside a property's enum, a freshness limit on a stored property, an action that targets another type.

**A condition on a time series tests the latest reading, which must be recent** (`max_age_s`, default 15 minutes). A drone that stopped reporting is not healthy, but nor is silence proof of a fault, so a missing or stale reading fails the condition. A missing stored value fails every comparison except `ne`.

**Evaluated when read, not materialised.** `GET /v1/triage/queues` evaluates every rule the caller may see and counts its matches; `GET /v1/triage/queues/{rule}` returns the matches, each with its evidence (the value compared, and for a series when it was read). There is no queue table to keep in sync, so a queue can never disagree with the objects: ground the drone and it leaves the queue on the next read.

**Markings apply.** A rule over a type or a property the caller is not cleared for does not exist for them, because its items and evidence would reveal the hidden values.

**The Workshop shows queues by severity**, with the evidence in words ("Actuator health 0.716 < 0.8, 42 s ago") and the rule's actions inline, and counts the urgent (high and critical) items in the navigation.

## Consequences

- Cost grows with objects × rules × reads: each evaluation lists up to 5,000 objects of the type and runs one telemetry query per series condition. Fine for fleets of hundreds to a few thousand assets. Beyond that, rules should be evaluated by a stream processor on the telemetry path and their matches stored, which this API can then read without changing shape.
- No history: the API says what is in a queue now, not when something entered it or who dealt with it. The audit log has the actions taken; a queue history (and assignment, snoozing, acknowledgement) needs stored state.
- No notifications: a queue is seen when someone looks. Push (email, chat, pager) needs the stored evaluation above.
- Conditions are a flat AND of comparisons. OR, aggregates over time windows (mean over 10 minutes), conditions across links ("substation's inspection is overdue") and model scores are not expressible yet.
