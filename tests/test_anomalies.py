"""Detectors must reproduce the seeded insight_events for each household."""

from __future__ import annotations

import pytest

from energyintelligence.analytics import anomalies


def _overlaps(a: str, b: str) -> bool:
    """Two 'YYYY-MM-DD..YYYY-MM-DD' periods overlap (or share an endpoint)."""
    a1, a2 = a.split("..")
    b1, b2 = b.split("..")
    return a1 <= b2 and b1 <= a2


@pytest.mark.parametrize("hh,seeded_window", [
    ("HH-1001", "2025-02-10..2025-02-17"),
    ("HH-1002", "2025-02-05..2025-02-12"),
    ("HH-1003", "2025-01-26..2025-02-02"),
])
def test_heatpump_fault_localized(conn, hh, seeded_window):
    hp = [f for f in anomalies.detect_all(conn, hh)
          if f.key == "heatpump_overconsumption"]
    assert hp, f"no heat-pump anomaly detected for {hh}"
    assert _overlaps(hp[0].period, seeded_window), \
        f"{hh}: detected {hp[0].period} does not overlap seeded {seeded_window}"


def test_no_heatpump_anomaly_for_hh1004(conn):
    keys = {f.key for f in anomalies.detect_all(conn, "HH-1004")}
    assert "heatpump_overconsumption" not in keys


@pytest.mark.parametrize("hh,month", [
    ("HH-1001", "2025-08"), ("HH-1002", "2025-08"),
    ("HH-1003", "2025-08"), ("HH-1004", "2025-12"),
])
def test_bill_spike_month(conn, hh, month):
    spikes = [f for f in anomalies.detect_all(conn, hh) if f.key == "bill_spike"]
    assert spikes and spikes[0].period == month
