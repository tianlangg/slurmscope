"""initial experiment and run schema"""

from alembic import op

from slurmscope.db import models  # noqa: F401
from slurmscope.db.database import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(bind=op.get_bind())


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind())
