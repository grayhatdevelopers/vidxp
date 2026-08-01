"""Add bounded browser upload handoff authorization.

Revision ID: 20260801_01
Revises: 20260729_01
Create Date: 2026-08-01
"""

from alembic import op
import sqlalchemy as sa


revision = "20260801_01"
down_revision = "20260729_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "upload_handoffs",
        sa.Column("selector", sa.String(length=32), nullable=False),
        sa.Column("intent_id", sa.String(length=32), nullable=False),
        sa.Column(
            "repository_binding",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("session_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "creation_grant_digest",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("creation_grant_expires_at", sa.Text(), nullable=True),
        sa.Column("creation_grant_consumed_at", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["intent_id"],
            ["upload_intents.intent_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("selector"),
        sa.UniqueConstraint("intent_id"),
        sa.UniqueConstraint("creation_grant_digest"),
    )


def downgrade() -> None:
    op.drop_table("upload_handoffs")
