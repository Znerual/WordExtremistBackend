"""add_creativity_score_to_word_submission

Revision ID: d1194e00f082
Revises: 7d1ccbc8434b
Create Date: 2025-05-26 09:33:03.951922

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1194e00f082'
down_revision: Union[str, None] = '7d1ccbc8434b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('word_submissions', sa.Column('creativity_score', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('word_submissions', 'creativity_score')
