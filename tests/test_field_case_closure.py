"""M12 — biên bản đóng ca (docs/PRD.md §5.1, docs/design/M12_bien_ban_dong_ca.md)."""

from __future__ import annotations

import json

from auth.schemas import CurrentUser
from field import models as field_models
from test_field_dispatch_photos_integrations import _case, _jpeg, _send, env  # noqa: F401

OWNER = CurrentUser(id=1, email="u1@example.com", role="user")
TECH = CurrentUser(id=2, email="u2@example.com", role="user")
OTHER_TECH = CurrentUser(id=4, email="u4@example.com", role="user")
OUTSIDER = CurrentUser(id=3, email="u3@example.com", role="user")

# Một chữ ký: hai nét đủ dài trong khung 3:1 (toạ độ chuẩn hoá 0..1).
SIGNATURE = [[[0.1, 0.5], [0.2, 0.3], [0.3, 0.7], [0.4, 0.4]], [[0.5, 0.5], [0.8, 0.5]]]


def close_case(client, case_id, **overrides):
    """Dùng chung cho các test khác cần một ca đã đóng đúng cách (M12)."""
    body = {"outcome_code": "fixed", "note": "Thay dây nhảy quang, đèn LOS hết đỏ", "signature": SIGNATURE}
    body.update(overrides)
    return client.post(f"/field/cases/{case_id}/close", json=body)


def _assign(sessions, case_id, user_id):
    with sessions() as db:
        db.get(field_models.ServiceCase, case_id).assigned_to = user_id
        db.commit()


def _closures(sessions, case_id):
    with sessions() as db:
        return db.query(field_models.FieldCaseClosure).filter_by(case_id=case_id).order_by("id").all()


# ---------------------------------------------------------------- tiêu chí 1 + 2
def test_closing_with_outcome_note_and_signature_records_a_report(env):  # noqa: F811
    client, actor, ws, sessions = env
    case = _case(client, ws)
    _assign(sessions, case["id"], 2)
    actor["user"] = TECH
    response = close_case(client, case["id"], customer_name="Nguyễn Văn A")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "resolved"
    closure = body["closure"]
    assert closure["outcome_code"] == "fixed" and closure["outcome_label"] == "Đã sửa xong"
    assert closure["signed"] is True and closure["customer_name"] == "Nguyễn Văn A" and closure["closed_by"] == 2
    assert closure["signature_url"].startswith("http://testserver/field/photos/")
    image = client.get(closure["signature_url"].replace("http://testserver", ""))
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert len(_closures(sessions, case["id"])) == 1
    events = [e for e in body["events"] if e["event_type"] == "closed"]
    assert len(events) == 1 and "Đã sửa xong" in events[0]["detail"] and "Nguyễn Văn A" in events[0]["detail"]


def test_missing_outcome_note_or_signature_keeps_the_case_open(env):  # noqa: F811
    client, actor, ws, sessions = env
    case = _case(client, ws)
    assert close_case(client, case["id"], outcome_code="bogus").status_code == 422
    assert close_case(client, case["id"], note="  ").status_code == 422
    assert close_case(client, case["id"], signature=None).status_code == 422
    assert close_case(client, case["id"], signature=[[[0.5, 0.5], [0.51, 0.5]]]).status_code == 422  # một chấm
    assert close_case(client, case["id"], signature=[[[0.1, 0.1]] * 5000]).status_code == 422  # quá nhiều điểm
    assert client.get(f"/field/cases/{case['id']}").json()["status"] == "open"
    assert _closures(sessions, case["id"]) == []


# ---------------------------------------------------------------- Q2: khách vắng / từ chối
def test_absent_customer_needs_a_photo_instead_of_a_signature(env):  # noqa: F811
    client, actor, ws, sessions = env
    case = _case(client, ws)
    absent = {"outcome_code": "customer_absent", "note": "Gọi 3 lần không ai mở cửa", "signature": None}
    denied = close_case(client, case["id"], **absent)
    assert denied.status_code == 422 and "ảnh" in denied.json()["detail"]
    client.post(f"/field/cases/{case['id']}/photos", files={"file": ("door.jpg", _jpeg(), "image/jpeg")})
    closed = close_case(client, case["id"], **absent)
    assert closed.status_code == 200, closed.text
    assert closed.json()["closure"]["signed"] is False and closed.json()["closure"]["signature_url"] is None


# ---------------------------------------------------------------- Q3: ai được đóng, đường cũ bị chặn
def test_only_the_assigned_technician_or_the_owner_can_close(env):  # noqa: F811
    client, actor, ws, sessions = env
    client.post(f"/field/workspaces/{ws}/members", json={"email": "u4@example.com"})
    case = _case(client, ws)
    _assign(sessions, case["id"], 2)
    actor["user"] = OTHER_TECH
    assert close_case(client, case["id"]).status_code == 403
    actor["user"] = OUTSIDER
    assert close_case(client, case["id"]).status_code == 404
    actor["user"] = OWNER
    assert close_case(client, case["id"]).status_code == 200


def test_the_old_direct_resolve_is_refused_but_crm_can_still_close(env):  # noqa: F811
    client, actor, ws, sessions = env
    case = _case(client, ws)
    refused = client.patch(f"/field/cases/{case['id']}", json={"status": "resolved"})
    assert refused.status_code == 409 and "biên bản" in refused.json()["detail"]
    integration = client.post(f"/field/workspaces/{ws}/integrations", json={"name": "CRM", "kind": "crm"}).json()
    ticket = {"event_id": "m12-1", "type": "ticket.created", "external_ref": "T-12", "data": {"title": "Mất mạng", "description": "Khách báo mất mạng"}}
    crm_case = _send(client, integration, ticket).json()["case_id"]
    _send(client, integration, {"event_id": "m12-2", "type": "ticket.closed", "external_ref": "T-12", "data": {}})
    closed = client.get(f"/field/cases/{crm_case}").json()
    assert closed["status"] == "resolved" and closed["closure"] is None


# ---------------------------------------------------------------- offline, mở lại, xoá ảnh
def test_offline_replay_creates_one_report_and_one_signature(env):  # noqa: F811
    client, actor, ws, sessions = env
    case = _case(client, ws)
    first = close_case(client, case["id"], client_action_id="m12-offline-0001")
    again = close_case(client, case["id"], client_action_id="m12-offline-0001")
    assert first.status_code == again.status_code == 200
    assert len(_closures(sessions, case["id"])) == 1
    with sessions() as db:
        assert db.query(field_models.ServiceCasePhoto).filter_by(case_id=case["id"]).count() == 1


def test_reopen_and_close_again_keeps_history_and_shows_the_latest(env):  # noqa: F811
    client, actor, ws, sessions = env
    case = _case(client, ws)
    close_case(client, case["id"])
    assert close_case(client, case["id"]).status_code == 409  # đã đóng
    reopened = client.patch(f"/field/cases/{case['id']}", json={"status": "in_progress"}).json()
    assert reopened["closure"] is None
    latest = close_case(client, case["id"], outcome_code="replaced").json()
    assert latest["closure"]["outcome_code"] == "replaced"
    assert [c.outcome_code for c in _closures(sessions, case["id"])] == ["fixed", "replaced"]


def test_signature_cannot_be_deleted_like_an_ordinary_photo_but_expires_with_photos(env):  # noqa: F811
    from datetime import UTC, datetime, timedelta

    from field.photos import purge_expired_photos

    client, actor, ws, sessions = env
    case = _case(client, ws)
    photo_id = close_case(client, case["id"]).json()["closure"]["signature_photo_id"]
    assert client.delete(f"/field/photos/{photo_id}").status_code == 409
    with sessions() as db:
        assert purge_expired_photos(db, now=datetime.now(UTC) + timedelta(days=4000)) == 1
    closure = client.get(f"/field/cases/{case['id']}").json()["closure"]
    assert closure["signed"] is True and closure["signature_url"] is None  # vẫn ghi nhận đã ký


# ---------------------------------------------------------------- Q4: CRM
def test_crm_gets_the_outcome_but_never_the_signature_image(env):  # noqa: F811
    client, actor, ws, sessions = env
    integration = client.post(
        f"/field/workspaces/{ws}/integrations",
        json={"name": "CRM", "kind": "crm", "outbound_url": "http://crm.local/hook"},
    ).json()
    ticket = {"event_id": "m12-3", "type": "ticket.created", "external_ref": "T-13", "data": {"title": "Mất mạng", "description": "Khách báo mất mạng"}}
    case_id = _send(client, integration, ticket).json()["case_id"]
    close_case(client, case_id, customer_name="Khách B")
    manual = _case(client, ws)
    close_case(client, manual["id"])
    with sessions() as db:
        rows = db.query(field_models.FieldWebhookDelivery).filter_by(event_type="case.status_changed").all()
    assert len(rows) == 1
    payload = json.loads(rows[0].payload_json)
    assert payload["case"]["status"] == "resolved"
    assert payload["closure"] == {
        "outcome_code": "fixed",
        "outcome_label": "Đã sửa xong",
        "note": "Thay dây nhảy quang, đèn LOS hết đỏ",
        "signed": True,
        "customer_name": "Khách B",
    }
    assert "photos" not in rows[0].payload_json and "signature_url" not in rows[0].payload_json
