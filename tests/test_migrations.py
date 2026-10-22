import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_offline_alembic_uses_database_url_and_renders_new_constraints():
    env = os.environ | {"DATABASE_URL": "postgresql+asyncpg://offline:offline@localhost/test"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "driver://" not in result.stdout
    assert "SET NULL (assignee_id)" in result.stdout
    assert "ix_tasks_organization_status" in result.stdout
    assert "ix_tasks_organization_assignee" in result.stdout
