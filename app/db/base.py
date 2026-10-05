import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    pass


class MembershipRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class TaskStatus(str, enum.Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="organization"
    )
    projects: Mapped[list["Project"]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"Organization(id={self.id!r}, name={self.name!r}, created_at={self.created_at!r})"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    memberships: Mapped[list["Membership"]] = relationship(back_populates="user")
    assigned_tasks: Mapped[list["Task"]] = relationship(back_populates="assignee")

    def __repr__(self) -> str:
        return f"User(id={self.id!r}, email={self.email!r}, hashed_password={self.hashed_password!r}, is_active={self.is_active!r}, created_at={self.created_at!r})"


class Membership(Base):
    __tablename__ = "memberships"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    role: Mapped[MembershipRole] = mapped_column(
        Enum(
            MembershipRole,
            name="membership_role",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=MembershipRole.MEMBER,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    organization: Mapped["Organization"] = relationship(back_populates="memberships")
    user: Mapped["User"] = relationship(back_populates="memberships")
    __table_args__ = (UniqueConstraint("user_id", "organization_id"),)

    def __repr__(self) -> str:
        return f"Membership(id={self.id!r}, user_id={self.user_id!r}, organization_id={self.organization_id!r}, role={self.role!r}, created_at={self.created_at!r})"


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    organization: Mapped["Organization"] = relationship(back_populates="projects")

    def __repr__(self) -> str:
        return f"Project(id={self.id!r}, organization_id={self.organization_id!r}, name={self.name!r}, created_at={self.created_at!r})"


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(1000))
    status: Mapped[TaskStatus] = mapped_column(
        Enum(
            TaskStatus,
            name="task_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=TaskStatus.TODO,
    )
    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    project: Mapped["Project"] = relationship(back_populates="tasks")
    assignee: Mapped["User | None"] = relationship(back_populates="assigned_tasks")

    def __repr__(self) -> str:
        return f"Task(id={self.id!r}, organization_id={self.organization_id!r}, project_id={self.project_id!r}, title={self.title!r}, description={self.description!r}, status={self.status!r}, assignee_id={self.assignee_id!r}, created_at={self.created_at!r})"
