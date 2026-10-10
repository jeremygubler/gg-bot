"""Phase 5: xp_members, rank_card_styles

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "xp_members",
        sa.Column("guild_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("xp", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("msg_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("voice_seconds", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("last_xp_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("guild_id", "user_id", name="pk_xp_members"),
    )
    op.create_index("ix_xp_members_guild_id_xp", "xp_members", ["guild_id", "xp"])
    op.create_table(
        "rank_card_styles",
        sa.Column("guild_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("style", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("guild_id", "user_id", name="pk_rank_card_styles"),
    )


def downgrade() -> None:
    op.drop_table("rank_card_styles")
    op.drop_table("xp_members")
