"""Bandwidth routes: seeded sqlite rows, range totals, monthly fill, quotas.

Rows are seeded straight into the panel's sqlite (second connection to the
same file the running app uses), in 2025 — safely away from anything the live
accumulator books for "today".
"""

import sqlite3

import pytest
from conftest import STATE_DIR

GiB = 1024**3

SEED = [
    # (vmid, date, bytes_in, bytes_out)
    (105, "2025-03-01", 100, 50),
    (105, "2025-03-02", 200, 100),
    (105, "2025-03-03", 400, 150),
    (105, "2025-06-10", 7, 3),
    # vmid 115 has a 500 GB/month quota from HLIDSKJALF_BANDWIDTH_QUOTAS
    (115, "2025-05-01", 100 * GiB, 150 * GiB),
]


@pytest.fixture(scope="module", autouse=True)
def seed_rows(client):
    conn = sqlite3.connect(f"{STATE_DIR}/hlidskjalf.sqlite3")
    with conn:
        conn.executemany(
            """INSERT INTO bandwidth (vmid, date, bytes_in, bytes_out)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (vmid, date) DO UPDATE SET
                 bytes_in = excluded.bytes_in, bytes_out = excluded.bytes_out""",
            SEED,
        )
    conn.close()


def test_range_totals(auth_client):
    r = auth_client.get("/api/vms/105/bandwidth?from=2025-03-01&to=2025-03-02")
    assert r.status_code == 200
    body = r.json()
    assert body["from"] == "2025-03-01" and body["to"] == "2025-03-02"
    assert [d["date"] for d in body["days"]] == ["2025-03-01", "2025-03-02"]
    assert body["totals"] == {"bytes_in": 300, "bytes_out": 150, "total": 450}
    # no quota configured for 105
    assert body["quota_gb"] is None
    assert body["utilization"] is None


def test_range_bad_dates_400(auth_client):
    r = auth_client.get("/api/vms/105/bandwidth?from=notadate&to=2025-03-02")
    assert r.status_code == 400


def test_monthly_fills_all_12_months(auth_client):
    r = auth_client.get("/api/vms/105/bandwidth/monthly?year=2025")
    assert r.status_code == 200
    body = r.json()
    assert body["year"] == 2025
    months = body["months"]
    assert [m["month"] for m in months] == list(range(1, 13))
    by_month = {m["month"]: m for m in months}
    assert by_month[3] == {"month": 3, "bytes_in": 700, "bytes_out": 300}
    assert by_month[6] == {"month": 6, "bytes_in": 7, "bytes_out": 3}
    for m in (1, 2, 4, 5, 7, 8, 9, 10, 11, 12):
        assert by_month[m]["bytes_in"] == 0 and by_month[m]["bytes_out"] == 0


def test_summary_shape(auth_client):
    r = auth_client.get("/api/bandwidth/summary?month=2025-03")
    assert r.status_code == 200
    body = r.json()
    assert body["month"] == "2025-03"
    assert body["vms"] == {
        "105": {"bytes_in": 700, "bytes_out": 300, "total": 1000}
    }


def test_summary_bad_month_400(auth_client):
    assert auth_client.get("/api/bandwidth/summary?month=2025-3").status_code == 400


def test_quota_utilization_math(auth_client):
    r = auth_client.get("/api/vms/115/bandwidth?from=2025-05-01&to=2025-05-31")
    assert r.status_code == 200
    body = r.json()
    assert body["quota_gb"] == 500  # from HLIDSKJALF_BANDWIDTH_QUOTAS={"115": 500}
    assert body["totals"]["total"] == 250 * GiB
    # 250 GiB used of 500 GiB → 50%
    assert body["utilization"] == pytest.approx(0.5)
