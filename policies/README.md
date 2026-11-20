# Policies

Open Policy Agent policies, in Rego. Every governed action is decided here: who may request or approve it, and whether the state of the world allows it.

```
s2/actions/
  common.rego         baseline rules every action policy includes
  standard.rego       the default policy: baseline only        (policy_path s2/actions/standard)
  ground_drone.rego   baseline + rules for groundDrone         (policy_path s2/actions/ground_drone)
  actions_test.rego   unit tests
```

## Contract with the ontology engine

An action type's `policy_path` names a package. The engine posts the input below to it and executes the action only if the package's `allow` is `true` and its `deny` set is empty. An undefined result, a missing `allow`, or an unreachable OPA all mean no.

```json
{
  "stage": "request",
  "actor": {"id": "…", "type": "user", "roles": ["operator"], "tenant_id": "…"},
  "requested_by": "…",
  "action": {"api_name": "groundDrone", "version": 1, "requires_approval": false, "agent_access": "none"},
  "parameters": {"droneId": "DRN-0003", "reason": "…"},
  "target": {"object_type": "CommercialDrone", "primary_key": "DRN-0003", "properties": {"status": "READY"}},
  "context": {}
}
```

`stage` is `request` when the action is asked for and `approval` when a pending one is approved; at approval `actor` is the approver and `requested_by` the original requester. `target` is `null` when the action has no target object or the object does not exist.

`context` holds the objects an action type declares in `policy_context`, for a policy that needs more than the target: the evidence a step names, say. Each entry is `{object_type, key, links}`; `key` may reference parameters, and the policy sees `input.context.<name>` as `{object_type, primary_key, properties, links: {<link name>: [far primary keys]}}`, or `null` when the object does not exist. Links may be forward or reverse names, and at most 500 keys are shown per link. A pack is refused at registration if a context names an unknown type, an unknown link or an undeclared parameter. `context` is `{}` for an action that declares none.

```yaml
policy_context:
  run: {object_type: TestRun, key: "{{ params.testRunId }}", links: [testsBuild]}
```

Reasons in `deny` are returned to the caller and written to the audit log, so write them for a person.

The baseline (`common.rego`) requires the `operator` role to request and the `approver` role to approve, forbids approving your own request, lets AI agents only propose actions that are both approval-gated and marked `agent_access: propose`, and never lets an agent approve.

## Writing a policy for a new action

```rego
package s2.actions.dispatch_inspection

import rego.v1

import data.s2.actions.common

deny contains reason if some reason in common.deny

deny contains "the drone is not ready" if not input.target.properties.status == "READY"

deny contains "dispatching needs the dispatcher role" if not common.has_role("dispatcher")

default allow := false

allow if {
	common.well_formed
	count(deny) == 0
}
```

Then set `policy_path: s2/actions/dispatch_inspection` on the action in its pack, and add tests.

### Make missing input deny

A rule that reads a field the input does not have is undefined, and an undefined `deny` rule denies nothing. Three habits keep a policy closed:

- Compare with `not x == y`, never `x != y`. The second is silently false when `x` is absent.
- Do not negate `in` or a function call directly. `not "r" in input.actor.roles` and `not startswith(input.x, "a")` deny nothing when the field is missing, because Rego resolves the reference before applying `not`. Negate a helper rule instead, as `common.has_role` does.
- Keep `common.well_formed` in `allow`. It refuses any input that lacks a field the baseline reads, whatever the individual rules do.

`actions_test.rego` has a test for each of these, including an empty input. Add one for every new rule that negates.

## Testing

```bash
opa test policies -v     # or: make policy-test
```

The local stack's OPA watches this directory and reloads on save.
