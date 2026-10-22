"""Enforce task assignees as organization members and index task filters.

Revision ID: 5f17b4c8a301
Revises: cd34b15bbcf4
"""

from typing import Sequence, Union

from alembic import op

revision: str = "5f17b4c8a301"
down_revision: Union[str, None] = "cd34b15bbcf4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The named key is the target of the composite assignee foreign key.
    op.create_unique_constraint(
        "uq_memberships_user_organization", "memberships", ["user_id", "organization_id"]
    )
    op.drop_constraint("memberships_user_id_organization_id_key", "memberships", type_="unique")

    # Preserve tasks whose assignee was removed before this invariant existed.
    op.execute(
        """UPDATE tasks AS task SET assignee_id = NULL
        WHERE assignee_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM memberships
            WHERE memberships.user_id = task.assignee_id
              AND memberships.organization_id = task.organization_id
        )"""
    )
    op.drop_constraint("tasks_assignee_id_fkey", "tasks", type_="foreignkey")
    op.create_foreign_key(
        "fk_tasks_assignee_membership",
        "tasks",
        "memberships",
        ["assignee_id", "organization_id"],
        ["user_id", "organization_id"],
        ondelete="SET NULL (assignee_id)",
    )
    op.create_index("ix_tasks_organization_status", "tasks", ["organization_id", "status"])
    op.create_index(
        "ix_tasks_organization_assignee", "tasks", ["organization_id", "assignee_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_organization_assignee", table_name="tasks")
    op.drop_index("ix_tasks_organization_status", table_name="tasks")
    op.drop_constraint("fk_tasks_assignee_membership", "tasks", type_="foreignkey")
    op.create_foreign_key(
        "tasks_assignee_id_fkey", "tasks", "users", ["assignee_id"], ["id"], ondelete="SET NULL"
    )
    op.create_unique_constraint(
        "memberships_user_id_organization_id_key", "memberships", ["user_id", "organization_id"]
    )
    op.drop_constraint("uq_memberships_user_organization", "memberships", type_="unique")
