"""Collapse server catalog state to one repository and one upload quota.

Revision ID: 20260729_02
Revises: 20260729_01
Create Date: 2026-07-29
"""

from alembic import op
import sqlalchemy as sa


revision = "20260729_02"
down_revision = "20260729_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF (
                SELECT count(DISTINCT repository_id)
                FROM (
                    SELECT repository_id FROM repositories
                    UNION ALL
                    SELECT repository_id FROM index_generations
                    UNION ALL
                    SELECT repository_id FROM index_snapshots
                    UNION ALL
                    SELECT repository_id FROM upload_intents
                    UNION ALL
                    SELECT repository_id FROM upload_quotas
                ) AS repository_ids
            ) > 1 THEN
                RAISE EXCEPTION
                    'VidXP cannot collapse multiple repositories into one stack';
            END IF;
        END
        $$;
        """
    )

    op.rename_table("repositories", "index_state")
    op.alter_column(
        "index_state",
        "repository_id",
        new_column_name="singleton_id",
    )
    op.execute("UPDATE index_state SET singleton_id = '1'")
    op.alter_column(
        "index_state",
        "singleton_id",
        existing_type=sa.String(length=255),
        type_=sa.String(length=1),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "index_state_singleton",
        "index_state",
        "singleton_id = '1'",
    )

    op.drop_index(
        "index_generations_repository_media",
        table_name="index_generations",
    )
    op.drop_constraint(
        "index_generations_repository_media_generation_key",
        "index_generations",
        type_="unique",
    )
    op.drop_column("index_generations", "repository_id")
    op.create_index(
        "index_generations_media",
        "index_generations",
        ["media_id"],
        unique=False,
    )

    op.drop_index(
        "index_snapshots_repository_created",
        table_name="index_snapshots",
    )
    op.drop_column("index_snapshots", "repository_id")
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
    op.execute(
        """
        INSERT INTO upload_quota (singleton_id, reserved_bytes)
        SELECT '1', COALESCE(sum(reserved_bytes), 0)
        FROM upload_quotas
        """
    )
    op.drop_table("upload_quotas")

    op.drop_index(
        "upload_intents_owner_state",
        table_name="upload_intents",
    )
    op.drop_constraint(
        "upload_intents_repository_intent_key",
        "upload_intents",
        type_="unique",
    )
    op.drop_column("upload_intents", "owner_subject")
    op.drop_column("upload_intents", "repository_id")


def downgrade() -> None:
    op.add_column(
        "upload_intents",
        sa.Column("repository_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "upload_intents",
        sa.Column("owner_subject", sa.String(length=255), nullable=True),
    )
    op.execute(
        """
        UPDATE upload_intents
        SET repository_id = 'default', owner_subject = 'shared'
        """
    )
    op.alter_column(
        "upload_intents",
        "repository_id",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.alter_column(
        "upload_intents",
        "owner_subject",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.create_unique_constraint(
        "upload_intents_repository_intent_key",
        "upload_intents",
        ["repository_id", "intent_id"],
    )
    op.create_index(
        "upload_intents_owner_state",
        "upload_intents",
        ["repository_id", "owner_subject", "state"],
        unique=False,
    )

    op.create_table(
        "upload_quotas",
        sa.Column("repository_id", sa.String(length=255), nullable=False),
        sa.Column("owner_subject", sa.String(length=255), nullable=False),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "reserved_bytes >= 0",
            name="upload_quotas_reserved_bytes_nonnegative",
        ),
        sa.PrimaryKeyConstraint("repository_id", "owner_subject"),
    )
    op.execute(
        """
        INSERT INTO upload_quotas (
            repository_id,
            owner_subject,
            reserved_bytes
        )
        SELECT 'default', 'shared', reserved_bytes
        FROM upload_quota
        """
    )
    op.drop_table("upload_quota")

    op.drop_index(
        "index_snapshots_created",
        table_name="index_snapshots",
    )
    op.add_column(
        "index_snapshots",
        sa.Column("repository_id", sa.String(length=255), nullable=True),
    )
    op.execute(
        "UPDATE index_snapshots SET repository_id = 'default'"
    )
    op.alter_column(
        "index_snapshots",
        "repository_id",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.create_index(
        "index_snapshots_repository_created",
        "index_snapshots",
        ["repository_id", "created_at"],
        unique=False,
    )

    op.drop_index(
        "index_generations_media",
        table_name="index_generations",
    )
    op.add_column(
        "index_generations",
        sa.Column("repository_id", sa.String(length=255), nullable=True),
    )
    op.execute(
        "UPDATE index_generations SET repository_id = 'default'"
    )
    op.alter_column(
        "index_generations",
        "repository_id",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.create_unique_constraint(
        "index_generations_repository_media_generation_key",
        "index_generations",
        ["repository_id", "media_id", "generation_id"],
    )
    op.create_index(
        "index_generations_repository_media",
        "index_generations",
        ["repository_id", "media_id"],
        unique=False,
    )

    op.drop_constraint(
        "index_state_singleton",
        "index_state",
        type_="check",
    )
    op.alter_column(
        "index_state",
        "singleton_id",
        existing_type=sa.String(length=1),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
    op.alter_column(
        "index_state",
        "singleton_id",
        new_column_name="repository_id",
    )
    op.execute("UPDATE index_state SET repository_id = 'default'")
    op.rename_table("index_state", "repositories")
