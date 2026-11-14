# Policy for actions that need nothing beyond the baseline rules.
# It is the default `policy_path` (s2/actions/standard) of an action type.
package s2.actions.standard

import rego.v1

import data.s2.actions.common

deny contains reason if some reason in common.deny

default allow := false

allow if {
	common.well_formed
	count(deny) == 0
}
