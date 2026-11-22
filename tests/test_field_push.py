"""M15 — thông báo đẩy cho lịch hôm nay (docs/design/M15_thong_bao_day.md).

Không bao giờ gọi Expo thật: httpx.MockTransport.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from auth.schemas import CurrentUser
from field import models as field_models
from field import push
from test_field_dispatch_photos_integrations import _case, _send, env  # noqa: F401

OWNER = CurrentUser(id=1, email="u1@example.com", role="user")
TECH = CurrentUser(id=2, email="u2@example.com", role="user")
OTHER_TECH = CurrentUser(id=4, email="u4@example.com", role="user")
TOKEN2 = "ExponentPushToken[tech2-device-0001]"
TOKEN4 = "ExponentPushToken[tech4-device-0001]"


class FakeExpo:
    def __init__(self, monkeypatch, status=200, tickets=None, error=None):
        self.calls: list[httpx.Request] = []
        self.status = status
        self.tickets = tickets
        self.error = error
        monkeypatch.setattr(push, "_transport", httpx.MockTransport(self.handle))

    def handle(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        sent = json.loads(request.content)
        tickets = self.tickets or [{"status": "ok", "id": f"t{i}"} for i in range(len(sent))]
        return httpx.Response(self.status, json={"data": tickets})

    def messages(self) -> list[dict]:
        return [m for call in self.calls for m in json.loads(call.content)]


def _register(client, actor, user, token):
    actor["user"] = user
    assert client.post("/field/push/devices", json={"token": token}).status_code == 204
    actor["user"] = OWNER


def _assign(client, case_id, user_id):
    response = client.patch(f"/field/cases/{case_id}", json={"assigned_to": user_id})
    assert response.status_code == 200, response.text


def _later(seconds=push.GROUP_SECONDS + 1):
    return datetime.now(UTC) + timedelta(seconds=seconds)


def _dispatch(sessions, now=None):
    with sessions() as db:
        return push.dispatch_due(db, now=now or _later())


def _outbox(sessions):
    with sessions() as db:
        return db.scalars(select(field_models.FieldPushOutbox).order_by(field_models.FieldPushOutbox.id)).all()


@pytest.fixture
def team(env):  # noqa: F811
    client, actor, ws, sessions = env
    client.post(f"/field/workspaces/{ws}/members", json={"email": "u4@example.com"})
    _register(client, actor, TECH, TOKEN2)
    _register(client, actor, OTHER_TECH, TOKEN4)
    return client, actor, ws, sessions


# ---------------------------------------------------------------- đăng ký máy (tiêu chí 5)
def test_device_registration_moves_between_users_and_logout_removes_it(env):  # noqa: F811
    client, actor, ws, sessions = env
    assert client.get("/field/push/status").json() == {"enabled": True}
    assert client.post("/field/push/devices", json={"token": "not-a-real-token-xxxxxxxx"}).status_code == 422
    _register(client, actor, TECH, TOKEN2)
    _register(client, actor, TECH, TOKEN2)  # đăng ký lại không tạo bản trùng
    _register(client, actor, OTHER_TECH, TOKEN2)  # máy đổi người dùng
    with sessions() as db:
        devices = db.scalars(select(field_models.FieldPushDevice)).all()
        assert [(d.user_id, d.token) for d in devices] == [(4, TOKEN2)]
    actor["user"] = TECH  # người cũ không xoá được token của người mới
    assert client.post("/field/push/devices/unregister", json={"token": TOKEN2}).status_code == 204
    actor["user"] = OTHER_TECH
    assert client.post("/field/push/devices/unregister", json={"token": TOKEN2}).status_code == 204
    with sessions() as db:
        assert db.scalars(select(field_models.FieldPushDevice)).all() == []


# ---------------------------------------------------------------- tiêu chí 1, 2, 4
def test_assigning_a_case_notifies_only_the_assignee_without_customer_data(team, monkeypatch):
    client, actor, ws, sessions = team
    fake = FakeExpo(monkeypatch)
    case = _case(client, ws, title="Nguyễn Văn A mất mạng", customer_label="Nguyễn Văn A", address="12 Lê Lợi")
    _assign(client, case["id"], 2)
    assert _dispatch(sessions) == {"sent": 1}
    [message] = fake.messages()
    assert message["to"] == TOKEN2 and message["channelId"] == "schedule"
    assert message["body"] == "Bạn có 1 ca mới."
    assert message["data"] == {"screen": "today", "date": None}
    text = json.dumps(message, ensure_ascii=False)
    for secret in ("Nguyễn", "Lê Lợi", "mất mạng"):
        assert secret not in text
    assert set(message) == {"to", "sound", "channelId", "priority", "title", "body", "data"}  # không có mã ca
    assert message["title"] == "FieldOps — ca mới"
    assert "Authorization" not in fake.calls[0].headers
    assert _dispatch(sessions) == {}  # đã gửi thì không gửi lại


def test_no_notification_for_self_assignment_or_users_without_a_device(env, monkeypatch):  # noqa: F811
    client, actor, ws, sessions = env
    FakeExpo(monkeypatch)
    _register(client, actor, OWNER, "ExponentPushToken[owner-device-001]")
    _assign(client, _case(client, ws)["id"], 1)  # trưởng đội tự giao cho mình
    _assign(client, _case(client, ws)["id"], 2)  # thợ 2 chưa bật thông báo
    actor["user"] = TECH  # thợ tự nhận ca chưa có người
    _assign(client, _case(client, ws)["id"], 2)
    assert _outbox(sessions) == []


def test_case_created_already_assigned_notifies(team, monkeypatch):
    client, actor, ws, sessions = team
    fake = FakeExpo(monkeypatch)
    _case(client, ws, assigned_to=2)
    _dispatch(sessions)
    assert [m["to"] for m in fake.messages()] == [TOKEN2]


# ---------------------------------------------------------------- tiêu chí 3 + Q1 (lịch)
def _plan_for_tech(client, ws, day, count=3):
    assert (
        client.patch(
            f"/field/workspaces/{ws}/members/2/dispatch",
            json={"base_latitude": 21.0, "base_longitude": 105.8, "shift_start": "08:00", "shift_end": "17:00"},
        ).status_code
        == 200
    )
    ids = []
    for i in range(count):
        c = _case(client, ws, title=f"Ca {i}")
        client.patch(f"/field/cases/{c['id']}/dispatch", json={"latitude": 21.0 + i / 100, "longitude": 105.8})
        ids.append(c["id"])
    return ids


def _apply(client, ws, day, ids):
    body = {"date": day.isoformat(), "routes": [{"technician_id": 2, "case_ids": ids}]}
    response = client.post(f"/field/workspaces/{ws}/schedule/apply", json=body)
    assert response.status_code == 200, response.text


def test_saving_a_schedule_sends_one_grouped_notification(team, monkeypatch):
    client, actor, ws, sessions = team
    fake = FakeExpo(monkeypatch)
    day = date.today() + timedelta(days=2)
    ids = _plan_for_tech(client, ws, day, count=3)
    _apply(client, ws, day, ids)
    assert len(_outbox(sessions)) == 3
    assert _dispatch(sessions) == {"sent": 1}
    [message] = fake.messages()
    assert message["body"].startswith("Bạn có 3 ca mới. Ca đầu bắt đầu 08:")
    assert message["title"] == f"FieldOps — lịch ngày {day:%d/%m}"
    assert message["data"] == {"screen": "today", "date": day.isoformat()}

    _apply(client, ws, day, ids)  # lưu lại y hệt: không có gì đổi -> không báo
    assert len(_outbox(sessions)) == 3

    _apply(client, ws, day, list(reversed(ids)))  # đổi thứ tự
    _dispatch(sessions)
    assert fake.messages()[-1]["body"].startswith("Lịch của bạn vừa được cập nhật (")
    assert len(fake.messages()) == 2


def test_moving_a_case_tells_both_technicians(team, monkeypatch):
    client, actor, ws, sessions = team
    fake = FakeExpo(monkeypatch)
    case = _case(client, ws)
    _assign(client, case["id"], 2)
    _dispatch(sessions)
    _assign(client, case["id"], 4)
    _dispatch(sessions)
    by_token = {m["to"]: m["body"] for m in fake.messages()[1:]}
    assert by_token == {TOKEN2: "1 ca đã được chuyển cho người khác.", TOKEN4: "Bạn có 1 ca mới."}


def test_assign_then_move_away_within_the_window_sends_nothing(team, monkeypatch):
    client, actor, ws, sessions = team
    fake = FakeExpo(monkeypatch)
    case = _case(client, ws)
    _assign(client, case["id"], 2)
    _assign(client, case["id"], 4)
    summary = _dispatch(sessions)
    assert summary == {"skipped": 1, "sent": 1}
    assert [m["to"] for m in fake.messages()] == [TOKEN4]


def test_rows_queued_inside_the_window_join_the_same_notification(team, monkeypatch):
    client, actor, ws, sessions = team
    fake = FakeExpo(monkeypatch)
    first, second = _case(client, ws), _case(client, ws)
    _assign(client, first["id"], 2)
    with sessions() as db:  # dòng thứ 2 chưa đến hạn nhưng vẫn được gom chung
        db.get(field_models.ServiceCase, second["id"]).assigned_to = 2
        push.enqueue(db, 2, second["id"], "assigned", actor_id=1, now=datetime.now(UTC) + timedelta(seconds=15))
        db.commit()
    _dispatch(sessions)
    _dispatch(sessions, now=_later(60))
    assert [m["body"] for m in fake.messages()] == ["Bạn có 2 ca mới."]


# ---------------------------------------------------------------- lỗi Expo (tiêu chí 6)
def test_expo_errors_retry_then_succeed_and_never_block_assignment(team, monkeypatch):
    client, actor, ws, sessions = team
    fake = FakeExpo(monkeypatch, status=503)
    case = _case(client, ws)
    _assign(client, case["id"], 2)  # giao ca vẫn thành công dù Expo đang lỗi
    assert _dispatch(sessions) == {"retry": 1}
    [row] = _outbox(sessions)
    assert row.status == "pending" and row.attempts == 1 and "HTTP 503" in row.last_error
    assert _dispatch(sessions) == {}  # chưa đến hạn thử lại (30 giây)
    fake.status = 200
    assert _dispatch(sessions, now=_later(90)) == {"sent": 1}
    assert _outbox(sessions)[0].status == "sent"


def test_network_failure_is_retried_and_bad_request_is_dropped(team, monkeypatch):
    client, actor, ws, sessions = team
    fake = FakeExpo(monkeypatch, error=httpx.ConnectTimeout("timeout"))
    _assign(client, _case(client, ws)["id"], 2)
    assert _dispatch(sessions) == {"retry": 1}
    fake.error, fake.status = None, 400
    assert _dispatch(sessions, now=_later(90)) == {"dead": 1}
    assert _outbox(sessions)[0].status == "dead"


def test_gives_up_after_max_attempts(team, monkeypatch):
    client, actor, ws, sessions = team
    FakeExpo(monkeypatch, status=500)
    _assign(client, _case(client, ws)["id"], 2)
    moment = _later()
    for _ in range(push.MAX_ATTEMPTS):
        _dispatch(sessions, now=moment)
        moment += timedelta(hours=2)
    row = _outbox(sessions)[0]
    assert row.status == "dead" and row.attempts == push.MAX_ATTEMPTS


def test_unregistered_device_is_removed(team, monkeypatch):
    client, actor, ws, sessions = team
    FakeExpo(monkeypatch, tickets=[{"status": "error", "details": {"error": "DeviceNotRegistered"}}])
    _assign(client, _case(client, ws)["id"], 2)
    assert _dispatch(sessions) == {"devices_removed": 1, "sent": 1}
    with sessions() as db:
        assert db.scalar(select(field_models.FieldPushDevice).where(field_models.FieldPushDevice.user_id == 2)) is None


def test_old_notifications_are_dropped_after_24_hours(team, monkeypatch):
    client, actor, ws, sessions = team
    fake = FakeExpo(monkeypatch)
    _assign(client, _case(client, ws)["id"], 2)
    assert _dispatch(sessions, now=_later(25 * 3600)) == {"expired": 1}
    assert fake.calls == [] and _outbox(sessions)[0].status == "dead"


def test_disabled_push_queues_nothing(team, monkeypatch):
    client, actor, ws, sessions = team
    monkeypatch.setattr("config.PUSH_ENABLED", False)
    assert client.get("/field/push/status").json() == {"enabled": False}
    _assign(client, _case(client, ws)["id"], 2)
    assert _outbox(sessions) == []


def test_access_token_is_sent_when_configured(team, monkeypatch):
    client, actor, ws, sessions = team
    monkeypatch.setattr("config.EXPO_ACCESS_TOKEN", "expo-test-access")
    fake = FakeExpo(monkeypatch)
    _assign(client, _case(client, ws)["id"], 2)
    _dispatch(sessions)
    assert fake.calls[0].headers["Authorization"] == "Bearer expo-test-access"


def test_closed_case_is_not_announced(team, monkeypatch):
    client, actor, ws, sessions = team
    fake = FakeExpo(monkeypatch)
    case = _case(client, ws)
    _assign(client, case["id"], 2)
    with sessions() as db:
        db.get(field_models.ServiceCase, case["id"]).status = "resolved"
        db.commit()
    assert _dispatch(sessions) == {"skipped": 1} and fake.calls == []


# ---------------------------------------------------------------- CRM + dọn dẹp
def test_crm_case_with_default_assignee_notifies(team, monkeypatch):
    client, actor, ws, sessions = team
    fake = FakeExpo(monkeypatch)
    integration = client.post(
        f"/field/workspaces/{ws}/integrations", json={"name": "CRM", "kind": "crm", "default_assignee": 2}
    ).json()
    body = {
        "event_id": "evt-push-0001",
        "type": "ticket.created",
        "external_ref": "T-1",
        "data": {"title": "Mất mạng", "description": "Khách báo", "customer": "Nguyễn Văn A"},
    }
    assert _send(client, integration, body).status_code == 200
    _dispatch(sessions)
    [message] = fake.messages()
    assert message["to"] == TOKEN2 and "Nguyễn" not in json.dumps(message, ensure_ascii=False)


def test_today_title_and_purge(team, monkeypatch):
    client, actor, ws, sessions = team
    fake = FakeExpo(monkeypatch)
    case = _case(client, ws)
    _assign(client, case["id"], 2)
    tz = timezone(timedelta(minutes=420))
    moment = _later()
    start = (moment.astimezone(tz) + timedelta(minutes=1)).replace(second=0, microsecond=0)
    with sessions() as db:
        db.get(field_models.ServiceCase, case["id"]).scheduled_start = start.astimezone(UTC)
        db.commit()
    _dispatch(sessions, now=moment)
    message = fake.messages()[0]
    if start.date() == moment.astimezone(tz).date():  # bỏ qua phút giao thừa 23:59
        assert message["title"] == "FieldOps — lịch hôm nay"
    assert message["body"] == f"Bạn có 1 ca mới. Ca đầu bắt đầu {start:%H:%M}."
    with sessions() as db:
        assert push.purge_old(db, now=datetime.now(UTC) + timedelta(days=8)) == 1
