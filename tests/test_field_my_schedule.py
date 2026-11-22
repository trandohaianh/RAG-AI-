"""M11 — lịch hôm nay của kỹ thuật viên (docs/PRD.md §5.1, docs/design/M11_lich_hom_nay_mobile.md)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

from auth.schemas import CurrentUser
from field import models as field_models
from test_field_dispatch_photos_integrations import _case, env  # noqa: F401

OWNER = CurrentUser(id=1, email="u1@example.com", role="user")
TECH = CurrentUser(id=2, email="u2@example.com", role="user")
OTHER_TECH = CurrentUser(id=4, email="u4@example.com", role="user")
VN = timezone(timedelta(hours=7))
DAY = date(2026, 12, 1)


def _schedule(sessions, case_id, technician, local_hhmm: str | None, order: int | None = None, status=None):
    with sessions() as db:
        case = db.get(field_models.ServiceCase, case_id)
        case.assigned_to = technician
        if local_hhmm is not None:
            hour, minute = map(int, local_hhmm.split(":"))
            case.scheduled_start = datetime(DAY.year, DAY.month, DAY.day, hour, minute, tzinfo=VN).astimezone(UTC)
            case.route_order = order
        if status:
            case.status = status
        db.commit()


def _mine(client, day=DAY):
    response = client.get("/field/my-schedule", params={"date": day.isoformat(), "tz_offset_minutes": 420})
    assert response.status_code == 200, response.text
    return response.json()


def _add_member(client, ws, email):
    assert client.post(f"/field/workspaces/{ws}/members", json={"email": email}).status_code == 201


# ---------------------------------------------------------------- tiêu chí 1: chỉ ca của tôi, đúng thứ tự
def test_technician_sees_only_own_cases_in_route_order(env):  # noqa: F811
    client, actor, ws, sessions = env
    _add_member(client, ws, "u4@example.com")
    late = _case(client, ws, title="Ca chiều")
    early = _case(client, ws, title="Ca sáng", address="12 Lê Lợi")
    other = _case(client, ws, title="Ca của người khác")
    _schedule(sessions, late["id"], 2, "14:00", order=2)
    _schedule(sessions, early["id"], 2, "08:30", order=1)
    _schedule(sessions, other["id"], 4, "09:00", order=1)
    actor["user"] = TECH
    mine = _mine(client)
    assert [s["case_id"] for s in mine["stops"]] == [early["id"], late["id"]]
    first = mine["stops"][0]
    assert first["scheduled_start"].startswith("2026-12-01T08:30") and first["scheduled_start"].endswith("+07:00")
    assert first["address"] == "12 Lê Lợi" and first["route_order"] == 1 and first["workspace"]
    assert mine["total"] == 2 and mine["done"] == 0


def test_cases_from_every_team_of_the_technician_are_merged(env):  # noqa: F811
    client, actor, ws, sessions = env
    second_ws = client.post("/field/workspaces", json={"name": "Đội Q3"}).json()["id"]
    _add_member(client, second_ws, "u2@example.com")
    a = _case(client, ws, title="Ca đội 1")
    b = _case(client, second_ws, title="Ca đội 2")
    _schedule(sessions, a["id"], 2, "10:00", order=1)
    _schedule(sessions, b["id"], 2, "09:00", order=1)
    actor["user"] = TECH
    assert [s["case_id"] for s in _mine(client)["stops"]] == [b["id"], a["id"]]


def test_vietnam_day_boundaries_are_respected(env):  # noqa: F811
    client, actor, ws, sessions = env
    late_night = _case(client, ws, title="23:30 ngày 1/12")
    after_midnight = _case(client, ws, title="00:15 ngày 2/12")
    _schedule(sessions, late_night["id"], 2, "23:30", order=1)
    with sessions() as db:  # 00:15 giờ VN ngày 2/12 = 17:15 UTC ngày 1/12
        case = db.get(field_models.ServiceCase, after_midnight["id"])
        case.assigned_to = 2
        case.scheduled_start = datetime(2026, 12, 2, 0, 15, tzinfo=VN).astimezone(UTC)
        db.commit()
    actor["user"] = TECH
    assert [s["case_id"] for s in _mine(client)["stops"]] == [late_night["id"]]
    assert [s["case_id"] for s in _mine(client, DAY + timedelta(days=1))["stops"]] == [after_midnight["id"]]


# ---------------------------------------------------------------- Q4 + tiến độ
def test_unscheduled_open_cases_and_progress(env):  # noqa: F811
    client, actor, ws, sessions = env
    done = _case(client, ws, title="Đã xong")
    todo = _case(client, ws, title="Chưa làm")
    loose = _case(client, ws, title="Giao nhưng chưa xếp giờ")
    closed_loose = _case(client, ws, title="Chưa xếp giờ nhưng đã đóng")
    _schedule(sessions, done["id"], 2, "08:00", order=1, status="resolved")
    _schedule(sessions, todo["id"], 2, "10:00", order=2)
    _schedule(sessions, loose["id"], 2, None)
    _schedule(sessions, closed_loose["id"], 2, None, status="resolved")
    actor["user"] = TECH
    mine = _mine(client)
    assert mine["done"] == 1 and mine["total"] == 2
    assert [s["status"] for s in mine["stops"]] == ["resolved", "open"]
    assert [s["case_id"] for s in mine["unscheduled"]] == [loose["id"]]


def test_a_case_from_a_team_i_left_is_not_shown(env):  # noqa: F811
    client, actor, ws, sessions = env
    case = _case(client, ws, title="Ca cũ")
    _schedule(sessions, case["id"], 2, "09:00", order=1)
    with sessions() as db:
        db.query(field_models.FieldWorkspaceMember).filter_by(workspace_id=ws, user_id=2).delete()
        db.commit()
    actor["user"] = TECH
    assert _mine(client)["stops"] == []


# ---------------------------------------------------------------- Q2: lịch cả đội chỉ cho trưởng đội
def test_team_schedule_shows_a_technician_only_their_own_route(env):  # noqa: F811
    client, actor, ws, sessions = env
    _add_member(client, ws, "u4@example.com")
    mine = _case(client, ws, title="Ca của tôi")
    theirs = _case(client, ws, title="Ca của người khác", address="Nhà khách hàng khác")
    _schedule(sessions, mine["id"], 2, "09:00", order=1)
    _schedule(sessions, theirs["id"], 4, "09:00", order=1)
    params = {"date": DAY.isoformat(), "tz_offset_minutes": 420}
    owner_view = client.get(f"/field/workspaces/{ws}/schedule", params=params).json()
    assert {r["technician_id"] for r in owner_view["routes"]} == {2, 4}
    actor["user"] = TECH
    tech_view = client.get(f"/field/workspaces/{ws}/schedule", params={**params, "technician_id": 4}).json()
    assert [r["technician_id"] for r in tech_view["routes"]] == [2]
    assert "Nhà khách hàng khác" not in str(tech_view)


# ---------------------------------------------------------------- lỗi có sẵn phát hiện khi chạy thử M11
def test_case_times_carry_a_timezone_so_apps_do_not_shift_them_by_seven_hours(env):  # noqa: F811
    client, actor, ws, sessions = env
    case = _case(client, ws, title="Ca 07:04")
    _schedule(sessions, case["id"], 2, "07:04", order=1)
    body = client.get(f"/field/cases/{case['id']}").json()
    # SQLite trả giờ UTC không múi giờ; thiếu "+00:00" thì app/web hiển thị 00:04 thay vì 07:04.
    for key in ("scheduled_start", "created_at", "updated_at"):
        assert body[key].endswith("+00:00"), (key, body[key])
    assert datetime.fromisoformat(body["scheduled_start"]).astimezone(VN).strftime("%H:%M") == "07:04"
    assert body["events"][0]["created_at"].endswith("+00:00")
