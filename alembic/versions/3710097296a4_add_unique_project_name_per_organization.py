"""Add unique project name per organization.

Revision ID: 3710097296a4
Revises: 220987db010e
"""

from typing import Sequence, Union

from alembic import op

revision: str = "3710097296a4"
down_revision: Union[str, None] = "220987db010e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "projects_organization_id_name_key",
        "projects",
        ["organization_id", "name"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "projects_organization_id_name_key",
        "projects",
        type_="unique",
    )
