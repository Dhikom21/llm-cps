"""
Tiny shared module: connect to TimescaleDB and (idempotently) ensure
the schema exists.

Why a shared module: consumer, agent, and query script all need the
same connection settings. Centralising avoids drift.
"""
import os
import psycopg2

PG_HOST = os.environ.get("PG_HOST", "localhost")
PG_PORT = int(os.environ.get("PG_PORT", "5432"))
PG_DB   = os.environ.get("PG_DB",   "building")
PG_USER = os.environ.get("PG_USER", "postgres")
PG_PWD  = os.environ.get("PG_PASSWORD", "postgres")

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS readings (
    ts          TIMESTAMPTZ      NOT NULL,
    sensor_id   TEXT             NOT NULL,
    room        TEXT,
    floor       TEXT,
    type        TEXT,
    unit        TEXT,
    value       DOUBLE PRECISION
);

SELECT create_hypertable('readings', 'ts',
                         if_not_exists => TRUE,
                         migrate_data  => TRUE);

CREATE INDEX IF NOT EXISTS idx_readings_room_type_ts
    ON readings (room, type, ts DESC);


-- Audit trail (lecture-4 §Audit Trail).
-- Every agent decision, tool call, and Thought lands here.
-- This is what you replay at the oral exam to defend the agent's choices.
CREATE TABLE IF NOT EXISTS decisions (
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    agent_id   TEXT        NOT NULL,
    step       TEXT        NOT NULL,    -- 'thought' | 'action' | 'observation' |
                                        -- 'guardrail_blocked' | 'fallback' | 'final'
    tool       TEXT,                    -- tool name when step='action'
    args       JSONB,                   -- tool arguments (validated)
    result     JSONB,                   -- tool result when step='observation'
    text       TEXT,                    -- free-text Thought / explanation
    success    BOOLEAN     NOT NULL DEFAULT TRUE
);

SELECT create_hypertable('decisions', 'ts',
                         if_not_exists => TRUE,
                         migrate_data  => TRUE);

CREATE INDEX IF NOT EXISTS idx_decisions_agent_ts
    ON decisions (agent_id, ts DESC);


-- Alerts surfaced by the agent (lecture-4 §The ReAct Pattern's create_alert
-- example tool).
CREATE TABLE IF NOT EXISTS alerts (
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    agent_id   TEXT,
    severity   TEXT        NOT NULL,    -- 'info' | 'warning' | 'critical'
    room       TEXT,
    message    TEXT        NOT NULL,
    acknowledged BOOLEAN   NOT NULL DEFAULT FALSE
);
"""

def connect(autocommit: bool = True):
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        dbname=PG_DB, user=PG_USER, password=PG_PWD,
        connect_timeout=5,
    )
    conn.autocommit = autocommit
    return conn

def ensure_schema():
    """Create extension, table, hypertable, and index if missing.
    Safe to call repeatedly."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)

if __name__ == "__main__":
    # Quick connectivity check
    ensure_schema()
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT version()")
        print(cur.fetchone()[0])
        cur.execute("SELECT count(*) FROM readings")
        print(f"readings rows: {cur.fetchone()[0]}")
