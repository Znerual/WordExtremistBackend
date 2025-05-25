"""add password hash field

Revision ID: a18ce72e6dc3
Revises: a43f8ac0d3bc
Create Date: 2025-05-25 20:24:14.533435

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a18ce72e6dc3'
down_revision: Union[str, None] = 'a43f8ac0d3bc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
