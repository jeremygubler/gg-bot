"""ankuendigungen

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "announcements",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("content", sa.String(length=2000), nullable=True),
        sa.Column("embed", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("state", sa.String(length=12), server_default="draft", nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rev", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_key", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_announcements")),
    )
    op.create_index(
        "ix_announcements_guild_state", "announcements", ["guild_id", "state"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_announcements_guild_state", table_name="announcements")
    op.drop_table("announcements")
