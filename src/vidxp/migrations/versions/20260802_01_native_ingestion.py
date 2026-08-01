"""Persist transfer and automatic-index orchestration metadata.

Revision ID: 20260802_01
Revises: 20260801_01
Create Date: 2026-08-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260802_01"
down_revision = "20260801_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "upload_sessions",
        sa.Column(
            "transfer_backend",
            sa.String(length=32),
            nullable=False,
            server_default="tus",
        ),
    )
    op.add_column(
        "upload_sessions",
        sa.Column(
            "index_after_import", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    op.add_column(
        "upload_sessions",
        sa.Column("index_modalities", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "upload_intents",
        sa.Column(
            "transfer_backend",
            sa.String(length=32),
            nullable=False,
            server_default="tus",
        ),
    )
    op.add_column(
        "upload_intents",
        sa.Column(
            "index_after_import", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    op.add_column(
        "upload_intents",
        sa.Column("index_modalities", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "upload_intents", sa.Column("index_job_id", sa.String(length=36), nullable=True)
    )
    op.add_column("upload_intents", sa.Column("source_path", sa.Text(), nullable=True))
    op.add_column(
        "upload_intents",
        sa.Column("failure_code", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "upload_intents",
        sa.Column("failure_message", sa.String(length=512), nullable=True),
    )
    op.create_unique_constraint(
        "uq_upload_intents_index_job_id", "upload_intents", ["index_job_id"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_upload_intents_index_job_id", "upload_intents", type_="unique"
    )
    for name in (
        "failure_message",
        "failure_code",
        "source_path",
        "index_job_id",
        "index_modalities",
        "index_after_import",
        "transfer_backend",
    ):
        op.drop_column("upload_intents", name)
    for name in ("index_modalities", "index_after_import", "transfer_backend"):
        op.drop_column("upload_sessions", name)
