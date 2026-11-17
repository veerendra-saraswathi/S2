# Baseline rules that every action policy includes.
#
# Input document (built by services/ontology-engine, domain/actions.py):
#
#   stage         "request" | "approval"
#   actor         {id, type: "user"|"service"|"agent", roles: [...], tenant_id}
#   requested_by  subject of whoever requested the action (differs from actor at approval)
#   action        {api_name, version, requires_approval, agent_access}
#   parameters    the validated action parameters
#   target        {object_type, primary_key, properties} of the object acted on, or null
#
# Contract with the engine: a policy package exposes `allow` (boolean) and `deny`
# (set of reasons). The engine executes only when allow is true and deny is empty,
# and treats everything else, including an undefined result, as a denial.
#
# Writing rules that fail closed. Negation needs care when the input may be incomplete:
#
#   not input.x == "y"          denies when input.x is missing          (safe)
#   not input.x                 denies when input.x is missing          (safe)
#   not "r" in input.roles      does NOT deny when input.roles is missing
#   not f(input.x)              does NOT deny when input.x is missing
#
# In the last two, Rego evaluates the reference before the negation, the rule body is
# undefined, and no reason is added. Negate a helper rule instead (`not has_role(...)`,
# `not valid_stage`): an undefined helper makes the negation true. `well_formed` backs
# this up by denying any input that lacks a field these rules read.
package s2.actions.common

import rego.v1

stages := {"request", "approval"}

actor_types := {"user", "service", "agent"}

valid_stage if input.stage in stages

has_role(role) if role in input.actor.roles

agent_may_propose if input.action.agent_access == "propose"

well_formed if {
	valid_stage
	is_string(input.actor.id)
	input.actor.type in actor_types
	is_array(input.actor.roles)
	is_string(input.requested_by)
	is_boolean(input.action.requires_approval)
	is_string(input.action.agent_access)
}

# Fail closed on input the engine should never send.
deny contains "malformed policy input" if not well_formed

deny contains "unknown stage" if not valid_stage

deny contains "requesting an action needs the operator role" if {
	input.stage == "request"
	not has_role("operator")
}

deny contains "approving an action needs the approver role" if {
	input.stage == "approval"
	not has_role("approver")
}

# Four-eyes: whoever asked for it cannot be the one to approve it.
deny contains "the requester cannot approve their own request" if {
	input.stage == "approval"
	input.actor.id == input.requested_by
}

# Four-eyes reaches through agents: the person an agent acts for cannot approve what the
# agent proposed, or the agent would be a way to approve one's own requests.
deny contains "the person an agent acts for cannot approve its proposal" if {
	input.stage == "approval"
	is_string(input.requested_on_behalf_of)
	acts_for_actor(input.requested_on_behalf_of)
}

# An agent names its person by subject or by username.
acts_for_actor(person) if input.actor.id == person

acts_for_actor(person) if input.actor.username == person

# AI agents never execute directly: they may only propose actions that a human approves.
deny contains "agents may only propose actions that require human approval" if {
	input.actor.type == "agent"
	not input.action.requires_approval
}

deny contains "this action is not open to agents" if {
	input.actor.type == "agent"
	not agent_may_propose
}

deny contains "agents cannot approve actions" if {
	input.stage == "approval"
	input.actor.type == "agent"
}
