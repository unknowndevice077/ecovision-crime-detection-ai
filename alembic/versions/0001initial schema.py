"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-22
"""
import os
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA_SQL_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "app", "schema_final.sql")


def upgrade():
    with open(SCHEMA_SQL_PATH, "r") as f:
        sql = f.read()
    for statement in sql.split(";"):
        stmt = statement.strip()
        if stmt:
            op.execute(stmt)


def downgrade():
    op.execute("""
        DROP TABLE IF EXISTS user_permissions CASCADE;
        DROP TABLE IF EXISTS permission_keys CASCADE;
        DROP TABLE IF EXISTS telemetry_readings CASCADE;
        DROP TABLE IF EXISTS video_records CASCADE;
        DROP TABLE IF EXISTS incident_visibility CASCADE;
        DROP TABLE IF EXISTS incident_details CASCADE;
        DROP TABLE IF EXISTS incidents CASCADE;
        DROP TABLE IF EXISTS cameras CASCADE;
        DROP TABLE IF EXISTS users CASCADE;
        DROP TABLE IF EXISTS barangays CASCADE;
    """)