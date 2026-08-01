"""Add capability-authorized multi-file upload sessions.

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
        "upload_sessions",
        sa.Column("session_id", sa.String(length=32), nullable=False),
        sa.Column("request_key", sa.String(length=64), nullable=False),
        sa.Column("selector", sa.String(length=32), nullable=False),
        sa.Column("capability_digest", sa.String(length=64), nullable=False),
        sa.Column("initiating_subject", sa.String(length=255), nullable=False),
        sa.Column("initiating_client_id", sa.String(length=255), nullable=True),
        sa.Column("repository_binding", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("maximum_files", sa.Integer(), nullable=False),
        sa.Column("maximum_file_bytes", sa.BigInteger(), nullable=False),
        sa.Column("maximum_aggregate_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("browser_session_digest", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("session_id"),
        sa.UniqueConstraint("request_key"),
        sa.UniqueConstraint("selector"),
        sa.UniqueConstraint("capability_digest"),
    )
    op.create_index(
        "upload_sessions_expiry",
        "upload_sessions",
        ["expires_at", "state"],
        unique=False,
    )
    op.create_table(
        "upload_session_files",
        sa.Column("session_id", sa.String(length=32), nullable=False),
        sa.Column("client_file_key", sa.String(length=255), nullable=False),
        sa.Column("intent_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("creation_grant_digest", sa.String(length=64), nullable=True),
        sa.Column("creation_grant_expires_at", sa.Text(), nullable=True),
        sa.Column("creation_grant_consumed_at", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["upload_sessions.session_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["intent_id"],
            ["upload_intents.intent_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "client_file_key"),
        sa.UniqueConstraint("intent_id"),
        sa.UniqueConstraint("creation_grant_digest"),
    )


def downgrade() -> None:
    op.drop_table("upload_session_files")
    op.drop_index("upload_sessions_expiry", table_name="upload_sessions")
    op.drop_table("upload_sessions")
