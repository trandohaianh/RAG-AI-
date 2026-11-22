"""M14 — trưởng đội sửa thông tin ca (docs/PRD.md §5.1, docs/design/M14_sua_thong_tin_ca.md).

Mỗi test ứng với một tiêu chí nghiệm thu hoặc một quyết định đã duyệt (Q1–Q4)."""

from __future__ import annotations

import json

from auth.schemas import CurrentUser
from field import models as field_models
from test_field_dispatch_photos_integrations import _case, _send, env  # noqa: F401

OWNER = CurrentUser(id=1, email="u1@example.com", role="user")
TECH = CurrentUser(id=2, email="u2@example.com", role="user")
OUTSIDER = CurrentUser(id=3, email="u3@example.com", role="user")

NEW = {
    "title": "Mất mạng toàn khu",
    "symptom": "Đèn LOS đỏ, khách báo mất mạng từ sáng; tổng đài đã hướng dẫn khởi động lại",
    "customer_label": "Nguyễn Văn A",
    "address": "21 Lê Lợi, Q1",
    "device_model": "HG8245H",
    "serial_number": "SN-0002",
}


def _edit(client, case, **changes):
    return client.patch(
        f"/field/cases/{case['id']}/details", json={"expected_version": case["details_version"], **changes}
    )


def _events(sessions, case_id, event_type="details_updated"):
    with sessions() as db:
        return [
            e.detail for e in db.query(field_models.ServiceCaseEvent).filter_by(case_id=case_id, event_type=event_type)
        ]


# ---------------------------------------------------------------- tiêu chí 1 + 3: sửa 6 trường, nhật ký cũ → mới
def test_owner_edits_all_details_and_one_log_line_records_old_and_new(env):  # noqa: F811
    client, actor, ws, sessions = env
    case = _case(client, ws, address="12 Lê Lợi, Q1", device_model="HG8145V5", serial_number="SN-0001")
    response = _edit(client, case, **NEW)
    assert response.status_code == 200, response.text
    body = response.json()
    assert {key: body[key] for key in NEW} == NEW
    assert body["details_version"] != case["details_version"]
    logs = _events(sessions, case["id"])
    assert len(logs) == 1
    log = logs[0]
    assert 'Địa chỉ: "12 Lê Lợi, Q1" → "21 Lê Lợi, Q1"' in log
    assert 'Model: "HG8145V5" → "HG8245H"' in log
    assert 'Serial: "SN-0001" → "SN-0002"' in log
    assert 'Khách hàng: (trống) → "Nguyễn Văn A"' in log
    assert "Triệu chứng/bàn giao: đã sửa" in log
    with sessions() as db:
        assert (
            db.get(
                field_models.ServiceCaseEvent,
                db.query(field_models.ServiceCaseEvent.id)
                .filter_by(case_id=case["id"], event_type="details_updated")
                .scalar(),
            ).actor_id
            == 1
        )


def test_unchanged_values_write_no_log_line(env):  # noqa: F811
    client, actor, ws, sessions = env
    case = _case(client, ws, address="12 Lê Lợi, Q1")
    assert _edit(client, case, title=case["title"], address="12 Lê Lợi, Q1").status_code == 200
    assert _events(sessions, case["id"]) == []


def test_optional_fields_can_be_cleared_but_title_and_symptom_cannot(env):  # noqa: F811
    client, actor, ws, sessions = env
    case = _case(client, ws, device_model="HG8145V5")
    cleared = _edit(client, case, device_model="")
    assert cleared.status_code == 200 and cleared.json()["device_model"] is None
    fresh = cleared.json()
    assert _edit(client, fresh, title="   ").status_code == 422
    assert _edit(client, fresh, title="ab").status_code == 422  # dưới 3 ký tự
    assert _edit(client, fresh, address="x" * 501).status_code == 422


# ---------------------------------------------------------------- tiêu chí 2: chỉ trưởng đội
def test_technician_cannot_edit_details_and_outsider_cannot_see_the_case(env):  # noqa: F811
    client, actor, ws, sessions = env
    case = _case(client, ws)
    actor["user"] = TECH
    denied = _edit(client, case, title="Kỹ thuật viên sửa")
    assert denied.status_code == 403
    actor["user"] = OUTSIDER
    assert _edit(client, case, title="Người ngoài sửa").status_code == 404
    actor["user"] = OWNER
    assert client.get(f"/field/cases/{case['id']}").json()["title"] == case["title"]
    assert _events(sessions, case["id"]) == []


# ---------------------------------------------------------------- Q3: xung đột không ghi đè
def test_second_editor_from_the_same_version_gets_a_conflict(env):  # noqa: F811
    client, actor, ws, sessions = env
    case = _case(client, ws)
    assert _edit(client, case, title="Bản sửa thứ nhất").status_code == 200
    stale = _edit(client, case, title="Bản sửa thứ hai")  # vẫn dùng details_version cũ
    assert stale.status_code == 409
    assert client.get(f"/field/cases/{case['id']}").json()["title"] == "Bản sửa thứ nhất"


def test_status_change_does_not_block_editing_details(env):  # noqa: F811
    client, actor, ws, sessions = env
    case = _case(client, ws)
    client.patch(f"/field/cases/{case['id']}", json={"status": "in_progress"})
    assert _edit(client, case, title="Vẫn sửa được").status_code == 200


def test_replayed_offline_edit_is_applied_once(env):  # noqa: F811
    client, actor, ws, sessions = env
    case = _case(client, ws)
    first = _edit(client, case, title="Sửa khi mất sóng", client_action_id="m14-offline-0001")
    again = _edit(client, case, title="Sửa khi mất sóng", client_action_id="m14-offline-0001")
    assert first.status_code == again.status_code == 200
    assert len(_events(sessions, case["id"])) == 1


# ---------------------------------------------------------------- Q2: đổi địa chỉ xoá toạ độ cũ
def test_new_address_clears_the_old_coordinates_so_routing_never_uses_them(env):  # noqa: F811
    client, actor, ws, sessions = env
    case = _case(client, ws, address="12 Lê Lợi, Q1")
    client.patch(f"/field/cases/{case['id']}/dispatch", json={"latitude": 10.77, "longitude": 106.70})
    case = client.get(f"/field/cases/{case['id']}").json()
    edited = _edit(client, case, address="Số 5 Nguyễn Huệ, Q1").json()
    assert edited["latitude"] is None and edited["longitude"] is None
    assert "đã xoá toạ độ cũ" in _events(sessions, case["id"])[0]
    # đổi trường khác không đụng toạ độ
    client.patch(f"/field/cases/{case['id']}/dispatch", json={"latitude": 10.78, "longitude": 106.71})
    case = client.get(f"/field/cases/{case['id']}").json()
    assert _edit(client, case, device_model="HG8245H").json()["latitude"] == 10.78


# ---------------------------------------------------------------- Q4: ca đã hoàn tất
def test_resolved_case_must_be_reopened_before_editing(env):  # noqa: F811
    client, actor, ws, sessions = env
    case = _case(client, ws)
    from test_field_case_closure import close_case

    assert close_case(client, case["id"]).status_code == 200  # M12: đóng ca bằng biên bản
    closed = _edit(client, case, title="Sửa sau khi đóng")
    assert closed.status_code == 409 and "Mở lại ca" in closed.json()["detail"]
    client.patch(f"/field/cases/{case['id']}", json={"status": "in_progress"})
    assert _edit(client, case, title="Sửa sau khi mở lại").status_code == 200


# ---------------------------------------------------------------- Q1 + tiêu chí 4: CRM
def _crm_case(client, ws):
    integration = client.post(
        f"/field/workspaces/{ws}/integrations",
        json={"name": "CRM", "kind": "crm", "outbound_url": "http://crm.local/hook"},
    ).json()
    created = _send(
        client,
        integration,
        {
            "event_id": "m14-1",
            "type": "ticket.created",
            "external_ref": "T-14",
            "data": {"title": "Khách báo mất mạng", "description": "Rớt mạng liên tục", "address": "12 Lê Lợi"},
        },
    ).json()
    return integration, client.get(f"/field/cases/{created['case_id']}").json()


def test_editing_a_crm_case_notifies_the_crm(env):  # noqa: F811
    client, actor, ws, sessions = env
    integration, case = _crm_case(client, ws)
    assert _edit(client, case, address="21 Lê Lợi").status_code == 200
    with sessions() as db:
        deliveries = db.query(field_models.FieldWebhookDelivery).filter_by(event_type="case.details_changed").all()
        assert len(deliveries) == 1
        payload = json.loads(deliveries[0].payload_json)
    assert payload["changed_fields"] == ["address"]
    assert payload["details"]["address"] == "21 Lê Lợi"
    assert payload["case"]["external_ref"] == "T-14"


def test_manual_case_edit_sends_no_webhook_and_later_crm_updates_still_apply(env):  # noqa: F811
    client, actor, ws, sessions = env
    manual = _case(client, ws)
    _edit(client, manual, title="Ca nhập tay đã sửa")
    integration, case = _crm_case(client, ws)
    _edit(client, case, title="Trưởng đội sửa tiêu đề")
    _send(
        client,
        integration,
        {"event_id": "m14-2", "type": "ticket.updated", "external_ref": "T-14", "data": {"title": "CRM sửa tiêu đề"}},
    )
    assert client.get(f"/field/cases/{case['id']}").json()["title"] == "CRM sửa tiêu đề"
    assert _events(sessions, case["id"], "external")
    with sessions() as db:
        assert db.query(field_models.FieldWebhookDelivery).filter_by(event_type="case.details_changed").count() == 1
