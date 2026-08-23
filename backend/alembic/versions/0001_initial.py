"""Initial modular-monolith schema.

Revision ID: 0001
Revises:
Create Date: 2026-08-23
"""

from alembic import op
from app.core.database import Base
from app.infrastructure import model_registry as _model_registry  # noqa: F401

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
