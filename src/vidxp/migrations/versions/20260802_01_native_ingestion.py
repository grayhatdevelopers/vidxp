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
    with op.batch_alter_table("upload_sessions") as batch:
        batch.add_column(
            sa.Column(
                "transfer_backend",
                sa.String(length=32),
                nullable=False,
                server_default="tus",
            )
        )
        batch.add_column(
            sa.Column(
                "index_after_import",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(
            sa.Column(
                "index_modalities",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            )
        )
    with op.batch_alter_table("upload_intents") as batch:
        batch.add_column(
            sa.Column(
                "transfer_backend",
                sa.String(length=32),
                nullable=False,
                server_default="tus",
            )
        )
        batch.add_column(
            sa.Column(
                "index_after_import",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(
            sa.Column(
                "index_modalities",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            )
        )
        batch.add_column(
            sa.Column("index_job_id", sa.String(length=36), nullable=True)
        )
        batch.add_column(sa.Column("index_command", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("source_path", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("content_sha256", sa.String(length=64), nullable=True)
        )
        batch.add_column(
            sa.Column("failure_code", sa.String(length=128), nullable=True)
        )
        batch.add_column(
            sa.Column("failure_message", sa.String(length=512), nullable=True)
        )
    op.create_index(
        "upload_intents_index_job_id",
        "upload_intents",
        ["index_job_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("upload_intents_index_job_id", table_name="upload_intents")
    with op.batch_alter_table("upload_intents") as batch:
        for name in (
            "failure_message",
            "failure_code",
            "content_sha256",
            "source_path",
            "index_command",
            "index_job_id",
            "index_modalities",
            "index_after_import",
            "transfer_backend",
        ):
            batch.drop_column(name)
    with op.batch_alter_table("upload_sessions") as batch:
        for name in ("index_modalities", "index_after_import", "transfer_backend"):
            batch.drop_column(name)
