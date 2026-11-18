"""подтверждение записи на мероприятие

Заявка ждёт решения организатора: «ждёт подтверждения» → «записан» или
«заявка отклонена». Дата решения — в новом поле participations.decided_at.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-18 23:10:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('participations', sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('participations') as batch:
        batch.drop_column('decided_at')
