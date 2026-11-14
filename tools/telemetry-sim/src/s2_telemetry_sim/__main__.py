"""Synthetic fleet telemetry over MQTT, following the S2 telemetry contract v1.

Publishes for drones DRN-0001.. (CommercialDrone) and ground robots UGV-0001..
(GroundRobot), the same keys `s2ctl seed-demo` creates. The last drone's actuator
health degrades over time, which gives you something to act on.

Environment
    S2_MQTT_URL       mqtt://localhost:1883
    S2_SIM_TENANT     00000000-0000-0000-0000-000000000001   (the dev realm's tenant)
    S2_SIM_FLEET      number of drones, default 3
    S2_SIM_ROBOTS     number of ground robots, default 1
    S2_SIM_RATE_HZ    messages per vehicle per second, default 1

Run:  python -m s2_telemetry_sim
"""

from __future__ import annotations

import json
import math
import os
import random
import signal
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import paho.mqtt.client as mqtt

BASE_LAT, BASE_LON = 17.4401, 78.3489  # Hyderabad


@dataclass
class Vehicle:
    object_type: str
    key: str
    lat: float
    lon: float
    altitude_m: float
    speed_mps: float
    degrading: bool = False
    heading_deg: float = field(default_factory=lambda: random.uniform(0, 360))
    battery_v: float = field(default_factory=lambda: random.uniform(24.2, 25.2))
    actuator_health: float = 0.99
    seq: int = 0

    def step(self, dt: float) -> dict[str, Any]:
        self.seq += 1
        self.heading_deg = (self.heading_deg + random.uniform(-15, 15)) % 360
        distance = self.speed_mps * dt
        self.lat += distance * math.cos(math.radians(self.heading_deg)) / 111_320
        self.lon += (
            distance * math.sin(math.radians(self.heading_deg)) / (111_320 * math.cos(math.radians(self.lat)))
        )
        self.battery_v -= random.uniform(0.0005, 0.002) * dt
        if self.battery_v < 21.0:  # battery swap
            self.battery_v = 25.2
        if self.degrading:
            self.actuator_health = max(0.40, self.actuator_health - random.uniform(0.0002, 0.0012) * dt)
        metrics: dict[str, Any] = {"battery_voltage": round(self.battery_v, 3)}
        if self.object_type == "CommercialDrone":
            metrics["actuator_health"] = round(self.actuator_health + random.uniform(-0.004, 0.004), 4)
        return {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "seq": self.seq,
            "metrics": metrics,
            "position": {
                "lat": round(self.lat, 7),
                "lon": round(self.lon, 7),
                "alt_m": round(self.altitude_m + random.uniform(-0.5, 0.5), 2),
                "heading_deg": round(self.heading_deg, 1),
                "speed_mps": self.speed_mps,
            },
        }


def build_fleet(drones: int, robots: int) -> list[Vehicle]:
    def near_base() -> tuple[float, float]:
        return BASE_LAT + random.uniform(-0.01, 0.01), BASE_LON + random.uniform(-0.01, 0.01)

    fleet = [
        Vehicle(
            "CommercialDrone",
            f"DRN-{n:04d}",
            *near_base(),
            altitude_m=120.0,
            speed_mps=12.0,
            degrading=(n == drones),
        )
        for n in range(1, drones + 1)
    ]
    fleet += [
        Vehicle("GroundRobot", f"UGV-{n:04d}", *near_base(), altitude_m=0.0, speed_mps=1.5)
        for n in range(1, robots + 1)
    ]
    return fleet


def main() -> None:
    broker = urlparse(os.environ.get("S2_MQTT_URL", "mqtt://localhost:1883"))
    tenant = os.environ.get("S2_SIM_TENANT", "00000000-0000-0000-0000-000000000001")
    rate_hz = float(os.environ.get("S2_SIM_RATE_HZ", "1"))
    fleet = build_fleet(int(os.environ.get("S2_SIM_FLEET", "3")), int(os.environ.get("S2_SIM_ROBOTS", "1")))
    interval = 1.0 / rate_hz

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="s2-telemetry-sim")
    client.connect(broker.hostname or "localhost", broker.port or 1883, keepalive=30)
    client.loop_start()

    running = True

    def stop(*_: Any) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    print(
        f"publishing {len(fleet)} vehicles at {rate_hz} Hz to {broker.hostname}:{broker.port or 1883}",
        flush=True,
    )
    while running:
        started = time.monotonic()
        for vehicle in fleet:
            topic = f"s2/v1/{tenant}/{vehicle.object_type}/{vehicle.key}/telemetry"
            client.publish(topic, json.dumps(vehicle.step(interval)), qos=1)
        time.sleep(max(0.0, interval - (time.monotonic() - started)))

    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
