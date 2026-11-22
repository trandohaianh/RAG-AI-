"""Shared field-service workspace, cases, checklists, and audit events."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, event, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class FieldWorkspace(Base):
    __tablename__ = "field_workspaces"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    members = relationship("FieldWorkspaceMember", back_populates="workspace", cascade="all, delete-orphan")
    cases = relationship("ServiceCase", back_populates="workspace", cascade="all, delete-orphan")


class FieldWorkspaceMember(Base):
    __tablename__ = "field_workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_field_workspace_user"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("field_workspaces.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="technician")
    # Điều phối tuyến: điểm xuất phát và ca làm của kỹ thuật viên (giờ địa phương "HH:MM").
    base_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    base_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    shift_start: Mapped[str] = mapped_column(String(5), default="08:00")
    shift_end: Mapped[str] = mapped_column(String(5), default="17:00")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace = relationship("FieldWorkspace", back_populates="members")


class ServiceCase(Base):
    __tablename__ = "field_service_cases"
    __table_args__ = (Index("ix_field_case_external", "workspace_id", "external_source", "external_ref", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("field_workspaces.id"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    customer_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    device_model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(160), nullable=True)
    symptom: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    # Điều phối: vị trí khách hàng, ưu tiên, khung giờ hẹn, thời gian xử lý ước tính, lịch đã xếp.
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    priority: Mapped[str] = mapped_column(String(10), default="normal")
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    est_minutes: Mapped[int] = mapped_column(Integer, default=60)
    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    route_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Nguồn ngoài (CRM ticket / NMS alarm) — duy nhất theo workspace.
    external_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Thời điểm ca chuyển sang "resolved" (KPI ca hoàn tất theo ngày). Không dùng updated_at:
    # mọi lần sửa sau đó (điều phối, ghi chú) làm ca "hoàn tất" nhảy sang ngày sửa cuối.
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    # M13: vị trí ĐỀ XUẤT (tự tra từ địa chỉ CRM, hoặc kỹ thuật viên gửi), chờ trưởng đội xác nhận.
    # Không dùng để xếp tuyến; xoá khi đã đặt toạ độ hoặc khi đổi địa chỉ.
    location_suggestion_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    workspace = relationship("FieldWorkspace", back_populates="cases")
    steps = relationship("ServiceCaseStep", back_populates="case", cascade="all, delete-orphan")
    events = relationship("ServiceCaseEvent", back_populates="case", cascade="all, delete-orphan")
    photos = relationship("ServiceCasePhoto", back_populates="case", cascade="all, delete-orphan")
    closures = relationship(
        "FieldCaseClosure", back_populates="case", cascade="all, delete-orphan", order_by="FieldCaseClosure.id"
    )

    @property
    def closure(self) -> "FieldCaseClosure | None":
        """M12 — biên bản hiệu lực: bản mới nhất, chỉ khi ca đang ở trạng thái đã hoàn tất."""
        return self.closures[-1] if self.status == "resolved" and self.closures else None


class FieldCaseClosure(Base):
    """M12 — biên bản đóng ca (docs/design/M12_bien_ban_dong_ca.md). Mở lại rồi đóng lại thì có
    thêm bản mới; các bản cũ giữ làm lịch sử."""

    __tablename__ = "field_case_closures"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("field_service_cases.id"), index=True)
    outcome_code: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(Text)
    customer_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Ảnh chữ ký (lưu như ảnh hiện trường, cùng hạn giữ); trống khi khách vắng / từ chối.
    signature_photo_id: Mapped[int | None] = mapped_column(ForeignKey("field_case_photos.id"), nullable=True)
    # Lúc đóng ca có chữ ký không — giữ nguyên kể cả khi ảnh chữ ký sau này hết hạn giữ và bị xoá.
    signed: Mapped[bool] = mapped_column(Boolean, default=False)
    closed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    client_action_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)

    case = relationship("ServiceCase", back_populates="closures")


class FieldGeocodeCache(Base):
    """M13: kết quả tra địa chỉ → toạ độ, riêng từng đội (Q3). Chỉ lưu hash của địa chỉ."""

    __tablename__ = "field_geocode_cache"
    __table_args__ = (UniqueConstraint("workspace_id", "query_hash", name="uq_field_geocode_query"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("field_workspaces.id", ondelete="CASCADE"), index=True)
    query_hash: Mapped[str] = mapped_column(String(64))
    results_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class FieldPushDevice(Base):
    """M15: địa chỉ nhận thông báo đẩy (Expo push token) của một máy đã đăng nhập.

    Một token chỉ thuộc một người: máy đổi người dùng thì token chuyển sang người mới.
    """

    __tablename__ = "field_push_devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(200), unique=True)
    platform: Mapped[str] = mapped_column(String(16), default="android")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FieldPushOutbox(Base):
    """M15: hàng đợi thông báo đẩy. Ghi cùng giao dịch với việc giao ca / xếp lịch; luồng nền
    gom theo từng người (Q4) rồi gửi qua Expo Push. Nội dung chỉ có số ca và giờ (Q2)."""

    __tablename__ = "field_push_outbox"
    __table_args__ = (Index("ix_field_push_outbox_due", "status", "next_attempt_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("field_service_cases.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # assigned | schedule_changed | unassigned
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | sent | skipped | dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    claim: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ServiceCasePhoto(Base):
    """Ảnh hiện trường. Tệp đã được mã hoá lại (bỏ EXIF/GPS) và nằm ngoài thư mục web."""

    __tablename__ = "field_case_photos"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("field_service_cases.id"), index=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    storage_key: Mapped[str] = mapped_column(String(200), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    content_type: Mapped[str] = mapped_column(String(40), default="image/jpeg")
    size_bytes: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    caption: Mapped[str] = mapped_column(String(300), default="")
    client_action_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    case = relationship("ServiceCase", back_populates="photos")


class FieldIntegration(Base):
    """Kết nối CRM/NMS của một đội: webhook vào (ký HMAC) và webhook ra khi ca đổi trạng thái."""

    __tablename__ = "field_integrations"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("field_workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(10), default="crm")  # crm | nms
    public_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    secret: Mapped[str] = mapped_column(String(128))
    outbound_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    default_assignee: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FieldIntegrationEvent(Base):
    """Sự kiện vào đã xử lý — chống lặp theo (integration, event_id)."""

    __tablename__ = "field_integration_events"
    __table_args__ = (UniqueConstraint("integration_id", "event_id", name="uq_integration_event"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("field_integrations.id"), index=True)
    event_id: Mapped[str] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(40))
    case_id: Mapped[int | None] = mapped_column(ForeignKey("field_service_cases.id"), nullable=True)
    outcome: Mapped[str] = mapped_column(String(20))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FieldWebhookDelivery(Base):
    """Hàng đợi webhook ra (outbox): gửi lại với backoff, quá hạn thì dead-letter."""

    __tablename__ = "field_webhook_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("field_integrations.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(40))
    payload_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)  # pending|delivered|dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ServiceCaseStep(Base):
    __tablename__ = "field_service_case_steps"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("field_service_cases.id"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(255))
    instruction: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="todo")
    result_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(default=0)
    completed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    case = relationship("ServiceCase", back_populates="steps")


class ServiceCaseEvent(Base):
    __tablename__ = "field_service_case_events"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("field_service_cases.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(Text, default="")
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    case = relationship("ServiceCase", back_populates="events")

    @property
    def evidence(self):
        from rag.evidence import read_bundle

        return read_bundle(self.evidence_json)["evidence"]

    @property
    def citation_audit(self):
        from rag.evidence import read_bundle

        return read_bundle(self.evidence_json)["citation_audit"]


class FieldClientAction(Base):
    __tablename__ = "field_client_actions"

    client_action_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("field_service_cases.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(32))
    request_hash: Mapped[str] = mapped_column(String(64))
    result_id: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


@event.listens_for(ServiceCase.status, "set")
def _track_resolved_at(target: ServiceCase, value, oldvalue, _initiator) -> None:
    # Mọi đường đóng ca (giao diện, ticket.closed, alarm.cleared) đều đi qua thuộc tính status.
    if value == "resolved" and oldvalue != "resolved":
        target.resolved_at = datetime.now(UTC)
    elif value != "resolved":
        target.resolved_at = None
