import multiprocessing

from fastapi.testclient import TestClient

from app.db import connect, ensure_db
from app.seed import seed
from app.web import create_app
from conftest import AVA, BEN, GARCIA, NOW, SWIM2_THU, SWIM2_TUE

HUGE = 10**20  # > SQLite's 64-bit integer range


def client(tmp_path):
    return TestClient(create_app(db_path=str(tmp_path / "web.db"), clock=lambda: NOW))


def test_pages_render(tmp_path):
    c = client(tmp_path)
    assert c.get("/").status_code == 200
    assert c.get("/staff").status_code == 200
    assert c.get("/api/metrics").status_code == 200


def test_more_people_than_seats_asks_who_gets_them(tmp_path):
    c = client(tmp_path)
    r = c.post(f"/sections/{SWIM2_THU}/register", data={"participant_ids": [AVA, BEN]})
    assert "Only 1 seat left in Swim Level 2 – Thu" in r.text
    assert "Ava is enrolled" not in r.text  # nothing written until the parent chooses

    r = c.post(f"/sections/{SWIM2_THU}/register-split",
               data={"participant_ids": [AVA, BEN], "enroll_ids": [BEN], "waitlist_rest": "true"})
    assert "Ben is enrolled." in r.text
    assert "Ava is #1 on the waitlist" in r.text


def test_split_without_waitlisting_the_rest(tmp_path):
    c = client(tmp_path)
    r = c.post(f"/sections/{SWIM2_THU}/register-split", data={"participant_ids": [AVA, BEN], "enroll_ids": [AVA]})
    assert "Ava is enrolled." in r.text
    assert "on the waitlist" not in r.text.split("my registrations")[1].split("</article>")[0]


def test_household_cookie_switch_and_reset(tmp_path):
    c = client(tmp_path)
    c.post("/household", data={"household_id": GARCIA}, follow_redirects=False)
    assert "Garcia household" in c.get("/").text
    c.post("/demo/reset")
    assert c.get("/api/metrics").json()["seats_freed"] == 0


# ---------------------------------------------------------------- regressions from the adversarial review

def test_huge_ids_are_rejected_not_500(tmp_path):
    c = client(tmp_path)
    assert c.post(f"/registrations/{HUGE}/drop").status_code == 422
    assert c.post(f"/sections/{SWIM2_THU}/register", data={"participant_ids": [HUGE]}).status_code == 422
    assert c.post("/household", data={"household_id": HUGE}).status_code == 422


def test_huge_household_cookie_falls_back_instead_of_breaking_page(tmp_path):
    c = client(tmp_path)
    c.cookies.set("household_id", str(HUGE))
    assert c.get("/").status_code == 200


def test_reset_does_not_redirect_off_site(tmp_path):
    c = client(tmp_path)
    r = c.post("/demo/reset", headers={"referer": "https://evil.example/phish"}, follow_redirects=False)
    assert r.headers["location"] == "/"
    r = c.post("/demo/reset", headers={"referer": "http://testserver/staff"}, follow_redirects=False)
    assert r.headers["location"] == "/staff"


def test_dropping_your_own_seat_is_not_counted_as_seeing_a_full_section(tmp_path):
    c = client(tmp_path)
    c.cookies.set("household_id", str(GARCIA))
    c.get("/")  # creates + seeds the database
    reg_id = connect(str(tmp_path / "web.db")).execute(
        "SELECT r.id FROM registrations r JOIN participants p ON p.id = r.participant_id "
        "WHERE p.household_id = ? AND r.section_id = ?", (GARCIA, SWIM2_TUE)).fetchone()[0]
    c.post(f"/registrations/{reg_id}/drop")
    tue = next(s for s in c.get("/api/metrics").json()["sections"] if s["id"] == SWIM2_TUE)
    assert tue["join_rate"] == 1.0  # was 0.5: Garcia's drop counted as "saw full"


def _init(path):
    ensure_db(path, lambda conn: seed(conn, NOW))


def test_ensure_db_is_safe_across_processes(tmp_path):
    path = str(tmp_path / "multi.db")
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_init, args=(path,)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    assert [p.exitcode for p in procs] == [0, 0, 0, 0]
    assert connect(path).execute("SELECT COUNT(*) FROM households").fetchone()[0] == 5
