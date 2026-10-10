"""Phase 8: Benachrichtigungen (Twitch, YouTube)

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notify_subscriptions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(length=8), nullable=False),
        sa.Column("ref", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("message", sa.String(length=300), nullable=True),
        sa.Column("role_id", sa.BigInteger(), nullable=True),
        sa.Column("last_key", sa.String(length=64), nullable=True),
        sa.Column("live", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("misses", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "seen", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notify_subscriptions")),
        sa.UniqueConstraint(
            "guild_id",
            "provider",
            "ref",
            name=op.f("uq_notify_subscriptions_guild_id_provider_ref"),
        ),
    )
    op.create_index(
        op.f("ix_notify_subscriptions_guild_id"), "notify_subscriptions", ["guild_id"], unique=False
    )
    op.create_index(
        "ix_notify_subscriptions_provider_ref",
        "notify_subscriptions",
        ["provider", "ref"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_notify_subscriptions_provider_ref", table_name="notify_subscriptions")
    op.drop_index(op.f("ix_notify_subscriptions_guild_id"), table_name="notify_subscriptions")
    op.drop_table("notify_subscriptions")
