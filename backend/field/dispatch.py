"""Điều phối lịch tuyến cho kỹ thuật viên.

Bài toán: một ngày, N kỹ thuật viên (điểm xuất phát, ca làm), M ca có toạ độ, thời gian xử lý
ước tính, độ ưu tiên và có thể có khung giờ hẹn khách. Cần gán ca cho người và xếp thứ tự đi
sao cho tổng thời gian di chuyển nhỏ, không vi phạm khung giờ hẹn và ca làm.

Thuật toán (VRP có khung giờ, heuristic — đủ nhanh cho vài trăm ca, không cần OR-Tools):
1. Xếp ca theo ưu tiên (urgent > high > normal > low), rồi khung giờ kết thúc sớm trước.
2. Chèn rẻ nhất (cheapest insertion): thử chèn từng ca vào mọi vị trí của mọi tuyến, giữ vị trí
   khả thi làm tăng thời gian di chuyển ít nhất (cộng chút phạt để chia đều tải).
3. Cải thiện từng tuyến bằng 2-opt, chỉ nhận đảo đoạn nếu vẫn khả thi.
Thời gian đi = khoảng cách đường chim bay (haversine) × hệ số đường phố 1,35 ÷ tốc độ trung bình.
Đây là ước lượng, không phải dẫn đường thực tế — kỹ thuật viên vẫn dùng bản đồ khi đi.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth.models import User
from auth.schemas import CurrentUser
from database import get_db
from dependencies import get_current_user
from field.models import FieldWorkspaceMember, ServiceCase, ServiceCaseEvent

router = APIRouter(prefix="/field", tags=["field dispatch"])

ROAD_FACTOR = 1.35
PRIORITY_RANK = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
# Mục tiêu = phút di chuyển + Σ trọng số × phút từ đầu ca tới lúc bắt đầu xử lý: ca khẩn được
# đẩy lên sớm dù phải đi xa hơn một chút (1 giờ chờ của ca khẩn "đắt" bằng 30 phút lái xe).
URGENCY_WEIGHT = {"urgent": 0.5, "high": 0.2, "normal": 0.05, "low": 0.0}
ACTIVE_STATUSES = ("open", "in_progress", "waiting", "escalated")


# ----------------------------------------------------------------------------- pure planner
@dataclass
class Tech:
    id: int
    lat: float
    lng: float
    start: int  # phút kể từ 00:00 giờ địa phương
    end: int


@dataclass
class Job:
    id: int
    lat: float
    lng: float
    duration: int
    priority: str = "normal"
    earliest: int | None = None
    latest: int | None = None  # khung giờ hẹn = khung giờ ĐẾN: phải bắt đầu xử lý trước mốc này


@dataclass
class Stop:
    job_id: int
    arrive: int
    start: int
    end: int
    travel: int
    distance_km: float
    wait_weight: float = 0.0  # phạt mỗi phút ca này phải chờ kể từ đầu ca làm (theo ưu tiên)


@dataclass
class Plan:
    routes: dict[int, list[Stop]] = field(default_factory=dict)
    unassigned: dict[int, str] = field(default_factory=dict)
    travel_minutes: int = 0
    distance_km: float = 0.0


def haversine_km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _simulate(tech: Tech, seq: list[Job], speed_kmh: float) -> list[Stop] | None:
    """Thời điểm đến/bắt đầu/xong của từng ca; None nếu vi phạm khung giờ hoặc ca làm."""
    stops, clock, lat, lng = [], tech.start, tech.lat, tech.lng
    for job in seq:
        km = haversine_km(lat, lng, job.lat, job.lng) * ROAD_FACTOR
        travel = math.ceil(km / speed_kmh * 60)
        arrive = clock + travel
        start = max(arrive, job.earliest) if job.earliest is not None else arrive
        if job.latest is not None and start > job.latest:
            return None
        end = start + job.duration
        if end > tech.end:
            return None
        stops.append(Stop(job.id, arrive, start, end, travel, round(km, 2), URGENCY_WEIGHT.get(job.priority, 0.05)))
        clock, lat, lng = end, job.lat, job.lng
    return stops


def _travel(stops: list[Stop]) -> int:
    return sum(s.travel for s in stops)


def _cost(stops: list[Stop], shift_start: int = 0) -> float:
    return sum(s.travel + s.wait_weight * max(0, s.start - shift_start) for s in stops)


def plan_routes(techs: list[Tech], jobs: list[Job], speed_kmh: float = 30.0) -> Plan:
    plan = Plan()
    if not techs:
        plan.unassigned = {j.id: "không có kỹ thuật viên có điểm xuất phát" for j in jobs}
        return plan
    seqs: dict[int, list[Job]] = {t.id: [] for t in techs}
    by_id = {t.id: t for t in techs}
    order = sorted(
        jobs, key=lambda j: (PRIORITY_RANK.get(j.priority, 2), j.latest if j.latest is not None else 10**6, j.id)
    )
    for job in order:
        best = None
        for tech in techs:
            seq = seqs[tech.id]
            base = _simulate(tech, seq, speed_kmh)
            base_cost = _cost(base, tech.start) if base is not None else 0
            for pos in range(len(seq) + 1):
                trial = seq[:pos] + [job] + seq[pos:]
                stops = _simulate(tech, trial, speed_kmh)
                if stops is None:
                    continue
                # phạt nhẹ theo số ca đã có để không dồn hết vào một người
                score = (_cost(stops, tech.start) - base_cost) + 3 * len(seq)
                if best is None or score < best[0]:
                    best = (score, tech.id, trial)
        if best is None:
            reason = "không xếp được: ngoài ca làm hoặc không kịp khung giờ hẹn"
            if job.latest is not None and job.earliest is not None and job.latest < job.earliest:
                reason = "khung giờ hẹn không hợp lệ"
            plan.unassigned[job.id] = reason
            continue
        seqs[best[1]] = best[2]
    for tech_id, seq in seqs.items():
        seqs[tech_id] = _two_opt(by_id[tech_id], seq, speed_kmh)
        stops = _simulate(by_id[tech_id], seqs[tech_id], speed_kmh) or []
        if stops:
            plan.routes[tech_id] = stops
            plan.travel_minutes += _travel(stops)
            plan.distance_km += sum(s.distance_km for s in stops)
    plan.distance_km = round(plan.distance_km, 2)
    return plan


def _two_opt(tech: Tech, seq: list[Job], speed_kmh: float) -> list[Job]:
    best = list(seq)
    best_stops = _simulate(tech, best, speed_kmh)
    if best_stops is None or len(best) < 3:
        return best
    improved = True
    while improved:
        improved = False
        for i in range(len(best) - 1):
            for k in range(i + 1, len(best)):
                trial = best[:i] + best[i : k + 1][::-1] + best[k + 1 :]
                stops = _simulate(tech, trial, speed_kmh)
                if stops is not None and _cost(stops, tech.start) < _cost(best_stops, tech.start):
                    best, best_stops, improved = trial, stops, True
    return best


# ----------------------------------------------------------------------------- API
class CaseDispatchUpdate(BaseModel):
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    priority: str | None = Field(default=None, pattern="^(urgent|high|normal|low)$")
    window_start: datetime | None = None
    window_end: datetime | None = None
    est_minutes: int | None = Field(default=None, ge=5, le=600)

    @model_validator(mode="after")
    def _window(self):
        if self.window_start and self.window_end and self.window_end <= self.window_start:
            raise ValueError("Khung giờ hẹn: giờ kết thúc phải sau giờ bắt đầu")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Cần cả vĩ độ và kinh độ")
        return self


class MemberDispatchUpdate(BaseModel):
    base_latitude: float = Field(ge=-90, le=90)
    base_longitude: float = Field(ge=-180, le=180)
    shift_start: str = Field(default="08:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="17:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class PlanRequest(BaseModel):
    date: date
    tz_offset_minutes: int = Field(default=420, ge=-720, le=840)  # mặc định giờ Việt Nam (UTC+7)
    speed_kmh: float = Field(default=30.0, ge=5, le=100)
    technician_ids: list[int] | None = None
    include_scheduled: bool = False


class RouteIn(BaseModel):
    technician_id: int
    case_ids: list[int] = Field(max_length=100)


class ApplyRequest(PlanRequest):
    routes: list[RouteIn]


def _owner(db: Session, workspace_id: int, user_id: int) -> FieldWorkspaceMember:
    member = db.scalar(
        select(FieldWorkspaceMember).where(
            FieldWorkspaceMember.workspace_id == workspace_id, FieldWorkspaceMember.user_id == user_id
        )
    )
    if not member:
        raise HTTPException(status_code=404, detail="Không tìm thấy đội")
    return member


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _local_minutes(moment: datetime | None, day: date, tz: timezone) -> int | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    local = moment.astimezone(tz)
    return int((local - datetime.combine(day, time(0), tz)).total_seconds() // 60)


def _inputs(db: Session, workspace_id: int, body: PlanRequest):
    tz = timezone(timedelta(minutes=body.tz_offset_minutes))
    members = db.scalars(select(FieldWorkspaceMember).where(FieldWorkspaceMember.workspace_id == workspace_id)).all()
    techs, skipped_techs = [], []
    for m in members:
        if body.technician_ids is not None and m.user_id not in body.technician_ids:
            continue
        if m.base_latitude is None or m.base_longitude is None:
            skipped_techs.append(m.user_id)
            continue
        techs.append(Tech(m.user_id, m.base_latitude, m.base_longitude, _minutes(m.shift_start), _minutes(m.shift_end)))
    day_start = datetime.combine(body.date, time(0), tz)
    day_end = day_start + timedelta(days=1)
    cases = db.scalars(
        select(ServiceCase).where(ServiceCase.workspace_id == workspace_id, ServiceCase.status.in_(ACTIVE_STATUSES))
    ).all()
    jobs, unassigned, by_id = [], {}, {}
    for case in cases:
        if case.scheduled_start is not None and not body.include_scheduled:
            continue
        if case.window_start and _aware(case.window_start) >= day_end:
            continue
        if case.window_end and _aware(case.window_end) < day_start:
            continue
        by_id[case.id] = case
        if case.latitude is None or case.longitude is None:
            unassigned[case.id] = "chưa có toạ độ"
            continue
        earliest = _local_minutes(case.window_start, body.date, tz)
        latest = _local_minutes(case.window_end, body.date, tz)
        jobs.append(
            Job(
                case.id,
                case.latitude,
                case.longitude,
                case.est_minutes or 60,
                case.priority or "normal",
                max(earliest, 0) if earliest is not None else None,
                min(latest, 24 * 60) if latest is not None else None,
            )
        )
    return tz, techs, skipped_techs, jobs, unassigned, by_id


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _plan_out(plan: Plan, body: PlanRequest, tz: timezone, by_id: dict, emails: dict) -> dict:
    def clock(minutes: int) -> str:
        return (datetime.combine(body.date, time(0), tz) + timedelta(minutes=minutes)).isoformat()

    return {
        "date": body.date.isoformat(),
        "routes": [
            {
                "technician_id": tech_id,
                "technician": emails.get(tech_id),
                "travel_minutes": _travel(stops),
                "distance_km": round(sum(s.distance_km for s in stops), 2),
                "stops": [
                    {
                        "case_id": s.job_id,
                        "title": by_id[s.job_id].title,
                        "address": by_id[s.job_id].address,
                        "latitude": by_id[s.job_id].latitude,
                        "longitude": by_id[s.job_id].longitude,
                        "priority": by_id[s.job_id].priority,
                        "arrive": clock(s.arrive),
                        "start": clock(s.start),
                        "end": clock(s.end),
                        "travel_minutes": s.travel,
                        "distance_km": s.distance_km,
                    }
                    for s in stops
                ],
            }
            for tech_id, stops in plan.routes.items()
        ],
        "unassigned": [
            {"case_id": cid, "title": by_id[cid].title if cid in by_id else None, "reason": reason}
            for cid, reason in plan.unassigned.items()
        ],
        "travel_minutes": plan.travel_minutes,
        "distance_km": plan.distance_km,
        "estimate_note": "Thời gian đi ước lượng theo đường chim bay × 1,35 và tốc độ trung bình.",
    }


def _emails(db: Session, ids) -> dict[int, str]:
    return {u.id: u.email for u in db.scalars(select(User).where(User.id.in_(list(ids) or [0]))).all()}


@router.patch("/cases/{case_id}/dispatch")
def update_case_dispatch(
    case_id: int, body: CaseDispatchUpdate, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)
):
    case = db.get(ServiceCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Không tìm thấy ca sửa")
    if _owner(db, case.workspace_id, user.id).role != "owner":
        # Vị trí/khung giờ/ưu tiên là đầu vào của lập lịch, vốn chỉ trưởng đội được làm.
        raise HTTPException(status_code=403, detail="Chỉ trưởng đội được sửa thông tin điều phối")
    # priority/est_minutes là cột NOT NULL: null tường minh phải là 422, không phải IntegrityError 500.
    for name in ("priority", "est_minutes"):
        if name in body.model_fields_set and getattr(body, name) is None:
            raise HTTPException(status_code=422, detail=f"{name} không được để trống")
    start = body.window_start if "window_start" in body.model_fields_set else case.window_start
    end = body.window_end if "window_end" in body.model_fields_set else case.window_end
    if start and end:
        if _aware(end) <= _aware(start):
            # Chỉ gửi một đầu mút vẫn phải so với đầu mút đã lưu.
            raise HTTPException(status_code=422, detail="Khung giờ hẹn: giờ kết thúc phải sau giờ bắt đầu")
    for name in body.model_fields_set:
        value = getattr(body, name)
        if isinstance(value, datetime):
            # Luôn lưu UTC: SQLite bỏ múi giờ khi lưu, đọc ra sẽ được hiểu là UTC.
            value = (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)
        setattr(case, name, value)
    db.add(
        ServiceCaseEvent(
            case_id=case.id, actor_id=user.id, event_type="dispatch", detail="Cập nhật vị trí/khung giờ/ưu tiên của ca"
        )
    )
    db.commit()
    return {
        "id": case.id,
        "latitude": case.latitude,
        "longitude": case.longitude,
        "priority": case.priority,
        "window_start": case.window_start,
        "window_end": case.window_end,
        "est_minutes": case.est_minutes,
    }


@router.patch("/workspaces/{workspace_id}/members/{member_user_id}/dispatch")
def update_member_dispatch(
    workspace_id: int,
    member_user_id: int,
    body: MemberDispatchUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    actor = _owner(db, workspace_id, user.id)
    if actor.role != "owner" and member_user_id != user.id:
        raise HTTPException(status_code=403, detail="Chỉ trưởng đội hoặc chính kỹ thuật viên được sửa")
    if _minutes(body.shift_end) <= _minutes(body.shift_start):
        raise HTTPException(status_code=422, detail="Giờ kết thúc ca phải sau giờ bắt đầu")
    member = _owner(db, workspace_id, member_user_id)
    for name, value in body.model_dump().items():
        setattr(member, name, value)
    db.commit()
    return {"user_id": member.user_id, **body.model_dump()}


@router.post("/workspaces/{workspace_id}/schedule/plan")
def plan_schedule(
    workspace_id: int, body: PlanRequest, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)
):
    if _owner(db, workspace_id, user.id).role != "owner":
        raise HTTPException(status_code=403, detail="Chỉ trưởng đội được lập lịch")
    tz, techs, skipped, jobs, unassigned, by_id = _inputs(db, workspace_id, body)
    plan = plan_routes(techs, jobs, body.speed_kmh)
    plan.unassigned.update(unassigned)
    out = _plan_out(plan, body, tz, by_id, _emails(db, [t.id for t in techs]))
    out["technicians_without_base"] = skipped
    return out


@router.post("/workspaces/{workspace_id}/schedule/apply")
def apply_schedule(
    workspace_id: int, body: ApplyRequest, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Lưu lịch (có thể đã được trưởng đội sửa tay): tính lại giờ và kiểm tra khả thi trước khi ghi."""
    if _owner(db, workspace_id, user.id).role != "owner":
        raise HTTPException(status_code=403, detail="Chỉ trưởng đội được lập lịch")
    tz, techs, _, _, _, _ = _inputs(db, workspace_id, body.model_copy(update={"include_scheduled": True}))
    tech_by_id = {t.id: t for t in techs}
    seen: set[int] = set()
    applied, applied_notify = [], []
    for route in body.routes:
        tech = tech_by_id.get(route.technician_id)
        if tech is None:
            raise HTTPException(status_code=422, detail=f"Kỹ thuật viên {route.technician_id} chưa có điểm xuất phát")
        jobs, cases = [], []
        for case_id in route.case_ids:
            case = db.get(ServiceCase, case_id)
            if not case or case.workspace_id != workspace_id or case_id in seen:
                raise HTTPException(status_code=422, detail=f"Ca {case_id} không hợp lệ hoặc bị lặp")
            if case.status not in ACTIVE_STATUSES:
                # Ca đã xong/huỷ không được xếp lại (và không gửi lại case.scheduled sang CRM).
                raise HTTPException(status_code=422, detail=f"Ca {case_id} đã đóng, không thể xếp lịch")
            if case.latitude is None:
                raise HTTPException(status_code=422, detail=f"Ca {case_id} chưa có toạ độ")
            seen.add(case_id)
            earliest = _local_minutes(case.window_start, body.date, tz)
            latest = _local_minutes(case.window_end, body.date, tz)
            jobs.append(
                Job(case.id, case.latitude, case.longitude, case.est_minutes or 60, case.priority, earliest, latest)
            )
            cases.append(case)
        stops = _simulate(tech, jobs, body.speed_kmh)
        if stops is None:
            raise HTTPException(
                status_code=422, detail=f"Tuyến của kỹ thuật viên {tech.id} vi phạm khung giờ hoặc ca làm"
            )
        day0 = datetime.combine(body.date, time(0), tz)
        for order, (case, stop) in enumerate(zip(cases, stops, strict=False), start=1):
            case.assigned_to = tech.id
            case.scheduled_start = (day0 + timedelta(minutes=stop.start)).astimezone(UTC)
            case.route_order = order
            db.add(
                ServiceCaseEvent(
                    case_id=case.id,
                    actor_id=user.id,
                    event_type="scheduled",
                    detail=f"Xếp lịch {body.date.isoformat()} — thứ tự {order}, bắt đầu "
                    f"{(day0 + timedelta(minutes=stop.start)).strftime('%H:%M')}",
                )
            )
            applied.append(case.id)
            from field.integrations import enqueue_case_event

            queued = enqueue_case_event(db, case, "case.scheduled")
            if queued:
                applied_notify.append(case.id)
    db.commit()
    if applied_notify:
        from field.integrations import kick

        kick()
    return {"scheduled": applied}


@router.get("/workspaces/{workspace_id}/schedule")
def day_schedule(
    workspace_id: int,
    day: date = Query(alias="date"),
    tz_offset_minutes: int = Query(420, ge=-720, le=840),
    technician_id: int | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _owner(db, workspace_id, user.id)
    tz = timezone(timedelta(minutes=tz_offset_minutes))
    start = datetime.combine(day, time(0), tz).astimezone(UTC)
    query = select(ServiceCase).where(
        ServiceCase.workspace_id == workspace_id,
        ServiceCase.scheduled_start >= start,
        ServiceCase.scheduled_start < start + timedelta(days=1),
    )
    if technician_id is not None:
        query = query.where(ServiceCase.assigned_to == technician_id)
    cases = db.scalars(query.order_by(ServiceCase.assigned_to, ServiceCase.route_order)).all()
    emails = _emails(db, {c.assigned_to for c in cases if c.assigned_to})
    routes: dict[int, list] = {}
    for c in cases:
        routes.setdefault(c.assigned_to, []).append(
            {
                "case_id": c.id,
                "title": c.title,
                "address": c.address,
                "status": c.status,
                "priority": c.priority,
                "latitude": c.latitude,
                "longitude": c.longitude,
                "route_order": c.route_order,
                "scheduled_start": _aware(c.scheduled_start).astimezone(tz).isoformat(),
            }
        )
    return {
        "date": day.isoformat(),
        "routes": [{"technician_id": t, "technician": emails.get(t), "stops": s} for t, s in routes.items()],
    }
