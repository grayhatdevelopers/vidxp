"""Add reviewable person identities and labels.

Revision ID: 20260803_01
Revises: 20260802_01
Create Date: 2026-08-03
"""
from alembic import op
import sqlalchemy as sa

revision = "20260803_01"
down_revision = "20260802_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "people",
        sa.Column("person_id", sa.String(length=32), primary_key=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "person_aliases",
        sa.Column(
            "person_id",
            sa.String(length=32),
            sa.ForeignKey("people.person_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("alias", sa.String(length=255), primary_key=True),
    )
    op.create_table(
        "person_references",
        sa.Column("reference_id", sa.String(length=32), primary_key=True),
        sa.Column(
            "person_id",
            sa.String(length=32),
            sa.ForeignKey("people.person_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(length=127), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "person_references_person_id",
        "person_references",
        ["person_id"],
        unique=False,
    )
    op.create_table(
        "person_cluster_links",
        sa.Column(
            "person_id",
            sa.String(length=32),
            sa.ForeignKey("people.person_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("cluster_id", sa.String(length=512), primary_key=True),
        sa.Column("media_id", sa.String(length=32), primary_key=True),
        sa.Column("generation_id", sa.String(length=32), primary_key=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "person_cluster_links_media_id",
        "person_cluster_links",
        ["media_id"],
        unique=False,
    )
    op.create_index(
        "person_cluster_links_cluster_id",
        "person_cluster_links",
        ["cluster_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "person_cluster_links_cluster_id",
        table_name="person_cluster_links",
    )
    op.drop_index(
        "person_cluster_links_media_id",
        table_name="person_cluster_links",
    )
    op.drop_table("person_cluster_links")
    op.drop_index(
        "person_references_person_id",
        table_name="person_references",
    )
    op.drop_table("person_references")
    op.drop_table("person_aliases")
    op.drop_table("people")
