"""Add composite foreign key for task project organization.

Revision ID: a31c2b8930fc
Revises: 3710097296a4
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a31c2b8930fc"
down_revision: Union[str, None] = "3710097296a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "projects_id_organization_id_key",
        "projects",
        ["id", "organization_id"],
    )

    op.drop_constraint(
        "tasks_project_id_fkey",
        "tasks",
        type_="foreignkey",
    )

    op.create_foreign_key(
        "tasks_project_id_organization_id_fkey",
        "tasks",
        "projects",
        ["project_id", "organization_id"],
        ["id", "organization_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "tasks_project_id_organization_id_fkey",
        "tasks",
        type_="foreignkey",
    )

    op.create_foreign_key(
        "tasks_project_id_fkey",
        "tasks",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "projects_id_organization_id_key",
        "projects",
        type_="unique",
    )
