"""Phase 9a: Eigene Befehle, Giveaways, Erinnerungen

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_commands",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("response", sa.Text(), nullable=False),
        sa.Column("ephemeral", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("required_role_id", sa.BigInteger(), nullable=True),
        sa.Column("cooldown_seconds", sa.Integer(), server_default="5", nullable=False),
        sa.Column("uses", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_custom_commands")),
        sa.UniqueConstraint("guild_id", "name", name=op.f("uq_custom_commands_guild_id_name")),
    )
    op.create_index(
        op.f("ix_custom_commands_guild_id"), "custom_commands", ["guild_id"], unique=False
    )
    op.create_table(
        "giveaways",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("prize", sa.String(length=200), nullable=False),
        sa.Column("winner_count", sa.Integer(), nullable=False),
        sa.Column("host_id", sa.BigInteger(), nullable=False),
        sa.Column("required_role_id", sa.BigInteger(), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "winners", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_giveaways")),
    )
    op.create_index("ix_giveaways_guild_ended", "giveaways", ["guild_id", "ended"], unique=False)
    op.create_table(
        "reminders",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("text", sa.String(length=500), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reminders")),
    )
    op.create_index(
        "ix_reminders_guild_user", "reminders", ["guild_id", "user_id", "state"], unique=False
    )
    op.create_table(
        "giveaway_entries",
        sa.Column("giveaway_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["giveaway_id"],
            ["giveaways.id"],
            name=op.f("fk_giveaway_entries_giveaway_id_giveaways"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("giveaway_id", "user_id", name=op.f("pk_giveaway_entries")),
    )


def downgrade() -> None:
    op.drop_table("giveaway_entries")
    op.drop_index("ix_reminders_guild_user", table_name="reminders")
    op.drop_table("reminders")
    op.drop_index("ix_giveaways_guild_ended", table_name="giveaways")
    op.drop_table("giveaways")
    op.drop_index(op.f("ix_custom_commands_guild_id"), table_name="custom_commands")
    op.drop_table("custom_commands")
