"""add_word_count

Revision ID: 0f7184e531d4
Revises: 85f421ecebe9
Create Date: 2025-05-30 21:45:59.609302

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0f7184e531d4'
down_revision: Union[str, None] = '85f421ecebe9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('users', sa.Column('words_count', sa.Integer(), nullable=False, server_default='0'))
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('users', 'words_count')
    # ### end Alembic commands ###
