# Run with:  opa test policies -v
package s2.actions_test

import rego.v1

import data.s2.actions.ground_drone
import data.s2.actions.standard

operator := {"id": "u-operator", "type": "user", "roles": ["operator"], "tenant_id": "t1"}

approver := {"id": "u-approver", "type": "user", "roles": ["approver"], "tenant_id": "t1"}

viewer := {"id": "u-viewer", "type": "user", "roles": ["viewer"], "tenant_id": "t1"}

immediate := {"api_name": "groundDrone", "version": 1, "requires_approval": false, "agent_access": "none"}

gated := {"api_name": "returnDroneToService", "version": 1, "requires_approval": true, "agent_access": "propose"}

drone(status) := {"object_type": "CommercialDrone", "primary_key": "DRN-0001", "properties": {"status": status}}

request(actor, action, target) := {
	"stage": "request",
	"actor": actor,
	"requested_by": actor.id,
	"action": action,
	"parameters": {},
	"target": target,
}

approval(actor, requested_by, action, target) := {
	"stage": "approval",
	"actor": actor,
	"requested_by": requested_by,
	"action": action,
	"parameters": {},
	"target": target,
}

# --- baseline -----------------------------------------------------------------

test_operator_may_request if standard.allow with input as request(operator, immediate, null)

test_viewer_may_not_request if {
	not standard.allow with input as request(viewer, immediate, null)
	"requesting an action needs the operator role" in standard.deny with input as request(viewer, immediate, null)
}

test_unknown_stage_is_denied if {
	bad := object.union(request(operator, immediate, null), {"stage": "surprise"})
	not standard.allow with input as bad
	"unknown stage" in standard.deny with input as bad
}

test_missing_stage_is_denied if {
	bad := object.remove(request(operator, immediate, null), ["stage"])
	not standard.allow with input as bad
	"unknown stage" in standard.deny with input as bad
}

# --- malformed input must never be allowed ------------------------------------
# These guard against Rego's `not f(input.x)` trap: with input.x missing, such a rule
# is undefined rather than a denial. See the note at the top of common.rego.

test_empty_input_is_denied if {
	not standard.allow with input as {}
	not ground_drone.allow with input as {}
	"malformed policy input" in standard.deny with input as {}
}

test_actor_without_roles_is_denied if {
	bad := json.remove(request(operator, immediate, null), ["actor/roles"])
	not standard.allow with input as bad
	"requesting an action needs the operator role" in standard.deny with input as bad
}

test_missing_actor_is_denied if {
	bad := object.remove(approval(approver, "u-operator", gated, null), ["actor"])
	not standard.allow with input as bad
	"approving an action needs the approver role" in standard.deny with input as bad
}

test_missing_requester_is_denied if {
	bad := object.remove(approval(approver, "u-operator", gated, null), ["requested_by"])
	not standard.allow with input as bad
}

test_missing_action_is_denied if {
	bad := object.remove(request(operator, immediate, null), ["action"])
	not standard.allow with input as bad
}

test_agent_cannot_approve if {
	agent := {"id": "copilot", "type": "agent", "roles": ["approver"], "tenant_id": "t1"}
	not standard.allow with input as approval(agent, "u-operator", gated, null)
	"agents cannot approve actions" in standard.deny with input as approval(agent, "u-operator", gated, null)
}

test_agent_without_agent_access_field_is_denied if {
	agent := {"id": "copilot", "type": "agent", "roles": ["operator"], "tenant_id": "t1"}
	action := {"api_name": "x", "version": 1, "requires_approval": true}
	not standard.allow with input as request(agent, action, null)
}

test_ground_in_mission_without_roles_is_denied if {
	supervisor := {"id": "u-sup", "type": "user", "roles": ["operator", "fleet-supervisor"], "tenant_id": "t1"}
	bad := json.remove(request(supervisor, immediate, drone("IN_MISSION")), ["actor/roles"])
	not ground_drone.allow with input as bad
	"only a fleet-supervisor may ground a drone that is in a mission" in ground_drone.deny with input as bad
}

test_approver_may_approve_someone_elses_request if {
	standard.allow with input as approval(approver, "u-operator", gated, null)
}

test_requester_cannot_approve_own_request if {
	both := {"id": "u-both", "type": "user", "roles": ["operator", "approver"], "tenant_id": "t1"}
	not standard.allow with input as approval(both, "u-both", gated, null)
}

test_operator_without_approver_role_cannot_approve if {
	not standard.allow with input as approval(operator, "u-someone", gated, null)
}

test_agent_may_propose_a_gated_action if {
	agent := {"id": "copilot", "type": "agent", "roles": ["operator"], "tenant_id": "t1"}
	standard.allow with input as request(agent, gated, null)
}

test_agent_may_not_run_an_immediate_action if {
	agent := {"id": "copilot", "type": "agent", "roles": ["operator"], "tenant_id": "t1"}
	not standard.allow with input as request(agent, immediate, null)
}

# --- groundDrone --------------------------------------------------------------

test_ground_ready_drone if ground_drone.allow with input as request(operator, immediate, drone("READY"))

test_ground_missing_drone_is_denied if {
	not ground_drone.allow with input as request(operator, immediate, null)
	"the drone does not exist" in ground_drone.deny with input as request(operator, immediate, null)
}

test_ground_already_grounded_is_denied if {
	not ground_drone.allow with input as request(operator, immediate, drone("GROUNDED"))
}

test_ground_in_mission_needs_supervisor if {
	not ground_drone.allow with input as request(operator, immediate, drone("IN_MISSION"))
	supervisor := {"id": "u-sup", "type": "user", "roles": ["operator", "fleet-supervisor"], "tenant_id": "t1"}
	ground_drone.allow with input as request(supervisor, immediate, drone("IN_MISSION"))
}

test_ground_by_viewer_is_denied if {
	not ground_drone.allow with input as request(viewer, immediate, drone("READY"))
}

# --- agents acting for a person ---------------------------------------------------

agent_proposal(approver) := object.union(approval(approver, "svc-s2-agent", gated, null), {"requested_on_behalf_of": "supervisor"})

test_the_person_an_agent_acts_for_cannot_approve_its_proposal if {
	supervisor := {"id": "u-supervisor", "type": "user", "roles": ["approver"], "tenant_id": "t1", "username": "supervisor"}
	not standard.allow with input as agent_proposal(supervisor)
	"the person an agent acts for cannot approve its proposal" in standard.deny with input as agent_proposal(supervisor)
}

test_another_approver_may_approve_an_agent_proposal if {
	other := {"id": "u-approver", "type": "user", "roles": ["approver"], "tenant_id": "t1", "username": "approver"}
	standard.allow with input as agent_proposal(other)
}

test_people_s_own_requests_are_unaffected if {
	approver := {"id": "u-approver", "type": "user", "roles": ["approver"], "tenant_id": "t1", "username": "approver"}
	standard.allow with input as object.union(approval(approver, "u-operator", gated, null), {"requested_on_behalf_of": null})
}

test_the_person_named_by_subject_cannot_approve_either if {
	supervisor := {"id": "supervisor", "type": "user", "roles": ["approver"], "tenant_id": "t1"}
	not standard.allow with input as agent_proposal(supervisor)
}
