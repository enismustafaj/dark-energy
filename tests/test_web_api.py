from fastapi.testclient import TestClient

from energyintelligence.web.app import app


def test_households_api_returns_seeded_homes(db_path):
    client = TestClient(app)

    response = client.get("/api/households")

    assert response.status_code == 200
    data = response.json()
    assert [home["household_id"] for home in data["households"]] == [
        "HH-1001",
        "HH-1002",
        "HH-1003",
        "HH-1004",
        "HH-2001",
        "HH-2002",
    ]


def test_household_view_api_returns_client_payload(db_path):
    client = TestClient(app)

    response = client.get("/api/households/HH-1001/view")

    assert response.status_code == 200
    data = response.json()
    assert data["household"]["household_id"] == "HH-1001"
    assert data["hub"]["annual_cost_eur"] > 0
    assert data["nodes"]
    assert data["advice"]
    assert all(item["status"] == "open" for item in data["advice"])
    assert all("agent_actionable" in item for item in data["advice"])
    assert any(item["agent_actionable"] for item in data["advice"])
    assert any(not item["agent_actionable"] for item in data["advice"])
    # agent_actionable is true only for direct device controls; assert the mapping
    # for whichever of these actions are present in the open advice (applied advice
    # is filtered out of the live list, so not every action is guaranteed to show).
    by_action = {item["action_type"]: item["agent_actionable"] for item in data["advice"]}
    expected_actionable = {
        "shift_heatpump_to_cheap_window": True,
        "book_maintenance": False,
        "suggest_tariff_switch": False,
    }
    for action_type, expected in expected_actionable.items():
        if action_type in by_action:
            assert by_action[action_type] is expected
    # Applied advice never appears in the open list.
    applied_keys = {a["fact_key"] for a in data["applied_advice"]}
    assert all(item["fact_key"] not in applied_keys for item in data["advice"])
    # Realized savings from applied-advice history are part of the payload.
    assert data["realized_savings_eur"] > 0
    assert data["applied_advice"]
    assert sum(a["benefit_eur"] for a in data["applied_advice"]) == data["realized_savings_eur"]
    # Each advice carries its grounded numbers + an optional viz payload the UI
    # renders as a chart under "How this works".
    assert all("numbers" in item and isinstance(item["numbers"], dict) for item in data["advice"])
    bill_spike = next((i for i in data["advice"] if i["fact_key"] == "bill_spike"), None)
    if bill_spike is not None:
        assert bill_spike["viz"] and bill_spike["viz"]["kind"] == "monthly_bills"
        assert bill_spike["viz"]["series"]


def test_completed_action_resolves_recommendation_in_view(db_path):
    client = TestClient(app)

    before = client.get("/api/households/HH-1001/view")
    assert before.status_code == 200
    item = next(entry for entry in before.json()["advice"] if entry["action_type"])

    action = client.post(
        f"/api/actions/{item['action_type']}?household_id=HH-1001",
        json={"recommendation_fact_key": item["fact_key"]},
    )
    assert action.status_code == 200

    after = client.get("/api/households/HH-1001/view")
    assert after.status_code == 200
    advice = after.json()["advice"]
    assert all(entry["fact_key"] != item["fact_key"] for entry in advice)


def test_resolve_api_resolves_manual_recommendation_in_view(db_path):
    client = TestClient(app)

    # Uses HH-1002 (which has several manual recommendations) to avoid contending
    # with test_completed_action_resolves_recommendation_in_view over HH-1001's
    # advice — the seeded DB is shared across the session.
    before = client.get("/api/households/HH-1002/view")
    assert before.status_code == 200
    item = next(entry for entry in before.json()["advice"] if not entry["agent_actionable"])

    response = client.patch(
        f"/api/advice/HH-1002/{item['fact_key']}",
        json={"status": "resolved"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "resolved"

    after = client.get("/api/households/HH-1002/view")
    assert after.status_code == 200
    assert all(entry["fact_key"] != item["fact_key"] for entry in after.json()["advice"])


def test_chat_api_returns_fallback_without_openai_key(db_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(app)

    response = client.post(
        "/api/chat/HH-1001",
        json={"message": "What should I do next?", "messages": []},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "fallback"
    assert data["message"]
