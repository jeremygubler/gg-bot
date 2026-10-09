"""Phase 2: mod_cases, mod_case_counters, mod_notes, channel_locks

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mod_cases",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("case_no", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("moderator_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("ref_case_no", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mod_cases"),
        sa.UniqueConstraint("guild_id", "case_no", name="uq_mod_cases_guild_id_case_no"),
    )
    op.create_index(
        "ix_mod_cases_guild_id_target_id_created_at",
        "mod_cases",
        ["guild_id", "target_id", "created_at"],
    )
    op.create_table(
        "mod_case_counters",
        sa.Column("guild_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("last_no", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("guild_id", name="pk_mod_case_counters"),
    )
    op.create_table(
        "mod_notes",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mod_notes"),
    )
    op.create_index("ix_mod_notes_guild_id_target_id", "mod_notes", ["guild_id", "target_id"])
    op.create_table(
        "channel_locks",
        sa.Column("guild_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("channel_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("previous_send_messages", sa.Boolean(), nullable=True),
        sa.Column("locked_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("guild_id", "channel_id", name="pk_channel_locks"),
    )


def downgrade() -> None:
    op.drop_table("channel_locks")
    op.drop_table("mod_notes")
    op.drop_table("mod_case_counters")
    op.drop_table("mod_cases")
