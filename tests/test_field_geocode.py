"""M13 — địa chỉ → toạ độ (docs/PRD.md §5.1, docs/design/M13_dia_chi_toa_do.md).

Không bao giờ gọi Nominatim thật: httpx.MockTransport + đồng hồ giả.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from auth.schemas import CurrentUser
from field import geocode
from field import models as field_models
from test_field_dispatch_photos_integrations import _case, _send, env  # noqa: F401

TECH = CurrentUser(id=2, email="u2@example.com", role="user")
OTHER_TECH = CurrentUser(id=4, email="u4@example.com", role="user")
OUTSIDER = CurrentUser(id=3, email="u3@example.com", role="user")

HOUSE = {
    "lat": "10.7769",
    "lon": "106.7009",
    "display_name": "12 Lê Lợi, Bến Nghé, TP.HCM",
    "addresstype": "building",
    "address": {"house_number": "12", "road": "Lê Lợi"},
}
STREET = {
    "lat": "21.0285",
    "lon": "105.8542",
    "display_name": "Phố Huế, Hà Nội",
    "addresstype": "road",
    "category": "highway",
    "address": {"road": "Phố Huế"},
}
WARD = {"lat": "21.01", "lon": "105.85", "display_name": "Phường Bạch Mai", "addresstype": "quarter", "address": {}}


class FakeNominatim:
    def __init__(self, monkeypatch, answer=None, status=200):
        self.calls: list[httpx.Request] = []
        self.answer = [HOUSE] if answer is None else answer
        self.status = status
        self.clock = [1000.0]
        self.sleeps: list[float] = []
        monkeypatch.setattr("config.NOMINATIM_CONTACT", "ops@example.test")
        monkeypatch.setattr(geocode, "_transport", httpx.MockTransport(self.handle))
        monkeypatch.setattr(geocode, "_monotonic", lambda: self.clock[0])
        monkeypatch.setattr(geocode, "_sleep", self.sleep)
        monkeypatch.setattr(geocode, "_last_call", [float("-inf")])

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.clock[0] += seconds

    def handle(self, request):
        self.calls.append(request)
        if isinstance(self.status, Exception):
            raise self.status
        return httpx.Response(self.status, json=self.answer)

    def query(self, i=-1):
        return parse_qs(urlparse(str(self.calls[i].url)).query)


def _geocode(client, ws, address):
    return client.post(f"/field/workspaces/{ws}/geocode", json={"address": address})


def _assign(sessions, case_id, user_id):
    with sessions() as db:
        db.get(field_models.ServiceCase, case_id).assigned_to = user_id
        db.commit()


def _suggest(sessions, case_id, **kwargs):
    with sessions() as db:
        geocode.set_suggestion(db.get(field_models.ServiceCase, case_id), **kwargs)
        db.commit()


# ---------------------------------------------------------------- tiêu chí 1 + 3
def test_found_address_returns_suggestions_with_precision(env, monkeypatch):  # noqa: F811
    client, actor, ws, sessions = env
    fake = FakeNominatim(monkeypatch, [HOUSE, STREET, WARD])
    body = _geocode(client, ws, "12 Lê Lợi, Quận 1, TP.HCM").json()
    assert [r["precision"] for r in body["results"]] == ["house", "street", "ward"]
    assert body["results"][0]["latitude"] == 10.7769
    assert body["results"][1]["precision_label"] == "Chỉ chính xác đến tên đường"
    assert body["message"] is None and "OpenStreetMap" in body["attribution"]
    assert fake.query()["countrycodes"] == ["vn"]


def test_unknown_address_says_so_clearly(env, monkeypatch):  # noqa: F811
    client, actor, ws, sessions = env
    FakeNominatim(monkeypatch, [])
    response = _geocode(client, ws, "đường không có thật 999")
    assert response.status_code == 200
    assert response.json()["results"] == [] and "bằng tay" in response.json()["message"]


# ---------------------------------------------------------------- tiêu chí 4: cache + nhịp
def test_same_address_is_fetched_once_and_refetched_after_expiry(env, monkeypatch):  # noqa: F811
    client, actor, ws, sessions = env
    fake = FakeNominatim(monkeypatch)
    _geocode(client, ws, "12 Lê Lợi,  Quận 1 ")
    again = _geocode(client, ws, "12 lê lợi, quận 1").json()
    assert len(fake.calls) == 1 and again["cached"] is True
    with sessions() as db:
        row = db.query(field_models.FieldGeocodeCache).one()
        assert len(row.query_hash) == 64 and "lê" not in row.query_hash  # chỉ lưu hash câu tra
        row.created_at = datetime.now(UTC) - timedelta(days=91)
        db.commit()
    _geocode(client, ws, "12 Lê Lợi, Quận 1")
    assert len(fake.calls) == 2


def test_not_found_is_only_remembered_for_a_day(env, monkeypatch):  # noqa: F811
    client, actor, ws, sessions = env
    fake = FakeNominatim(monkeypatch, [])
    _geocode(client, ws, "Hẻm mới mở 77")
    _geocode(client, ws, "Hẻm mới mở 77")
    assert len(fake.calls) == 1
    with sessions() as db:
        db.query(field_models.FieldGeocodeCache).one().created_at = datetime.now(UTC) - timedelta(days=2)
        db.commit()
    _geocode(client, ws, "Hẻm mới mở 77")
    assert len(fake.calls) == 2


def test_calls_are_spaced_at_least_1_1_seconds(env, monkeypatch):  # noqa: F811
    client, actor, ws, sessions = env
    fake = FakeNominatim(monkeypatch)
    for street in ("Lê Lợi 1", "Lê Lợi 2", "Lê Lợi 3"):
        _geocode(client, ws, street)
    assert len(fake.calls) == 3
    assert len(fake.sleeps) == 2 and all(s >= 1.1 for s in fake.sleeps)


def test_cache_is_per_team(env, monkeypatch):  # noqa: F811
    client, actor, ws, sessions = env
    fake = FakeNominatim(monkeypatch)
    _geocode(client, ws, "12 Lê Lợi, Quận 1")
    actor["user"] = OUTSIDER
    other_ws = client.get("/field/workspaces").json()[0]["id"]
    assert other_ws != ws
    assert _geocode(client, other_ws, "12 Lê Lợi, Quận 1").json()["cached"] is False
    assert len(fake.calls) == 2


# ---------------------------------------------------------------- tiêu chí 5: chỉ gửi địa chỉ
def test_only_the_address_leaves_the_server(env, monkeypatch):  # noqa: F811
    client, actor, ws, sessions = env
    fake = FakeNominatim(monkeypatch)
    _geocode(client, ws, "12 Lê Lợi, Quận 1")
    request = fake.calls[0]
    params = fake.query()
    assert params["q"] == ["12 Lê Lợi, Quận 1"]
    assert set(params) == {"q", "countrycodes", "format", "addressdetails", "limit", "accept-language"}
    assert "ops@example.test" in request.headers["user-agent"]
    assert "authorization" not in request.headers and "cookie" not in request.headers


# ---------------------------------------------------------------- lỗi dịch vụ, quyền, tắt
@pytest.mark.parametrize("status", [429, 503, httpx.ReadTimeout("slow")])
def test_service_failure_asks_for_manual_entry_and_is_not_cached(env, monkeypatch, status):  # noqa: F811
    client, actor, ws, sessions = env
    fake = FakeNominatim(monkeypatch, status=status)
    response = _geocode(client, ws, "12 Lê Lợi, Quận 1")
    assert response.status_code == 502 and "bằng tay" in response.json()["detail"]
    with sessions() as db:
        assert db.query(field_models.FieldGeocodeCache).count() == 0
    fake.status = 200
    assert _geocode(client, ws, "12 Lê Lợi, Quận 1").json()["results"]


def test_access_and_disabled_geocoder(env, monkeypatch):  # noqa: F811
    client, actor, ws, sessions = env
    monkeypatch.setattr("config.NOMINATIM_CONTACT", "")
    response = _geocode(client, ws, "12 Lê Lợi, Quận 1")
    assert response.status_code == 503 and "NOMINATIM_CONTACT" in response.json()["detail"]
    FakeNominatim(monkeypatch)
    actor["user"] = TECH  # kỹ thuật viên cũng tra được (mobile, Q5)
    assert _geocode(client, ws, "12 Lê Lợi, Quận 1").status_code == 200
    actor["user"] = OUTSIDER
    assert _geocode(client, ws, "12 Lê Lợi, Quận 1").status_code == 404


# ---------------------------------------------------------------- xác nhận (tiêu chí 2) + nhật ký
def test_saving_confirmed_coordinates_logs_the_source_and_clears_the_suggestion(env):  # noqa: F811
    client, actor, ws, sessions = env
    case = _case(client, ws, address="12 Lê Lợi")
    _suggest(sessions, case["id"], latitude=10.7, longitude=106.7, source="geocoded", by=None, precision="street")
    shown = client.get(f"/field/cases/{case['id']}").json()
    assert shown["location_suggestion"]["precision_label"] == "Chỉ chính xác đến tên đường"
    assert shown["latitude"] is None, "gợi ý không phải toạ độ chính thức"
    assert "location_suggestion_json" not in shown
    saved = client.patch(
        f"/field/cases/{case['id']}/dispatch",
        json={"latitude": 10.77, "longitude": 106.7, "location_source": "suggestion"},
    )
    assert saved.status_code == 200, saved.text
    after = client.get(f"/field/cases/{case['id']}").json()
    assert after["latitude"] == 10.77 and after["location_suggestion"] is None
    assert "xác nhận vị trí đề xuất" in after["events"][-1]["detail"]


# ---------------------------------------------------------------- Q2: ca CRM
def test_crm_case_without_coordinates_gets_a_suggestion_not_a_route_location(env, monkeypatch):  # noqa: F811
    client, actor, ws, sessions = env
    fake = FakeNominatim(monkeypatch, [STREET])
    scheduled = []
    monkeypatch.setattr(geocode, "schedule_case", scheduled.append)
    integration = client.post(f"/field/workspaces/{ws}/integrations", json={"name": "CRM", "kind": "crm"}).json()
    data = {"title": "Mất mạng", "description": "Khách báo", "address": "Phố Huế, Hà Nội", "customer": "Nguyễn Văn A"}
    ticket = {"event_id": "g-1", "type": "ticket.created", "external_ref": "T-1", "data": data}
    case_id = _send(client, integration, ticket).json()["case_id"]
    assert scheduled == [case_id]
    with sessions() as db:
        assert geocode.suggest_for_case(db, case_id) == "suggested"
    assert "Nguy" not in str(fake.calls[0].url) and "T-1" not in str(fake.calls[0].url)
    case = client.get(f"/field/cases/{case_id}").json()
    assert case["latitude"] is None and case["location_suggestion"]["latitude"] == 21.0285
    assert "chờ trưởng đội xác nhận" in case["events"][-1]["detail"]

    # CRM đổi địa chỉ → gợi ý cũ bị xoá và tra lại.
    update = {"event_id": "g-2", "type": "ticket.updated", "external_ref": "T-1", "data": {"address": "Bạch Mai"}}
    _send(client, integration, update)
    assert client.get(f"/field/cases/{case_id}").json()["location_suggestion"] is None
    assert scheduled == [case_id, case_id]

    # CRM gửi kèm toạ độ → không cần tra.
    with_coords = {
        "title": "Mất mạng",
        "description": "x x",
        "address": "Phố Huế",
        "latitude": 21.0,
        "longitude": 105.8,
    }
    _send(
        client, integration, {"event_id": "g-3", "type": "ticket.created", "external_ref": "T-2", "data": with_coords}
    )
    assert len(scheduled) == 2


def test_background_lookup_skips_a_case_whose_address_changed_meanwhile(env, monkeypatch):  # noqa: F811
    client, actor, ws, sessions = env
    FakeNominatim(monkeypatch, [STREET])
    case = _case(client, ws, address="Phố Huế")
    original = geocode.lookup

    def lookup_then_edit(db, workspace_id, address):
        result = original(db, workspace_id, address)
        with sessions() as other:
            other.get(field_models.ServiceCase, case["id"]).address = "Địa chỉ mới"
            other.commit()
        return result

    monkeypatch.setattr(geocode, "lookup", lookup_then_edit)
    with sessions() as db:
        assert geocode.suggest_for_case(db, case["id"]) == "stale"
    assert client.get(f"/field/cases/{case['id']}").json()["location_suggestion"] is None


def test_manual_address_edit_clears_the_suggestion(env):  # noqa: F811
    client, actor, ws, sessions = env
    case = _case(client, ws, address="12 Lê Lợi")
    _suggest(sessions, case["id"], latitude=10.7, longitude=106.7, source="gps", by=2)
    current = client.get(f"/field/cases/{case['id']}").json()
    edited = client.patch(
        f"/field/cases/{case['id']}/details",
        json={"address": "99 Hai Bà Trưng", "expected_version": current["details_version"]},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["location_suggestion"] is None


# ---------------------------------------------------------------- Q5: kỹ thuật viên gửi gợi ý
def test_assigned_technician_suggests_and_the_owner_confirms(env):  # noqa: F811
    client, actor, ws, sessions = env
    client.post(f"/field/workspaces/{ws}/members", json={"email": "u4@example.com"})
    case = _case(client, ws, address="12 Lê Lợi")
    _assign(sessions, case["id"], 2)
    url = f"/field/cases/{case['id']}/location-suggestion"
    body = {"latitude": 10.7769, "longitude": 106.7009, "source": "gps", "client_action_id": "loc-00000001"}
    actor["user"] = OTHER_TECH
    assert client.post(url, json=body).status_code == 403
    actor["user"] = OUTSIDER
    assert client.post(url, json=body).status_code == 404
    actor["user"] = TECH
    sent = client.post(url, json=body)
    assert sent.status_code == 200, sent.text
    assert sent.json()["latitude"] is None and sent.json()["location_suggestion"]["suggested_by"] == 2
    assert "GPS tại nhà khách" in sent.json()["events"][-1]["detail"]
    # Gửi lại (offline replay) không ghi nhật ký lần hai.
    replay = client.post(url, json=body).json()
    assert sum("vị trí đề xuất" in e["detail"] for e in replay["events"]) == 1
    # Kỹ thuật viên vẫn không sửa thẳng được toạ độ.
    dispatch = client.patch(f"/field/cases/{case['id']}/dispatch", json={"latitude": 1, "longitude": 1})
    assert dispatch.status_code == 403


def test_expired_cache_rows_are_purged(env, monkeypatch):  # noqa: F811
    client, actor, ws, sessions = env
    FakeNominatim(monkeypatch)
    _geocode(client, ws, "12 Lê Lợi, Quận 1")
    with sessions() as db:
        assert geocode.purge_expired(db) == 0
        assert geocode.purge_expired(db, now=datetime.now(UTC) + timedelta(days=91)) == 1
