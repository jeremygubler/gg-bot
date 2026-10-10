"""Phase 7: Tickets (Panels, Kategorien, Tickets, Transkripte, Bewertungen)

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticket_counters",
        sa.Column("guild_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("last_no", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("guild_id", name=op.f("pk_ticket_counters")),
    )
    op.create_table(
        "ticket_panels",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_panels")),
    )
    op.create_index("ix_ticket_panels_guild_id", "ticket_panels", ["guild_id"], unique=False)
    op.create_table(
        "ticket_categories",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("panel_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("emoji", sa.String(length=64), nullable=True),
        sa.Column("description", sa.String(length=100), nullable=True),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("support_role_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("welcome_message", sa.String(length=1000), nullable=True),
        sa.Column("questions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["panel_id"],
            ["ticket_panels.id"],
            name=op.f("fk_ticket_categories_panel_id_ticket_panels"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_categories")),
        sa.UniqueConstraint("panel_id", "name", name=op.f("uq_ticket_categories_panel_id_name")),
    )
    op.create_index(
        op.f("ix_ticket_categories_panel_id"), "ticket_categories", ["panel_id"], unique=False
    )
    op.create_table(
        "tickets",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("creator_id", sa.BigInteger(), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=True),
        sa.Column("category_name", sa.String(length=80), nullable=False),
        sa.Column("support_role_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("claimed_by", sa.BigInteger(), nullable=True),
        sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.BigInteger(), nullable=True),
        sa.Column("close_reason", sa.String(length=300), nullable=True),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["ticket_categories.id"],
            name=op.f("fk_tickets_category_id_ticket_categories"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tickets")),
        sa.UniqueConstraint("channel_id", name=op.f("uq_tickets_channel_id")),
        sa.UniqueConstraint("guild_id", "number", name=op.f("uq_tickets_guild_id_number")),
    )
    op.create_index(
        "ix_tickets_guild_creator_status",
        "tickets",
        ["guild_id", "creator_id", "status"],
        unique=False,
    )
    op.create_index("ix_tickets_guild_status", "tickets", ["guild_id", "status"], unique=False)
    op.create_table(
        "ticket_feedback",
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tickets.id"],
            name=op.f("fk_ticket_feedback_ticket_id_tickets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("ticket_id", name=op.f("pk_ticket_feedback")),
    )
    op.create_table(
        "ticket_transcripts",
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("html", sa.Text(), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tickets.id"],
            name=op.f("fk_ticket_transcripts_ticket_id_tickets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("ticket_id", name=op.f("pk_ticket_transcripts")),
    )
    op.create_index(
        "ix_ticket_transcripts_guild_id", "ticket_transcripts", ["guild_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_ticket_transcripts_guild_id", table_name="ticket_transcripts")
    op.drop_table("ticket_transcripts")
    op.drop_table("ticket_feedback")
    op.drop_index("ix_tickets_guild_status", table_name="tickets")
    op.drop_index("ix_tickets_guild_creator_status", table_name="tickets")
    op.drop_table("tickets")
    op.drop_index(op.f("ix_ticket_categories_panel_id"), table_name="ticket_categories")
    op.drop_table("ticket_categories")
    op.drop_index("ix_ticket_panels_guild_id", table_name="ticket_panels")
    op.drop_table("ticket_panels")
    op.drop_table("ticket_counters")
