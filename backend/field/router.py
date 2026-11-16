"""Field-service workspace, case, and technician workflow API."""
import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from rag.evidence import EvidenceOut, CitationAudit, public_bundle
from auth.models import User
from auth.schemas import CurrentUser
from database import get_db
from dependencies import get_current_user
from saas.limits import enforce_query_quota
from field.models import (
    FieldClientAction,
    FieldWorkspace,
    FieldWorkspaceMember,
    ServiceCase,
    ServiceCaseEvent,
    ServiceCaseStep,
)

router = APIRouter(prefix="/field", tags=["field operations"])

CASE_STATUSES = {"open", "in_progress", "waiting", "escalated", "resolved"}
STEP_STATUSES = {"todo", "done", "blocked"}
# Nhãn tiếng Việt cho nhật ký ca (mã trạng thái vẫn lưu ở event_type / trường status).
CASE_STATUS_VI = {"open": "Mới tiếp nhận", "in_progress": "Đang xử lý", "waiting": "Chờ khách hàng",
                  "escalated": "Chuyển cấp", "resolved": "Đã hoàn tất"}
STEP_STATUS_VI = {"todo": "chưa làm", "done": "đã xong", "blocked": "bị vướng"}


class WorkspaceOut(BaseModel):
    id: int
    name: str
    role: str
    member_count: int


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)


class MemberCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class MemberOut(BaseModel):
    user_id: int
    email: str
    role: str
    base_latitude: float | None = None
    base_longitude: float | None = None
    shift_start: str = "08:00"
    shift_end: str = "17:00"


class CaseCreate(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    symptom: str = Field(min_length=3, max_length=5000)
    customer_label: str | None = Field(default=None, max_length=160)
    address: str | None = Field(default=None, max_length=500)
    device_model: str | None = Field(default=None, max_length=160)
    serial_number: str | None = Field(default=None, max_length=160)
    assigned_to: int | None = None


class CaseUpdate(BaseModel):
    status: str | None = None
    assigned_to: int | None = None
    client_action_id: str | None = Field(default=None, min_length=8, max_length=100)


class StepCreate(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    instruction: str = Field(default="", max_length=5000)
    source: str | None = Field(default=None, max_length=255)
    client_action_id: str | None = Field(default=None, min_length=8, max_length=100)


class StepUpdate(BaseModel):
    status: str
    result_note: str | None = Field(default=None, max_length=3000)
    client_action_id: str | None = Field(default=None, min_length=8, max_length=100)


class EventCreate(BaseModel):
    detail: str = Field(min_length=1, max_length=5000)
    client_action_id: str | None = Field(default=None, min_length=8, max_length=100)


class AssistRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


class EventOut(BaseModel):
    evidence: list[EvidenceOut] = Field(default_factory=list)
    citation_audit: CitationAudit | None = None
    id: int
    actor_id: int
    event_type: str
    detail: str
    created_at: datetime

    model_config = {"from_attributes": True}


class StepOut(BaseModel):
    id: int
    title: str
    instruction: str
    source: str | None
    status: str
    result_note: str | None
    position: int
    completed_by: int | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class CaseOut(BaseModel):
    id: int
    workspace_id: int
    created_by: int
    assigned_to: int | None
    title: str
    customer_label: str | None
    address: str | None
    device_model: str | None
    serial_number: str | None
    symptom: str
    status: str
    created_at: datetime
    updated_at: datetime
    latitude: float | None = None
    longitude: float | None = None
    priority: str = "normal"
    window_start: datetime | None = None
    window_end: datetime | None = None
    est_minutes: int = 60
    scheduled_start: datetime | None = None
    route_order: int | None = None
    external_source: str | None = None
    external_ref: str | None = None
    steps: list[StepOut] = []
    events: list[EventOut] = []

    model_config = {"from_attributes": True}


def _workspace_for_user(db: Session, workspace_id: int, user_id: int) -> FieldWorkspace:
    workspace = db.scalar(
        select(FieldWorkspace)
        .join(FieldWorkspaceMember)
        .where(
            FieldWorkspace.id == workspace_id,
            FieldWorkspaceMember.user_id == user_id,
        )
    )
    if not workspace:
        raise HTTPException(status_code=404, detail="Không tìm thấy đội")
    return workspace


def _case_for_user(db: Session, case_id: int, user_id: int) -> ServiceCase:
    case = db.scalar(
        select(ServiceCase)
        .join(
            FieldWorkspaceMember,
            FieldWorkspaceMember.workspace_id == ServiceCase.workspace_id,
        )
        .where(
            ServiceCase.id == case_id,
            FieldWorkspaceMember.workspace_id == ServiceCase.workspace_id,
            FieldWorkspaceMember.user_id == user_id,
        )
        .options(selectinload(ServiceCase.steps), selectinload(ServiceCase.events))
    )
    if not case:
        raise HTTPException(status_code=404, detail="Không tìm thấy ca sửa")
    case.steps.sort(key=lambda step: (step.position, step.id))
    case.events.sort(key=lambda event: (event.created_at, event.id))
    return case


def _add_event(
    db: Session, case_id: int, actor_id: int, event_type: str, detail: str, *, evidence: dict | None = None
) -> None:
    db.add(
        ServiceCaseEvent(
            case_id=case_id,
            actor_id=actor_id,
            event_type=event_type,
            detail=detail,
            evidence_json=json.dumps(evidence, ensure_ascii=False) if evidence else None,
        )
    )


def _claim_client_action(
    db: Session,
    *,
    client_action_id: str | None,
    case_id: int,
    actor_id: int,
    action_type: str,
    request_body: BaseModel,
    target: dict | None = None,
) -> tuple[FieldClientAction | None, bool]:
    if client_action_id is None:
        return None, False

    body = request_body.model_dump(exclude={"client_action_id"}, exclude_unset=True, mode="json")

    def _digest(payload: dict) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    # Đối tượng trong path (vd. step_id) là một phần của thao tác: cùng mã + cùng body nhưng
    # khác bước không được coi là "đã áp dụng" rồi lặng lẽ bỏ qua.
    request_hash = _digest({"body": body, "target": target}) if target else _digest(body)
    # Bản ghi cũ (trước khi thêm target) chỉ băm body: vẫn nhận replay của chúng.
    legacy_hash = _digest(body)

    def _check(existing: FieldClientAction) -> tuple[FieldClientAction, bool]:
        if (
            existing.case_id != case_id
            or existing.actor_id != actor_id
            or existing.action_type != action_type
            or existing.request_hash not in {request_hash, legacy_hash}
        ):
            raise HTTPException(status_code=409, detail="Mã thao tác đã được sử dụng")
        return existing, True

    existing = db.get(FieldClientAction, client_action_id)
    if existing:
        return _check(existing)

    action = FieldClientAction(
        client_action_id=client_action_id,
        case_id=case_id,
        actor_id=actor_id,
        action_type=action_type,
        request_hash=request_hash,
    )
    db.add(action)
    try:
        db.flush()
    except IntegrityError:
        # Hai lần gửi lại đồng thời cùng mã: bản kia đã ghi trước. Trả kết quả replay như
        # khi đến tuần tự, thay vì 500.
        db.rollback()
        existing = db.get(FieldClientAction, client_action_id)
        if existing is None:
            raise
        return _check(existing)
    return action, False


@router.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    memberships = db.scalars(
        select(FieldWorkspaceMember)
        .where(FieldWorkspaceMember.user_id == user.id)
        .options(selectinload(FieldWorkspaceMember.workspace).selectinload(FieldWorkspace.members))
    ).all()
    if not memberships:
        workspace = FieldWorkspace(
            name=f"Đội kỹ thuật {user.email.split('@', 1)[0]}",
            created_by=user.id,
        )
        db.add(workspace)
        db.flush()
        membership = FieldWorkspaceMember(
            workspace_id=workspace.id, user_id=user.id, role="owner"
        )
        db.add(membership)
        db.commit()
        db.refresh(workspace)
        return [
            WorkspaceOut(
                id=workspace.id,
                name=workspace.name,
                role="owner",
                member_count=1,
            )
        ]
    return [
        WorkspaceOut(
            id=membership.workspace.id,
            name=membership.workspace.name,
            role=membership.role,
            member_count=len(membership.workspace.members),
        )
        for membership in memberships
    ]


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
def create_workspace(
    body: WorkspaceCreate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    workspace = FieldWorkspace(name=body.name.strip(), created_by=user.id)
    db.add(workspace)
    db.flush()
    db.add(
        FieldWorkspaceMember(
            workspace_id=workspace.id, user_id=user.id, role="owner"
        )
    )
    db.commit()
    db.refresh(workspace)
    return WorkspaceOut(
        id=workspace.id, name=workspace.name, role="owner", member_count=1
    )


@router.get("/workspaces/{workspace_id}/members", response_model=list[MemberOut])
def list_members(
    workspace_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    workspace = _workspace_for_user(db, workspace_id, user.id)
    rows = db.execute(
        select(FieldWorkspaceMember, User)
        .join(User, User.id == FieldWorkspaceMember.user_id)
        .where(FieldWorkspaceMember.workspace_id == workspace.id)
        .order_by(FieldWorkspaceMember.created_at)
    ).all()
    return [
        MemberOut(user_id=member.user_id, email=account.email, role=member.role,
                  base_latitude=member.base_latitude, base_longitude=member.base_longitude,
                  shift_start=member.shift_start or "08:00", shift_end=member.shift_end or "17:00")
        for member, account in rows
    ]


@router.post(
    "/workspaces/{workspace_id}/members", response_model=MemberOut, status_code=201
)
def add_member(
    workspace_id: int,
    body: MemberCreate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    workspace = _workspace_for_user(db, workspace_id, user.id)
    actor_membership = db.scalar(
        select(FieldWorkspaceMember).where(
            FieldWorkspaceMember.workspace_id == workspace.id,
            FieldWorkspaceMember.user_id == user.id,
        )
    )
    if not actor_membership or actor_membership.role != "owner":
        raise HTTPException(status_code=403, detail="Chỉ trưởng đội được thêm thành viên")
    from auth.service import find_active_user_by_email

    # Email lưu giữ nguyên hoa/thường: so sánh chuỗi đã lower với cột gốc bỏ sót "Tech@x.com".
    account = find_active_user_by_email(db, body.email)
    if not account:
        raise HTTPException(
            status_code=404,
            detail="Email chưa có tài khoản. Thành viên cần đăng ký trước khi được thêm.",
        )
    existing = db.scalar(
        select(FieldWorkspaceMember).where(
            FieldWorkspaceMember.workspace_id == workspace.id,
            FieldWorkspaceMember.user_id == account.id,
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="Người dùng đã ở trong đội")
    membership = FieldWorkspaceMember(
        workspace_id=workspace.id, user_id=account.id, role="technician"
    )
    db.add(membership)
    db.commit()
    return MemberOut(user_id=account.id, email=account.email, role="technician")


@router.get("/cases", response_model=list[CaseOut])
def list_cases(
    workspace_id: int = Query(gt=0),
    status: str | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _workspace_for_user(db, workspace_id, user.id)
    query = (
        select(ServiceCase)
        .where(ServiceCase.workspace_id == workspace_id)
        .options(selectinload(ServiceCase.steps), selectinload(ServiceCase.events))
        .order_by(ServiceCase.updated_at.desc())
    )
    if status:
        if status not in CASE_STATUSES:
            raise HTTPException(status_code=422, detail="Trạng thái ca sửa không hợp lệ")
        query = query.where(ServiceCase.status == status)
    cases = db.scalars(query.limit(200)).all()
    for case in cases:
        case.steps.sort(key=lambda step: (step.position, step.id))
        case.events.sort(key=lambda event: (event.created_at, event.id))
    return cases


@router.post("/workspaces/{workspace_id}/cases", response_model=CaseOut, status_code=201)
def create_case(
    workspace_id: int,
    body: CaseCreate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _workspace_for_user(db, workspace_id, user.id)
    if body.assigned_to is not None:
        assignee = db.scalar(
            select(FieldWorkspaceMember).where(
                FieldWorkspaceMember.workspace_id == workspace_id,
                FieldWorkspaceMember.user_id == body.assigned_to,
            )
        )
        if not assignee:
            raise HTTPException(status_code=422, detail="Người được giao không thuộc đội")
    case = ServiceCase(
        workspace_id=workspace_id,
        created_by=user.id,
        assigned_to=body.assigned_to,
        title=body.title.strip(),
        customer_label=body.customer_label,
        address=body.address,
        device_model=body.device_model,
        serial_number=body.serial_number,
        symptom=body.symptom.strip(),
    )
    db.add(case)
    db.flush()
    _add_event(db, case.id, user.id, "created", "Tạo ca sửa mới")
    db.commit()
    return _case_for_user(db, case.id, user.id)


@router.get("/cases/{case_id}", response_model=CaseOut)
def get_case(
    case_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _case_for_user(db, case_id, user.id)


def _member_role(db: Session, workspace_id: int, user_id: int) -> str | None:
    return db.scalar(
        select(FieldWorkspaceMember.role).where(
            FieldWorkspaceMember.workspace_id == workspace_id,
            FieldWorkspaceMember.user_id == user_id,
        )
    )


def _require_case_edit_rights(db: Session, case: ServiceCase, user_id: int, body: "CaseUpdate") -> None:
    """Trưởng đội sửa mọi thứ. Kỹ thuật viên:

    - chỉ được nhận ca chưa có người phụ trách cho chính mình (không giao/huỷ giao ca người khác);
    - chỉ được đổi trạng thái ca giao cho mình, hoặc ca chưa giao do chính mình tạo.
    Quyền giao việc phải khớp với lập lịch (cũng chỉ trưởng đội), vì mỗi lần giao còn gửi
    webhook `case.assigned` sang CRM.
    """
    if _member_role(db, case.workspace_id, user_id) == "owner":
        return
    assignee = case.assigned_to
    if "assigned_to" in body.model_fields_set and body.assigned_to != case.assigned_to:
        if not (case.assigned_to is None and body.assigned_to == user_id):
            raise HTTPException(status_code=403, detail="Chỉ trưởng đội được giao ca cho người khác")
        assignee = user_id
    if body.status is not None and body.status != case.status:
        own = assignee == user_id or (assignee is None and case.created_by == user_id)
        if not own:
            raise HTTPException(status_code=403, detail="Chỉ được đổi trạng thái ca giao cho bạn")


@router.patch("/cases/{case_id}", response_model=CaseOut)
def update_case(
    case_id: int,
    body: CaseUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    case = _case_for_user(db, case_id, user.id)
    action, already_applied = _claim_client_action(
        db,
        client_action_id=body.client_action_id,
        case_id=case.id,
        actor_id=user.id,
        action_type="case_update",
        request_body=body,
    )
    if already_applied:
        return case
    _require_case_edit_rights(db, case, user.id, body)
    notify: list[str] = []
    if body.status is not None:
        if body.status not in CASE_STATUSES:
            raise HTTPException(status_code=422, detail="Trạng thái ca sửa không hợp lệ")
        if case.status != body.status:
            case.status = body.status
            _add_event(db, case.id, user.id, "status", f"Chuyển trạng thái: {CASE_STATUS_VI.get(body.status, body.status)}")
            notify.append("case.status_changed")
    if "assigned_to" in body.model_fields_set:
        if body.assigned_to is not None:
            assignee = db.scalar(
                select(FieldWorkspaceMember).where(
                    FieldWorkspaceMember.workspace_id == case.workspace_id,
                    FieldWorkspaceMember.user_id == body.assigned_to,
                )
            )
            if not assignee:
                raise HTTPException(
                    status_code=422, detail="Người được giao không thuộc đội"
                )
        case.assigned_to = body.assigned_to
        _add_event(db, case.id, user.id, "assigned", "Cập nhật người phụ trách")
        notify.append("case.assigned")
    from field.integrations import enqueue_case_event, kick

    queued = sum(enqueue_case_event(db, case, event_type) for event_type in notify)
    db.commit()
    if queued:
        kick()
    return _case_for_user(db, case.id, user.id)


@router.post("/cases/{case_id}/steps", response_model=StepOut, status_code=201)
def create_step(
    case_id: int,
    body: StepCreate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    case = _case_for_user(db, case_id, user.id)
    action, already_applied = _claim_client_action(
        db,
        client_action_id=body.client_action_id,
        case_id=case.id,
        actor_id=user.id,
        action_type="step_create",
        request_body=body,
    )
    if already_applied:
        step = db.scalar(
            select(ServiceCaseStep).where(
                ServiceCaseStep.id == action.result_id,
                ServiceCaseStep.case_id == case.id,
            )
        )
        if not step:
            raise HTTPException(status_code=409, detail="Không thể khôi phục bước đã đồng bộ")
        return step
    step = ServiceCaseStep(
        case_id=case.id,
        created_by=user.id,
        title=body.title.strip(),
        instruction=body.instruction.strip(),
        source=body.source,
        position=len(case.steps),
    )
    db.add(step)
    db.flush()
    _add_event(db, case.id, user.id, "step_added", f"Thêm bước: {step.title}")
    if action:
        action.result_id = step.id
    db.commit()
    db.refresh(step)
    return step


@router.patch("/cases/{case_id}/steps/{step_id}", response_model=StepOut)
def update_step(
    case_id: int,
    step_id: int,
    body: StepUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    case = _case_for_user(db, case_id, user.id)
    if body.status not in STEP_STATUSES:
        raise HTTPException(status_code=422, detail="Trạng thái checklist không hợp lệ")
    step = db.scalar(
        select(ServiceCaseStep).where(
            ServiceCaseStep.id == step_id, ServiceCaseStep.case_id == case.id
        )
    )
    if not step:
        raise HTTPException(status_code=404, detail="Không tìm thấy bước checklist")
    action, already_applied = _claim_client_action(
        db,
        client_action_id=body.client_action_id,
        case_id=case.id,
        actor_id=user.id,
        action_type="step_update",
        request_body=body,
        target={"step_id": step_id},
    )
    if already_applied:
        return step
    step.status = body.status
    step.result_note = body.result_note
    step.completed_by = user.id if body.status == "done" else None
    step.completed_at = datetime.now(timezone.utc) if body.status == "done" else None
    _add_event(db, case.id, user.id, "step_updated", f"{step.title}: {STEP_STATUS_VI.get(body.status, body.status)}")
    db.commit()
    db.refresh(step)
    return step


@router.post("/cases/{case_id}/events", response_model=EventOut, status_code=201)
def add_case_event(
    case_id: int,
    body: EventCreate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    case = _case_for_user(db, case_id, user.id)
    action, already_applied = _claim_client_action(
        db,
        client_action_id=body.client_action_id,
        case_id=case.id,
        actor_id=user.id,
        action_type="event_create",
        request_body=body,
    )
    if already_applied:
        event = db.scalar(
            select(ServiceCaseEvent).where(
                ServiceCaseEvent.id == action.result_id,
                ServiceCaseEvent.case_id == case.id,
            )
        )
        if not event:
            raise HTTPException(status_code=409, detail="Không thể khôi phục ghi nhận đã đồng bộ")
        return event
    event = ServiceCaseEvent(
        case_id=case.id, actor_id=user.id, event_type="field_note", detail=body.detail.strip()
    )
    db.add(event)
    db.flush()
    if action:
        action.result_id = event.id
    db.commit()
    db.refresh(event)
    return event


@router.post("/cases/{case_id}/assist")
async def assist_case(
    case_id: int,
    body: AssistRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    case = _case_for_user(db, case_id, user.id)
    if case.status == "resolved":
        raise HTTPException(status_code=409, detail="Ca đã đóng; không thể tạo hướng dẫn mới")
    from rag.chain import RagGenerationError, answer_rag_once

    question = (
        "Bối cảnh ca sửa hiện trường:\n"
        f"Thiết bị/model: {case.device_model or 'chưa xác định'}\n"
        f"Triệu chứng: {case.symptom}\n"
        f"Địa điểm: {case.address or 'không cung cấp'}\n\n"
        "Chỉ hướng dẫn bước kiểm tra an toàn dựa trên tài liệu đã lập chỉ mục. "
        "Không đề xuất thao tác thay đổi cấu hình hoặc điện nguy hiểm nếu tài liệu không nêu rõ; "
        "nêu rõ khi cần chuyển cấp cho kỹ thuật viên chuyên trách.\n\n"
        f"Câu hỏi kỹ thuật viên: {body.question}"
    )
    from costs.budget import enforce_budget
    from costs.context import set_request_usage

    enforce_budget(db, user.id)
    set_request_usage(user_id=user.id, workspace_id=case.workspace_id, feature="field_assistant")
    enforce_query_quota(db, user.id, user.subscription_plan)
    evidence_bundle = {}
    try:
        answer, sources = await answer_rag_once(
            user.id, question, workspace_id=case.workspace_id, evidence_out=evidence_bundle
        )
    except RagGenerationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    _case_for_user(db, case_id, user.id)  # Recheck membership after generation.
    source_text = ", ".join(sources) if sources else "không có nguồn truy xuất"
    _add_event(
        db,
        case.id,
        user.id,
        "assistant_recommendation",
        f"Câu hỏi: {body.question.strip()}\nGợi ý: {answer}\nNguồn: {source_text}",
        evidence=evidence_bundle,
    )
    db.commit()
    return {"answer": answer, "sources": sources, **public_bundle(evidence_bundle)}


@router.get("/cases/{case_id}/events/{event_id}/evidence/{reference_id}")
def get_case_evidence(case_id: int, event_id: int, reference_id: str,
                      user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    from rag.evidence import read_bundle, evidence_status
    case = _case_for_user(db, case_id, user.id)
    event = next((event for event in case.events if event.id == event_id), None)
    if event is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy bằng chứng")
    bundle = read_bundle(event.evidence_json)
    item = next((item for item in bundle['evidence'] if item['reference_id'] == reference_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy bằng chứng")
    from fastapi.responses import JSONResponse
    return JSONResponse({"evidence": item, "document_status": evidence_status(bundle, f"workspace_{case.workspace_id}")}, headers={"Cache-Control": "no-store"})
