"""Phase 12b: Vorschlaege, Sticky-Nachrichten, temporaere Sprachkanaele

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sticky_messages",
        sa.Column("channel_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.String(length=1500), nullable=False),
        sa.Column("last_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("channel_id", name=op.f("pk_sticky_messages")),
    )
    op.create_index(
        op.f("ix_sticky_messages_guild_id"), "sticky_messages", ["guild_id"], unique=False
    )
    op.create_table(
        "suggestion_counters",
        sa.Column("guild_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("last_no", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("guild_id", name=op.f("pk_suggestion_counters")),
    )
    op.create_table(
        "suggestions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("text", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("decided_by", sa.BigInteger(), nullable=True),
        sa.Column("decision_reason", sa.String(length=500), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suggestions")),
        sa.UniqueConstraint("guild_id", "number", name=op.f("uq_suggestions_guild_id_number")),
    )
    op.create_index(
        "ix_suggestions_guild_status", "suggestions", ["guild_id", "status"], unique=False
    )
    op.create_table(
        "temp_voice_channels",
        sa.Column("channel_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("channel_id", name=op.f("pk_temp_voice_channels")),
    )
    op.create_index(
        op.f("ix_temp_voice_channels_guild_id"), "temp_voice_channels", ["guild_id"], unique=False
    )
    op.create_table(
        "suggestion_votes",
        sa.Column("suggestion_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("vote", sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["suggestion_id"],
            ["suggestions.id"],
            name=op.f("fk_suggestion_votes_suggestion_id_suggestions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("suggestion_id", "user_id", name=op.f("pk_suggestion_votes")),
    )


def downgrade() -> None:
    op.drop_table("suggestion_votes")
    op.drop_index(op.f("ix_temp_voice_channels_guild_id"), table_name="temp_voice_channels")
    op.drop_table("temp_voice_channels")
    op.drop_index("ix_suggestions_guild_status", table_name="suggestions")
    op.drop_table("suggestions")
    op.drop_table("suggestion_counters")
    op.drop_index(op.f("ix_sticky_messages_guild_id"), table_name="sticky_messages")
    op.drop_table("sticky_messages")
