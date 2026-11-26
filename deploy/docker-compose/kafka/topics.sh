#!/bin/sh
# Create the telemetry topics with their partitions before anything produces to them, so
# the broker does not auto-create them with one. Run once by the kafka-topics service.
#
# Partitions can only grow. On an existing topic with fewer than asked, this raises the
# count; Kafka then places some keys (one object each) on a new partition, so for that
# moment only, an object's readings on either side of the change may be read out of order.
# The sink's inserts are idempotent, so nothing is stored twice or lost.
set -eu

topics=/opt/kafka/bin/kafka-topics.sh
server="--bootstrap-server ${KAFKA_BOOTSTRAP:-kafka:9092}"

ensure() { # topic partitions
	"$topics" $server --create --if-not-exists --topic "$1" --partitions "$2" --replication-factor 1 >/dev/null
	have=$("$topics" $server --describe --topic "$1" | sed -n 's/.*PartitionCount: *\([0-9][0-9]*\).*/\1/p' | head -n 1)
	if [ "$have" -lt "$2" ]; then
		"$topics" $server --alter --topic "$1" --partitions "$2"
		echo "$1: raised from $have to $2 partitions"
	else
		echo "$1: $have partitions"
	fi
}

# Readings are keyed by object, so one object stays on one partition and in order; the
# sinks in group s2-timeseries-sink share the partitions between them.
ensure s2.telemetry.raw "${TELEMETRY_PARTITIONS:-4}"
ensure s2.telemetry.dlq 1
