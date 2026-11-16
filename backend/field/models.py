"""Shared field-service workspace, cases, checklists, and audit events."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class FieldWorkspace(Base):
    __tablename__ = "field_workspaces"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    members = relationship(
        "FieldWorkspaceMember", back_populates="workspace", cascade="all, delete-orphan"
    )
    cases = relationship(
        "ServiceCase", back_populates="workspace", cascade="all, delete-orphan"
    )


class FieldWorkspaceMember(Base):
    __tablename__ = "field_workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_field_workspace_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("field_workspaces.id"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="technician")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    workspace = relationship("FieldWorkspace", back_populates="members")


class ServiceCase(Base):
    __tablename__ = "field_service_cases"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("field_workspaces.id"), index=True
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    assigned_to: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    customer_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    device_model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(160), nullable=True)
    symptom: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    workspace = relationship("FieldWorkspace", back_populates="cases")
    steps = relationship(
        "ServiceCaseStep", back_populates="case", cascade="all, delete-orphan"
    )
    events = relationship(
        "ServiceCaseEvent", back_populates="case", cascade="all, delete-orphan"
    )


class ServiceCaseStep(Base):
    __tablename__ = "field_service_case_steps"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("field_service_cases.id"), index=True
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(255))
    instruction: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="todo")
    result_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(default=0)
    completed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    case = relationship("ServiceCase", back_populates="steps")


class ServiceCaseEvent(Base):
    __tablename__ = "field_service_case_events"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("field_service_cases.id"), index=True
    )
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(Text, default="")
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    case = relationship("ServiceCase", back_populates="events")

    @property
    def evidence(self):
        from rag.evidence import read_bundle
        return read_bundle(self.evidence_json)['evidence']

    @property
    def citation_audit(self):
        from rag.evidence import read_bundle
        return read_bundle(self.evidence_json)['citation_audit']


class FieldClientAction(Base):
    __tablename__ = "field_client_actions"

    client_action_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("field_service_cases.id"), index=True
    )
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(32))
    request_hash: Mapped[str] = mapped_column(String(64))
    result_id: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
