# Ontology packs

A pack is a YAML file that defines object types, link types and action types. Packs are how the ontology is authored: reviewed in pull requests, versioned with the code, applied through the API.

| Pack | Defines |
|---|---|
| [core-fleet](core-fleet/pack.yaml) | `CommercialDrone`, `GroundRobot`, `InspectionMission`, `WorkOrder`; actions `groundDrone`, `returnDroneToService`, `createWorkOrder` |
| [energy-grid](energy-grid/pack.yaml) | `Substation`, `WindTurbine`; links from drones and work orders to substations. Requires core-fleet |

## Applying

```bash
make seed                                                   # both packs, plus demo objects
docker compose -f deploy/docker-compose/docker-compose.yml exec ontology-engine \
  python -m s2_ontology.cli apply-pack /app/ontologies/core-fleet/pack.yaml
```

Applying needs the `ontology-admin` role and is idempotent: the response lists what was created, updated and left unchanged.

## Rules for changing a pack

- **Adding** object types, properties, links and actions is always safe.
- **Removing a property** from a pack marks it `deprecated`: existing values stay readable, new writes are refused. Nothing is dropped.
- **Refused as breaking**: changing a property's `data_type` or `is_array`, moving the primary key, changing a link's target type. Add a new property or link and deprecate the old one instead.
- An object type may have at most one `geopoint` / `geoshape` property.
- An action is checked when applied: its edits must refer to existing types and writable properties, its templates may only use declared parameters, and an action open to agents must require approval.

## Naming

`api_name` is the stable identifier used in URLs, telemetry topics and policies: `PascalCase` for object types, `camelCase` for properties, links and actions. Changing an `api_name` creates a new thing; it does not rename the old one. `display_name` is free text and can change at will.

## Templates in actions

`"{{ params.name }}"` substitutes an action parameter into an edit or a write-back payload. A value that is exactly one reference keeps the parameter's type and becomes null when the parameter was not supplied. There are no expressions; conditions go in the action's policy (`policy_path`, see [policies/](../policies/README.md)).

## Triage rules

`triage_rules` turn "needs attention" into a work queue in the Workshop ([ADR 0014](../docs/architecture/adr/0014-triage-rules.md)):

```yaml
triage_rules:
  - api_name: degradingActuators        # camelCase, unique in the tenant
    display_name: Degrading actuators
    description: Ground them before they fly again.
    object_type: CommercialDrone
    severity: high                      # low | medium | high | critical
    when:                               # all must hold
      - {property: actuatorHealth, op: lt, value: 0.8, max_age_s: 600}
      - {property: status, op: ne, value: GROUNDED}
    actions: [groundDrone]              # offered on each item; must target the same type
```

- Operators: `eq`, `ne`, `in` (a list) on text, numbers, booleans and dates; `lt`, `le`, `gt`, `ge` on numbers, dates and time series.
- On a time series the condition tests the latest reading, which must be no older than `max_age_s` (default 900). A missing or stale reading fails the condition.
- A missing stored value fails every comparison except `ne`.
- Values must fit the property: numbers for numeric properties, members of its `enum` when it has one.
- A rule that tests a marked property is visible only to people cleared for it.
