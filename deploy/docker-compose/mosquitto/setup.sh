#!/bin/sh
# Make the broker's local secrets: a certificate authority, the broker's certificate,
# and a password for each MQTT user in ./acl. Everything goes in ./secrets, which git
# ignores. Run by `make up`; it never replaces what exists, so it is safe to repeat.
#
#   secrets/ca.crt               give this to a client to trust the broker (mqtts://, port 8883)
#   secrets/ca.key               signs the broker's certificate; never leaves this machine
#   secrets/server.crt, .key     the broker's identity: localhost, 127.0.0.1, mqtt, s2-mqtt
#   secrets/passwd               Mosquitto's hashed password file
#   secrets/<user>.password      each user's password, for the client that logs in as it
#
# A broker reached by another name needs it in the certificate: MQTT_TLS_NAMES="drone-gw.local,10.0.0.5"
# Delete secrets/server.* and run again to re-issue the certificate; delete <user>.password
# to change a password. This is a development CA: a deployment uses its own PKI.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
dir="$here/secrets"
image="eclipse-mosquitto:${MOSQUITTO_VERSION:-2}"
mkdir -p "$dir"
chmod 700 "$dir"

command -v openssl >/dev/null || { echo "setup: openssl is needed to make the broker's certificate" >&2; exit 1; }

if [ ! -f "$dir/ca.key" ]; then
    echo "setup: creating a development certificate authority"
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 3650 \
        -subj "/CN=S2 development MQTT CA" -keyout "$dir/ca.key" -out "$dir/ca.crt" 2>/dev/null
    rm -f "$dir/server.crt" "$dir/server.key"  # a new CA invalidates the old certificate
fi

if [ ! -f "$dir/server.crt" ]; then
    echo "setup: issuing the broker's certificate"
    names="DNS:localhost,DNS:mqtt,DNS:s2-mqtt,IP:127.0.0.1,IP:::1"
    for extra in $(printf '%s' "${MQTT_TLS_NAMES:-}" | tr ',' ' '); do
        case "$extra" in
            *[!0-9.:]*) names="$names,DNS:$extra" ;;
            *) names="$names,IP:$extra" ;;
        esac
    done
    ext=$(mktemp)
    printf 'subjectAltName=%s\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n' "$names" > "$ext"
    openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -subj "/CN=s2-mqtt" \
        -keyout "$dir/server.key" -out "$dir/server.csr" 2>/dev/null
    openssl x509 -req -in "$dir/server.csr" -CA "$dir/ca.crt" -CAkey "$dir/ca.key" -CAcreateserial \
        -days 825 -extfile "$ext" -out "$dir/server.crt" 2>/dev/null
    rm -f "$ext" "$dir/server.csr" "$dir/ca.srl"
fi

# One password per user the ACL names.
users=$(awk '$1 == "user" { print $2 }' "$here/acl")
changed=false
for user in $users; do
    if [ ! -f "$dir/$user.password" ]; then
        openssl rand -hex 24 > "$dir/$user.password"
        changed=true
    fi
done
if [ "$changed" = true ] || [ ! -f "$dir/passwd" ]; then
    echo "setup: writing the password file for: $(echo $users)"
    # Plain user:password lines, hashed in place by -U, so no password is ever on a
    # command line where `ps` would show it.
    : > "$dir/passwd"
    chmod 600 "$dir/passwd"
    for user in $users; do
        printf '%s:%s\n' "$user" "$(cat "$dir/$user.password")" >> "$dir/passwd"
    done
    docker run --rm -v "$dir:/s" "$image" mosquitto_passwd -U /s/passwd
fi

chmod 600 "$dir"/*
chmod 644 "$dir/ca.crt"
