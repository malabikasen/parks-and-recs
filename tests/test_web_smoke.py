from fastapi.testclient import TestClient

from app.web import create_app
from conftest import AVA, BEN, NOW, SWIM2_THU


def client(tmp_path):
    return TestClient(create_app(db_path=str(tmp_path / "web.db"), clock=lambda: NOW))


def test_pages_render_and_register_flow(tmp_path):
    c = client(tmp_path)
    assert c.get("/").status_code == 200
    assert c.get("/staff").status_code == 200

    r = c.post(f"/sections/{SWIM2_THU}/register", data={"participant_ids": [AVA, BEN]})
    assert r.status_code == 200
    assert "Ava is enrolled." in r.text
    assert "No seat left for Ben" in r.text


def test_household_cookie_switch_and_reset(tmp_path):
    c = client(tmp_path)
    c.post("/household", data={"household_id": 3}, follow_redirects=False)
    assert "Garcia household" in c.get("/").text
    c.post(f"/sections/{SWIM2_THU}/register", data={"participant_ids": [AVA]})  # not Garcia's child
    c.post("/demo/reset")
    assert c.get("/api/metrics").json()["seats_freed"] == 0
