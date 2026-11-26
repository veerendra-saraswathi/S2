#!/bin/sh
# The broker container's entrypoint. Mosquitto wants its ACL, password file and key
# owned by its own user and unreadable by others, and says future versions will refuse
# them otherwise; files bind-mounted from the host are neither. So copy them into a
# directory the broker owns, then start it the way the image does.
set -eu

run=/mosquitto/run
mkdir -p "$run"
cp /mosquitto/config/acl /mosquitto/secrets/passwd \
   /mosquitto/secrets/server.crt /mosquitto/secrets/server.key /mosquitto/secrets/ca.crt "$run/"
chown -R mosquitto:mosquitto "$run"
chmod 0700 "$run"
chmod 0600 "$run"/*

exec /docker-entrypoint.sh mosquitto -c /mosquitto/config/mosquitto.conf
