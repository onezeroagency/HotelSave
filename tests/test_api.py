"""End-to-end API tests for the skeleton: health, auth, and MonitoringJob CRUD."""


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_register_login_me(client):
    r = client.post("/auth/register", json={"email": "a@b.com", "password": "password123"})
    assert r.status_code == 201
    assert r.json()["plan_status"] == "trialing"

    # Duplicate email is rejected.
    dup = client.post("/auth/register", json={"email": "a@b.com", "password": "password123"})
    assert dup.status_code == 409

    token = client.post(
        "/auth/login", data={"username": "a@b.com", "password": "password123"}
    ).json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "a@b.com"


def test_me_requires_auth(client):
    assert client.get("/auth/me").status_code == 401


def _job_payload(**overrides):
    payload = {
        "hotel_name_raw": "Hotel Kaunas",
        "city": "Vilnius",
        "country": "LT",
        "check_in": "2026-09-10",
        "check_out": "2026-09-13",
        "room_type_raw": "Superior Double, City View",
        "board_type": "BB",
        "adults": 2,
        "children": 0,
        "original_price": "420.00",
        "currency": "EUR",
        "original_ota": "Booking.com",
        "refundable": True,
        "cancellation_deadline": "2026-09-07T23:59:00",
    }
    payload.update(overrides)
    return payload


def test_create_and_read_job(auth_client):
    r = auth_client.post("/jobs", json=_job_payload())
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["nights"] == 3  # derived
    assert job["status"] == "active"
    assert job["hotel_id"] == "mock:hotel-kaunas"  # resolved via mock source
    job_id = job["id"]

    got = auth_client.get(f"/jobs/{job_id}")
    assert got.status_code == 200
    assert got.json()["hotel_name_raw"] == "Hotel Kaunas"

    listing = auth_client.get("/jobs")
    assert listing.status_code == 200
    assert len(listing.json()) == 1


def test_create_job_rejects_bad_dates(auth_client):
    r = auth_client.post("/jobs", json=_job_payload(check_out="2026-09-10"))
    assert r.status_code == 422


def test_cancel_job_soft_deletes(auth_client):
    job_id = auth_client.post("/jobs", json=_job_payload()).json()["id"]
    assert auth_client.delete(f"/jobs/{job_id}").status_code == 204
    assert auth_client.get(f"/jobs/{job_id}").json()["status"] == "cancelled"


def test_jobs_are_scoped_to_owner(client):
    # user one creates a job
    client.post("/auth/register", json={"email": "one@x.com", "password": "password123"})
    t1 = client.post(
        "/auth/login", data={"username": "one@x.com", "password": "password123"}
    ).json()["access_token"]
    job_id = client.post(
        "/jobs", json=_job_payload(), headers={"Authorization": f"Bearer {t1}"}
    ).json()["id"]

    # user two cannot see it
    client.post("/auth/register", json={"email": "two@x.com", "password": "password123"})
    t2 = client.post(
        "/auth/login", data={"username": "two@x.com", "password": "password123"}
    ).json()["access_token"]
    r = client.get(f"/jobs/{job_id}", headers={"Authorization": f"Bearer {t2}"})
    assert r.status_code == 404
