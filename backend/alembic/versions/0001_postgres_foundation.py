"""PostgreSQL tenant, identity and conversation foundation."""

from alembic import op

from app.db.base import Base
from app.db.models import core  # noqa: F401

revision = "0001_postgres_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(bind=op.get_bind())


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind())
