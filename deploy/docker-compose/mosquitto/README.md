# The local MQTT broker

Eclipse Mosquitto 2, the telemetry bus between field gateways and S2's `mqtt-bridge`.
Every client logs in, and every login is held to [`acl`](acl), on both listeners.

| Port | Transport | Reachable from | For |
|---|---|---|---|
| 8883 | MQTT over TLS 1.2+ | the network | gateways |
| 1883 | plain MQTT | this machine only (`127.0.0.1`), and the Compose network | the bridge, S2's simulator, local tools |

## Secrets

[`setup.sh`](setup.sh) makes them into `secrets/`, which git ignores. `make up` runs it,
and it never replaces what is already there.

| File | What it is |
|---|---|
| `ca.crt` | The development certificate authority. A gateway trusts the broker through it |
| `ca.key` | The CA's key. It stays on this machine |
| `server.crt`, `server.key` | The broker's certificate, for `localhost`, `127.0.0.1`, `::1`, `mqtt` and `s2-mqtt` |
| `passwd` | Mosquitto's hashed password file |
| `<user>.password` | Each user's password, for the client that logs in as that user |

A gateway on another machine that connects by a different name or address needs it in
the certificate. Re-issue with `rm secrets/server.* && MQTT_TLS_NAMES=10.0.0.5,gw-host.local ./setup.sh`,
then `docker restart s2-mqtt`.

Passwords never go on a command line or in an environment variable: Compose mounts each
service's password as a secret file, and the gateways take `-mqtt-password-file`. Hashing
uses `mosquitto_passwd -U` on a file for the same reason. This is a development CA; a
deployment uses its own PKI and its own password store.

## Users

| User | May | Used by |
|---|---|---|
| `s2-bridge` | read `s2/v1/+/+/+/telemetry` | `mqtt-bridge` |
| `s2-sim` | write the dev tenant's telemetry | `make sim` |
| `gw-dev` | write the dev tenant's telemetry | S1.a's MAVLink and CAN gateways |
| `s2-monitor` | read telemetry and `$SYS` | the health check; test tools comparing what the broker delivered with what was stored |

To add a gateway, add a `user` block to `acl` naming its tenant's topics, run `./setup.sh`,
and `docker restart s2-mqtt`. Give the gateway `secrets/ca.crt` and its `.password` file:

```bash
mavlink-gateway -mqtt mqtts://broker-host:8883 -mqtt-user gw-dev \
    -mqtt-password-file gw-dev.password -mqtt-ca ca.crt -vehicles 1=DRN-0001
```

## What a refusal looks like

- **A wrong password, or no login:** the connection is refused. Mosquitto answers both
  with "not authorised" (CONNACK 5).
- **A topic the user may not use:** under MQTT 3.1.1 the broker drops the message and
  says nothing. The publisher counts it as sent; nothing reaches the bridge. A gateway
  configured with the wrong tenant therefore looks healthy on its own side, and the only
  sign is that no telemetry arrives in S2.

## Files

`start.sh` is the container's entrypoint. Mosquitto 2.1 warns, and says future versions
will refuse, when its ACL, password file or key are not owned by its own user, which no
bind-mounted file is; so it copies them into a private directory first.

## Verified

On 2026-11-26, Mosquitto 2.1.4: anonymous and wrong-password logins refused on both
listeners; `gw-dev`'s telemetry for its own tenant delivered, and for another tenant, on a
non-telemetry topic, or from `s2-bridge` dropped; `gw-dev` unable to subscribe; the
certificate verified with `ca.crt` and refused without it; plain MQTT refused on 8883;
1883 unreachable from the LAN address and 8883 reachable. End to end, a TLS gateway's 360
messages were stored, and 360 sent to another tenant stored none.
