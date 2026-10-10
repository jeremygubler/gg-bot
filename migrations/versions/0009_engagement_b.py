"""Phase 9b: Starboard, Geburtstage, Stat-Kanäle, geplante Nachrichten

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "birthdays",
        sa.Column("guild_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("day", sa.SmallInteger(), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.PrimaryKeyConstraint("guild_id", "user_id", name=op.f("pk_birthdays")),
    )
    op.create_index(
        "ix_birthdays_guild_month_day", "birthdays", ["guild_id", "month", "day"], unique=False
    )
    op.create_table(
        "scheduled_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.String(length=1500), nullable=False),
        sa.Column("next_run", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduled_messages")),
    )
    op.create_index(
        "ix_scheduled_messages_guild_state",
        "scheduled_messages",
        ["guild_id", "state"],
        unique=False,
    )
    op.create_table(
        "starboard_entries",
        sa.Column("guild_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("message_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("star_message_id", sa.BigInteger(), nullable=True),
        sa.Column("posting", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("guild_id", "message_id", name=op.f("pk_starboard_entries")),
    )
    op.create_table(
        "starboard_votes",
        sa.Column("guild_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("message_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.PrimaryKeyConstraint(
            "guild_id", "message_id", "user_id", name=op.f("pk_starboard_votes")
        ),
    )
    op.create_table(
        "stat_channels",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("template", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stat_channels")),
        sa.UniqueConstraint("channel_id", name=op.f("uq_stat_channels_channel_id")),
    )
    op.create_index(op.f("ix_stat_channels_guild_id"), "stat_channels", ["guild_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_stat_channels_guild_id"), table_name="stat_channels")
    op.drop_table("stat_channels")
    op.drop_table("starboard_votes")
    op.drop_table("starboard_entries")
    op.drop_index("ix_scheduled_messages_guild_state", table_name="scheduled_messages")
    op.drop_table("scheduled_messages")
    op.drop_index("ix_birthdays_guild_month_day", table_name="birthdays")
    op.drop_table("birthdays")
