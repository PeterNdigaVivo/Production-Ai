"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-12

Creates tenancy, RBAC, time-series event, and production roll-up tables.
TimescaleDB hypertables for worker_events / production_events are created
opportunistically — if the timescaledb extension is not present, the tables
remain plain Postgres tables (still functional).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")

    op.create_table(
        "tenants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "factories",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("location", sa.String(255)),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "production_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("factory_id", UUID(as_uuid=True), sa.ForeignKey("factories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("target_pieces_per_hour", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "cameras",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("line_id", UUID(as_uuid=True), sa.ForeignKey("production_lines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("rtsp_url", sa.String(1024), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("resolution", sa.String(32)),
        sa.Column("fps_target", sa.Integer, nullable=False, server_default="8"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "workstations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("line_id", UUID(as_uuid=True), sa.ForeignKey("production_lines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("camera_id", UUID(as_uuid=True), sa.ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("layout_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "zones",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workstation_id", UUID(as_uuid=True), sa.ForeignKey("workstations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("polygon", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("layout_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE")),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("is_superuser", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("description", sa.String(255)),
    )

    op.create_table(
        "user_roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_id", UUID(as_uuid=True), sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("factory_id", UUID(as_uuid=True), sa.ForeignKey("factories.id", ondelete="CASCADE")),
    )

    op.create_table(
        "workers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("factory_id", UUID(as_uuid=True), sa.ForeignKey("factories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_code", sa.String(64), unique=True),
        sa.Column("display_name", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "shift_definitions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("factory_id", UUID(as_uuid=True), sa.ForeignKey("factories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("start_time", sa.Time, nullable=False),
        sa.Column("end_time", sa.Time, nullable=False),
    )

    op.create_table(
        "worker_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("camera_id", UUID(as_uuid=True), nullable=False),
        sa.Column("workstation_id", UUID(as_uuid=True)),
        sa.Column("worker_track_id", sa.Integer, nullable=False, server_default="0"),
        sa.Column("state", sa.String(32), nullable=False, server_default="UNKNOWN"),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("bbox", JSONB),
        sa.Column("extra", JSONB),
    )
    op.create_index("ix_worker_events_camera_ts", "worker_events", ["camera_id", "ts"])
    op.create_index("ix_worker_events_workstation_ts", "worker_events", ["workstation_id", "ts"])
    op.execute(
        "SELECT create_hypertable('worker_events', 'ts', if_not_exists => TRUE, migrate_data => TRUE);"
    )

    op.create_table(
        "production_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workstation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("line_id", UUID(as_uuid=True), nullable=False),
        sa.Column("factory_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("cycle_time_s", sa.Float),
        sa.Column("payload", JSONB),
    )
    op.execute(
        "SELECT create_hypertable('production_events', 'ts', if_not_exists => TRUE, migrate_data => TRUE);"
    )

    op.create_table(
        "production_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("factory_id", UUID(as_uuid=True), nullable=False),
        sa.Column("line_id", UUID(as_uuid=True), nullable=False),
        sa.Column("workstation_id", UUID(as_uuid=True)),
        sa.Column("pieces_completed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_cycle_time_s", sa.Float),
        sa.Column("effective_working_s", sa.Integer, nullable=False, server_default="0"),
        sa.Column("idle_s", sa.Integer, nullable=False, server_default="0"),
        sa.Column("away_s", sa.Integer, nullable=False, server_default="0"),
        sa.Column("waiting_s", sa.Integer, nullable=False, server_default="0"),
        sa.Column("extra", JSONB),
    )

    op.create_table(
        "alerts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("factory_id", UUID(as_uuid=True)),
        sa.Column("severity", sa.String(16), nullable=False, server_default="info"),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.String(2000)),
        sa.Column("acknowledged", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("acknowledged_by", UUID(as_uuid=True)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("payload", JSONB),
    )


def downgrade() -> None:
    for t in [
        "alerts", "production_records", "production_events", "worker_events",
        "shift_definitions", "workers", "user_roles", "roles", "users",
        "zones", "workstations", "cameras", "production_lines", "factories", "tenants",
    ]:
        op.drop_table(t)
