"""
Inspect the time-series store. Run any time, even while the consumer
is inserting — TimescaleDB supports concurrent readers + writers.

    python query_readings.py
"""
import db

with db.connect() as conn, conn.cursor() as cur:

    print("=== TimescaleDB version ===")
    cur.execute("SELECT extversion FROM pg_extension WHERE extname='timescaledb'")
    row = cur.fetchone()
    print(f"timescaledb {row[0] if row else '(not installed)'}")

    print("\n=== Total readings ===")
    cur.execute("SELECT COUNT(*) FROM readings")
    print(cur.fetchone()[0])

    print("\n=== Per-sensor summary ===")
    cur.execute("""
        SELECT sensor_id, room, type,
               COUNT(*) AS n,
               ROUND(AVG(value)::NUMERIC, 2) AS avg,
               ROUND(MIN(value)::NUMERIC, 2) AS lo,
               ROUND(MAX(value)::NUMERIC, 2) AS hi,
               MAX(ts) AS last
        FROM readings
        GROUP BY sensor_id, room, type
        ORDER BY sensor_id
    """)
    for sensor_id, room, typ, n, avg, lo, hi, last in cur.fetchall():
        print(f"  {sensor_id:32s}  room={room or '-':6s}  type={typ or '-':12s}"
              f"  n={n:5d}  avg={float(avg):6.2f}  range=[{float(lo):.2f}, {float(hi):.2f}]"
              f"  last={last}")

    print("\n=== Last 10 readings ===")
    cur.execute("""
        SELECT ts, sensor_id, value
        FROM readings
        ORDER BY ts DESC
        LIMIT 10
    """)
    for ts, sid, value in cur.fetchall():
        print(f"  {ts}  {sid:32s}  {value:.2f}")

    print("\n=== Per-second rolling mean of A109 temperature, last 5 minutes ===")
    cur.execute("""
        SELECT date_trunc('second', ts) AS sec,
               ROUND(AVG(value)::NUMERIC, 2) AS v
        FROM readings
        WHERE room = 'A109' AND type = 'temperature'
          AND ts > now() - INTERVAL '5 minutes'
        GROUP BY sec
        ORDER BY sec DESC
        LIMIT 20
    """)
    for sec, v in cur.fetchall():
        print(f"  {sec}  {float(v):.2f}")
