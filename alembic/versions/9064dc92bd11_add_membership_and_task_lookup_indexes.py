"""Add indexes for organization, project, and keyset task queries.

Revision ID: 9064dc92bd11
Revises: 5f17b4c8a301
"""

from typing import Sequence, Union

from alembic import op

revision: str = "9064dc92bd11"
down_revision: Union[str, None] = "5f17b4c8a301"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_memberships_organization_id", "memberships", ["organization_id"])
    op.drop_index("ix_tasks_organization_id", table_name="tasks")
    op.create_index("ix_tasks_organization_id_id", "tasks", ["organization_id", "id"])
    op.create_index("ix_tasks_organization_project", "tasks", ["organization_id", "project_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_organization_project", table_name="tasks")
    op.drop_index("ix_tasks_organization_id_id", table_name="tasks")
    op.create_index("ix_tasks_organization_id", "tasks", ["organization_id"])
    op.drop_index("ix_memberships_organization_id", table_name="memberships")
