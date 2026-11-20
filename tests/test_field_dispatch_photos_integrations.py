"""Field photos, route dispatch and CRM/NMS integrations."""

from __future__ import annotations

import io
import json
import time
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth.models import User
from auth.schemas import CurrentUser
from chat import models as chat_models  # noqa: F401
from database import Base, get_db
from dependencies import get_current_user
from field import dispatch, integrations, photos
from field import models as field_models
from field.router import router as field_router


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("config.FIELD_PHOTO_DIR", tmp_path / "photos")
    monkeypatch.setattr("config.API_PUBLIC_URL", "http://testserver")
    monkeypatch.setenv("FIELD_WEBHOOK_INLINE", "0")
    monkeypatch.setenv("FIELD_WEBHOOK_ALLOW_PRIVATE", "1")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with sessions() as db:
        db.add_all([User(id=i, email=f"u{i}@example.com", hashed_password="x", role="user") for i in (1, 2, 3, 4)])
        db.commit()
    app = FastAPI()
    for r in (field_router, photos.router, dispatch.router, integrations.router, integrations.public_router):
        app.include_router(r)
    actor = {"user": CurrentUser(id=1, email="u1@example.com", role="user")}

    def override_db():
        db = sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: actor["user"]
    with TestClient(app) as client:
        ws = client.get("/field/workspaces").json()[0]["id"]
        client.post(f"/field/workspaces/{ws}/members", json={"email": "u2@example.com"})
        yield client, actor, ws, sessions
    engine.dispose()


def _case(client, ws, title="Mất mạng", **extra):
    return client.post(f"/field/workspaces/{ws}/cases", json={"title": title, "symptom": "Đèn LOS đỏ", **extra}).json()


def _jpeg(size=(1200, 800), with_gps=True):
    from PIL import Image

    image = Image.new("RGB", size, (200, 30, 30))
    buf = io.BytesIO()
    exif = Image.Exif()
    if with_gps:
        exif[0x8825] = {1: "N", 2: (21.0, 1.0, 30.0), 3: "E", 4: (105.0, 50.0, 0.0)}
        exif[0x0110] = "SecretPhone 15"
    exif[0x0112] = 6  # orientation: rotate 90° when displayed
    image.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


# ----------------------------------------------------------------------------- photos
def test_photo_upload_strips_metadata_and_serves_signed_url(env):
    client, actor, ws, _ = env
    case = _case(client, ws)
    res = client.post(
        f"/field/cases/{case['id']}/photos",
        files={"file": ("a.jpg", _jpeg(), "image/jpeg")},
        data={"caption": "Đầu ONT", "client_action_id": "photo-abc-123"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert (body["width"], body["height"]) == (800, 1200)  # EXIF orientation applied
    again = client.post(
        f"/field/cases/{case['id']}/photos",
        files={"file": ("a.jpg", _jpeg(), "image/jpeg")},
        data={"client_action_id": "photo-abc-123"},
    )
    assert again.json()["id"] == body["id"]  # offline retry is idempotent
    url = body["url"].replace("http://testserver", "")
    image = client.get(url)
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    from PIL import Image

    stored = Image.open(io.BytesIO(image.content))
    assert not stored.getexif()  # no GPS, no camera model
    assert client.get(url.replace("sig=", "sig=0")).status_code in (403, 422)
    expired = photos.signed_url(body["id"], "full", now=time.time() - 3600).replace("http://testserver", "")
    assert client.get(expired).status_code == 403
    assert len(client.get(f"/field/cases/{case['id']}/photos").json()) == 1


def test_photo_rejects_fake_images_and_foreign_users(env):
    client, actor, ws, _ = env
    case = _case(client, ws)
    bad = client.post(f"/field/cases/{case['id']}/photos", files={"file": ("x.jpg", b"<?php echo 1; ?>", "image/jpeg")})
    assert bad.status_code == 422
    actor["user"] = CurrentUser(id=3, email="u3@example.com", role="user")  # not a member
    assert (
        client.post(
            f"/field/cases/{case['id']}/photos", files={"file": ("a.jpg", _jpeg(with_gps=False), "image/jpeg")}
        ).status_code
        == 404
    )


def test_photo_delete_rights_and_retention(env):
    client, actor, ws, sessions = env
    case = _case(client, ws)
    pid = client.post(f"/field/cases/{case['id']}/photos", files={"file": ("a.jpg", _jpeg(), "image/jpeg")}).json()[
        "id"
    ]
    actor["user"] = CurrentUser(id=2, email="u2@example.com", role="user")  # technician, not uploader
    assert client.delete(f"/field/photos/{pid}").status_code == 403
    actor["user"] = CurrentUser(id=1, email="u1@example.com", role="user")
    client.patch(f"/field/cases/{case['id']}", json={"status": "resolved"})
    with sessions() as db:
        assert photos.purge_expired_photos(db, now=datetime.now(UTC) + timedelta(days=400)) == 1
        assert db.query(field_models.ServiceCasePhoto).count() == 0


# ----------------------------------------------------------------------------- dispatch
def test_planner_respects_windows_and_shifts():
    techs = [dispatch.Tech(1, 21.03, 105.85, 8 * 60, 17 * 60), dispatch.Tech(2, 21.00, 105.80, 8 * 60, 12 * 60)]
    jobs = [
        dispatch.Job(10, 21.04, 105.86, 60, "normal"),
        dispatch.Job(11, 21.01, 105.81, 45, "urgent", earliest=9 * 60, latest=10 * 60),
        dispatch.Job(12, 21.05, 105.84, 60, "low", earliest=14 * 60, latest=16 * 60),
        dispatch.Job(13, 21.02, 105.83, 600, "normal"),  # longer than any shift
    ]
    plan = dispatch.plan_routes(techs, jobs, 30)
    placed = {s.job_id: (tech, s) for tech, stops in plan.routes.items() for s in stops}
    assert set(placed) == {10, 11, 12} and 13 in plan.unassigned
    assert 9 * 60 <= placed[11][1].start <= 10 * 60
    assert 14 * 60 <= placed[12][1].start <= 16 * 60
    for tech, stops in plan.routes.items():
        assert all(stops[i].end <= stops[i + 1].start for i in range(len(stops) - 1))
        shift_end = next(t.end for t in techs if t.id == tech)
        assert stops[-1].end <= shift_end


def test_urgent_case_goes_first_even_if_slightly_farther():
    tech = dispatch.Tech(1, 21.03, 105.85, 8 * 60, 17 * 60)
    near = [dispatch.Job(i, 21.03 + 0.003 * i, 105.85, 45, "low") for i in range(1, 5)]
    urgent = dispatch.Job(99, 21.06, 105.85, 45, "urgent")
    plan = dispatch.plan_routes([tech], near + [urgent], 30)
    assert plan.routes[1][0].job_id == 99


def test_two_opt_untangles_a_crossed_route():
    tech = dispatch.Tech(1, 0.0, 0.0, 0, 24 * 60)
    pts = [(0.0, 0.1), (0.1, 0.1), (0.1, 0.0), (0.0, 0.2), (0.2, 0.2)]
    jobs = [dispatch.Job(i, lat, lng, 5) for i, (lat, lng) in enumerate(pts)]
    crossed = dispatch._simulate(tech, [jobs[0], jobs[2], jobs[1], jobs[4], jobs[3]], 30)
    better = dispatch._simulate(tech, dispatch._two_opt(tech, [jobs[0], jobs[2], jobs[1], jobs[4], jobs[3]], 30), 30)
    assert dispatch._travel(better) < dispatch._travel(crossed)


def test_schedule_plan_apply_and_day_view(env):
    client, actor, ws, _ = env
    for uid, lat, lng in ((1, 21.03, 105.85), (2, 21.00, 105.80)):
        assert (
            client.patch(
                f"/field/workspaces/{ws}/members/{uid}/dispatch",
                json={"base_latitude": lat, "base_longitude": lng, "shift_start": "08:00", "shift_end": "17:00"},
            ).status_code
            == 200
        )
    day = date.today() + timedelta(days=1)
    tz = timezone(timedelta(hours=7))
    ids = []
    for i, (lat, lng) in enumerate(((21.04, 105.86), (21.01, 105.81), (21.02, 105.84))):
        c = _case(client, ws, title=f"Ca {i}")
        payload = {"latitude": lat, "longitude": lng, "est_minutes": 45, "priority": "high" if i == 1 else "normal"}
        if i == 2:
            payload |= {
                "window_start": datetime.combine(day, datetime.min.time(), tz).replace(hour=13).isoformat(),
                "window_end": datetime.combine(day, datetime.min.time(), tz).replace(hour=15).isoformat(),
            }
        assert client.patch(f"/field/cases/{c['id']}/dispatch", json=payload).status_code == 200
        ids.append(c["id"])
    no_location = _case(client, ws, title="Không toạ độ")["id"]
    plan = client.post(f"/field/workspaces/{ws}/schedule/plan", json={"date": day.isoformat()}).json()
    planned = [s["case_id"] for r in plan["routes"] for s in r["stops"]]
    assert sorted(planned) == sorted(ids)
    assert {u["case_id"]: u["reason"] for u in plan["unassigned"]}[no_location] == "chưa có toạ độ"
    routes = [
        {"technician_id": r["technician_id"], "case_ids": [s["case_id"] for s in r["stops"]]} for r in plan["routes"]
    ]
    applied = client.post(f"/field/workspaces/{ws}/schedule/apply", json={"date": day.isoformat(), "routes": routes})
    assert applied.status_code == 200 and sorted(applied.json()["scheduled"]) == sorted(ids)
    view = client.get(f"/field/workspaces/{ws}/schedule", params={"date": day.isoformat()}).json()
    assert sum(len(r["stops"]) for r in view["routes"]) == 3
    # infeasible manual edit is refused (window case moved to a technician whose day is full)
    bad = [{"technician_id": 2, "case_ids": ids * 1}, {"technician_id": 1, "case_ids": ids[:1]}]
    assert (
        client.post(f"/field/workspaces/{ws}/schedule/apply", json={"date": day.isoformat(), "routes": bad}).status_code
        == 422
    )
    actor["user"] = CurrentUser(id=2, email="u2@example.com", role="user")
    assert client.post(f"/field/workspaces/{ws}/schedule/plan", json={"date": day.isoformat()}).status_code == 403


# ----------------------------------------------------------------------------- integrations
def _send(client, integration, body: dict, ts=None, secret=None):
    raw = json.dumps(body).encode()
    ts = str(ts or int(time.time()))
    sig = integrations.sign(secret or integration["secret"], ts, raw)
    path = integration["inbound_url"].replace("http://testserver", "")
    return client.post(
        path,
        content=raw,
        headers={"X-FieldOps-Timestamp": ts, "X-FieldOps-Signature": sig, "Content-Type": "application/json"},
    )


def test_inbound_crm_ticket_lifecycle_is_signed_and_idempotent(env):
    client, actor, ws, _ = env
    integ = client.post(
        f"/field/workspaces/{ws}/integrations", json={"name": "CRM chính", "kind": "crm", "default_assignee": 2}
    ).json()
    assert "secret" in integ and "secret" not in client.get(f"/field/workspaces/{ws}/integrations").json()[0]
    event = {
        "event_id": "e1",
        "type": "ticket.created",
        "external_ref": "TCK-9",
        "data": {
            "title": "Khách báo chập chờn",
            "description": "Wifi rớt liên tục",
            "priority": "high",
            "latitude": 21.0,
            "longitude": 105.8,
            "address": "12 Láng Hạ",
        },
    }
    first = _send(client, integ, event)
    assert first.status_code == 200 and first.json()["outcome"] == "created"
    case_id = first.json()["case_id"]
    assert _send(client, integ, event).json()["duplicate"] is True
    assert _send(client, integ, event, secret="wrong").status_code == 401
    assert _send(client, integ, {**event, "event_id": "e2"}, ts=int(time.time()) - 3600).status_code == 401
    case = client.get(f"/field/cases/{case_id}").json()
    assert case["assigned_to"] == 2 and case["priority"] == "high" and case["external_ref"] == "TCK-9"
    _send(
        client,
        integ,
        {"event_id": "e3", "type": "ticket.updated", "external_ref": "TCK-9", "data": {"address": "14 Láng Hạ"}},
    )
    assert client.get(f"/field/cases/{case_id}").json()["address"] == "14 Láng Hạ"
    _send(client, integ, {"event_id": "e4", "type": "ticket.closed", "external_ref": "TCK-9", "data": {}})
    assert client.get(f"/field/cases/{case_id}").json()["status"] == "resolved"


def test_nms_alarm_cleared_only_autocloses_untouched_cases(env):
    client, actor, ws, _ = env
    integ = client.post(f"/field/workspaces/{ws}/integrations", json={"name": "NMS", "kind": "nms"}).json()

    def raise_alarm(ref, eid):
        return _send(
            client,
            integ,
            {
                "event_id": eid,
                "type": "alarm.raised",
                "external_ref": ref,
                "data": {"title": f"LOS tại {ref}", "description": "Mất quang", "severity": "critical"},
            },
        ).json()

    a = raise_alarm("OLT1/PON3", "a1")
    b = raise_alarm("OLT1/PON4", "a2")
    assert client.get(f"/field/cases/{a['case_id']}").json()["priority"] == "urgent"
    client.patch(f"/field/cases/{b['case_id']}", json={"status": "in_progress"})
    for ref, eid in (("OLT1/PON3", "c1"), ("OLT1/PON4", "c2")):
        _send(client, integ, {"event_id": eid, "type": "alarm.cleared", "external_ref": ref, "data": {}})
    assert client.get(f"/field/cases/{a['case_id']}").json()["status"] == "resolved"
    assert client.get(f"/field/cases/{b['case_id']}").json()["status"] == "in_progress"


class _FakeClient:
    def __init__(self, codes):
        self.codes, self.calls = list(codes), []

    def post(self, url, content, headers):
        self.calls.append((url, content, headers))
        code = self.codes.pop(0) if self.codes else 200

        class R:
            status_code = code

        return R()


def test_outbound_webhooks_retry_then_deliver_or_dead_letter(env):
    client, actor, ws, sessions = env
    integ = client.post(
        f"/field/workspaces/{ws}/integrations",
        json={"name": "CRM", "kind": "crm", "outbound_url": "http://crm.local/hook"},
    ).json()
    created = _send(
        client,
        integ,
        {
            "event_id": "x1",
            "type": "ticket.created",
            "external_ref": "T-1",
            "data": {"title": "Đứt cáp", "description": "Cáp bị cắt"},
        },
    ).json()
    client.patch(f"/field/cases/{created['case_id']}", json={"status": "in_progress"})
    with sessions() as db:
        fake = _FakeClient([500])
        assert integrations.dispatch_due(db, client=fake) == {"retry": 1}
        url, body, headers = fake.calls[0]
        assert headers["X-FieldOps-Signature"] == integrations.sign(
            integ["secret"], headers["X-FieldOps-Timestamp"], body
        )
        assert json.loads(body)["case"]["status"] == "in_progress"
        later = datetime.now(UTC) + timedelta(hours=1)
        assert integrations.dispatch_due(db, client=_FakeClient([204]), now=later) == {"delivered": 1}
    client.patch(f"/field/cases/{created['case_id']}", json={"status": "resolved"})
    with sessions() as db:
        moment = datetime.now(UTC)
        for _ in range(integrations.MAX_ATTEMPTS):
            moment += timedelta(hours=7)
            integrations.dispatch_due(db, client=_FakeClient([503]), now=moment)
        dead = db.query(field_models.FieldWebhookDelivery).filter_by(status="dead").count()
    assert dead == 1
    listed = client.get(f"/field/integrations/{integ['id']}/deliveries", params={"status": "dead"}).json()
    assert (
        client.post(f"/field/integrations/{integ['id']}/deliveries/{listed[0]['id']}/retry").json()["status"]
        == "pending"
    )


def test_outbound_url_must_be_public_https(monkeypatch):
    monkeypatch.delenv("FIELD_WEBHOOK_ALLOW_PRIVATE", raising=False)
    for url in ("http://example.com/hook", "https://127.0.0.1/hook", "https://10.0.0.5/x", "https://localhost/x"):
        with pytest.raises(ValueError):
            integrations.validate_outbound_url(url)
