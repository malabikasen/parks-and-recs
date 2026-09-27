"""Many parents click Register on the last seat at the same moment. Exactly one must win."""

import threading

from app import domain
from app.db import connect
from conftest import NOW, SWIM2_THU, make_db

N = 20


def test_last_seat_race(tmp_path):
    path = str(tmp_path / "race.db")
    setup = make_db(path)  # Swim L2 Thu has exactly one seat left
    start = setup.execute("SELECT start_date FROM sections WHERE id=?", (SWIM2_THU,)).fetchone()[0]
    racers = []
    for i in range(N):
        hid = setup.execute("INSERT INTO households (name, email) VALUES (?, ?)", (f"Racer {i}", f"r{i}@x.test")).lastrowid
        pid = setup.execute("INSERT INTO participants (household_id, first_name, date_of_birth) VALUES (?, ?, date(?, '-7 years'))",
                            (hid, f"Kid {i}", start)).lastrowid
        racers.append((hid, pid))
    setup.close()

    barrier = threading.Barrier(N)
    results = []

    def attempt(hid, pid):
        conn = connect(path)
        barrier.wait()
        results.extend(domain.register(conn, hid, SWIM2_THU, [pid], NOW))
        conn.close()

    threads = [threading.Thread(target=attempt, args=r) for r in racers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(o.ok for o in results) == 1
    assert {o.code for o in results if not o.ok} == {"SECTION_FULL"}
    check = connect(path)
    enrolled = check.execute("SELECT COUNT(*) FROM registrations WHERE section_id=? AND status='enrolled'",
                             (SWIM2_THU,)).fetchone()[0]
    assert enrolled == 2  # capacity
