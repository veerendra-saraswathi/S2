# Policy for the groundDrone action (ontologies/core-fleet).
package s2.actions.ground_drone

import rego.v1

import data.s2.actions.common

deny contains reason if some reason in common.deny

# Holds for a null target and for a missing one.
deny contains "the drone does not exist" if not input.target.primary_key

deny contains "the drone is already grounded" if input.target.properties.status == "GROUNDED"

# Pulling a drone out of an active mission is a bigger call than grounding an idle one.
deny contains "only a fleet-supervisor may ground a drone that is in a mission" if {
	input.target.properties.status == "IN_MISSION"
	not common.has_role("fleet-supervisor")
}

default allow := false

allow if {
	common.well_formed
	count(deny) == 0
}
