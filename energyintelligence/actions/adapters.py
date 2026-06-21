"""Device-control adapters.

``MockDeviceAdapter`` returns computed-but-mocked effects so the whole action
flow works end-to-end with no hardware. A real adapter would implement the same
``apply(command)`` surface (e.g. REST to an inverter/wallbox), making it a
drop-in replacement.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DeviceAdapter:
    """Interface a real device-control backend would implement."""

    def apply(self, household_id: str, command: dict) -> dict:  # pragma: no cover
        raise NotImplementedError


class MockDeviceAdapter(DeviceAdapter):
    """Logs the command and echoes it back as an acknowledged effect."""

    def apply(self, household_id: str, command: dict) -> dict:
        return {"acknowledged": True, "command": command, "device": "mock"}
