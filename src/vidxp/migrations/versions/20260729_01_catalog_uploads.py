"""Create the single-repository server catalog.

Revision ID: 20260729_01
Revises:
Create Date: 2026-07-29
"""

from alembic import op
import sqlalchemy as sa


revision = "20260729_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media",
        sa.Column("media_id", sa.String(length=32), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("media_id"),
        sa.UniqueConstraint("sha256"),
    )
    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.String(length=32), nullable=False),
        sa.Column("media_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["media_id"], ["media.media_id"]),
        sa.PrimaryKeyConstraint("artifact_id"),
    )
    op.create_index(
        "artifacts_media_id",
        "artifacts",
        ["media_id"],
        unique=False,
    )
    op.create_table(
        "artifact_requests",
        sa.Column("request_key", sa.String(length=64), nullable=False),
        sa.Column("artifact_id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["artifacts.artifact_id"],
        ),
        sa.PrimaryKeyConstraint("request_key"),
        sa.UniqueConstraint("artifact_id"),
    )
    op.create_table(
        "media_import_requests",
        sa.Column("request_key", sa.String(length=64), nullable=False),
        sa.Column(
            "request_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("media_id", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["media_id"], ["media.media_id"]),
        sa.PrimaryKeyConstraint("request_key"),
    )
    op.create_table(
        "index_state",
        sa.Column("singleton_id", sa.String(length=1), nullable=False),
        sa.Column("active_snapshot_id", sa.String(length=32), nullable=True),
        sa.Column(
            "active_snapshot_sha256",
            sa.String(length=64),
            nullable=True,
        ),
        sa.CheckConstraint(
            "singleton_id = '1'",
            name="index_state_singleton",
        ),
        sa.PrimaryKeyConstraint("singleton_id"),
    )
    op.create_table(
        "index_generations",
        sa.Column("generation_id", sa.String(length=32), nullable=False),
        sa.Column("media_id", sa.String(length=32), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("generation_id"),
    )
    op.create_index(
        "index_generations_media",
        "index_generations",
        ["media_id"],
        unique=False,
    )
    op.create_table(
        "index_snapshots",
        sa.Column("snapshot_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )
    op.create_index(
        "index_snapshots_created",
        "index_snapshots",
        ["created_at"],
        unique=False,
    )
    op.create_table(
        "upload_quota",
        sa.Column("singleton_id", sa.String(length=1), nullable=False),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "reserved_bytes >= 0",
            name="upload_quota_reserved_bytes_nonnegative",
        ),
        sa.CheckConstraint(
            "singleton_id = '1'",
            name="upload_quota_singleton",
        ),
        sa.PrimaryKeyConstraint("singleton_id"),
    )
    op.create_table(
        "upload_intents",
        sa.Column("intent_id", sa.String(length=32), nullable=False),
        sa.Column("request_key", sa.String(length=64), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column(
            "declared_mime_type",
            sa.String(length=127),
            nullable=True,
        ),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("upload_id", sa.String(length=255), nullable=True),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("media_id", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["media_id"], ["media.media_id"]),
        sa.PrimaryKeyConstraint("intent_id"),
        sa.UniqueConstraint("job_id"),
        sa.UniqueConstraint("request_key"),
        sa.UniqueConstraint("upload_id"),
    )
    op.create_index(
        "upload_intents_expiry",
        "upload_intents",
        ["expires_at", "state"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("upload_intents_expiry", table_name="upload_intents")
    op.drop_table("upload_intents")
    op.drop_table("upload_quota")
    op.drop_index(
        "index_snapshots_created",
        table_name="index_snapshots",
    )
    op.drop_table("index_snapshots")
    op.drop_index(
        "index_generations_media",
        table_name="index_generations",
    )
    op.drop_table("index_generations")
    op.drop_table("index_state")
    op.drop_table("media_import_requests")
    op.drop_table("artifact_requests")
    op.drop_index("artifacts_media_id", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_table("media")
