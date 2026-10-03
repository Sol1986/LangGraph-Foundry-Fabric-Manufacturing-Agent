import datetime
import os
from decimal import Decimal

import mssql_python
import requests
from dotenv import load_dotenv

load_dotenv()

MAINTENANCE_API_URL = os.environ.get("MAINTENANCE_API_URL", "http://127.0.0.1:8000")


def _connect():
    return mssql_python.connect(
        f"Server={os.environ['FABRIC_SQL_SERVER']};"
        f"Database={os.environ['FABRIC_DATABASE']};"
        "Authentication=ActiveDirectoryDefault;"
        "Encrypt=yes;"
    )


def _clean(v):
    if isinstance(v, datetime.datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, datetime.date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


def _query(sql, *params):
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, *params)
        cols = [c[0] for c in cur.description]
        return [{c: _clean(v) for c, v in zip(cols, row)} for row in cur.fetchall()]
    finally:
        conn.close()


def get_production_performance(plant: str, date: str) -> dict:
    """Get total target units, actual units, variance and attainment percent for one plant on one date.

    plant: the plant name exactly as 'Plant 1', 'Plant 2' or 'Plant 3'.
    date: the date in YYYY-MM-DD format, for example 2026-10-01.
    """
    rows = _query(
        """
        SELECT SUM(t.target_units) AS target, SUM(o.actual_units) AS actual
        FROM dbo_cleaned.production_targets_cleaned t
        JOIN dbo_cleaned.production_output_cleaned o
          ON t.[date] = o.[date] AND t.line_key = o.line_key
        WHERE t.plant = ? AND t.[date] = ?
        """,
        plant, date,
    )
    row = rows[0] if rows else None
    if not row or row["target"] is None:
        return {"error": f"No data found for {plant} on {date}"}
    return {
        "plant": plant,
        "date": date,
        "target": row["target"],
        "actual": row["actual"],
        "variance": row["actual"] - row["target"],
        "attainment_pct": round(100 * row["actual"] / row["target"], 1),
    }


def get_line_performance(plant: str, date: str) -> dict:
    """Get target, actual, variance and attainment percent for each production line in one plant on one date.
    Use this to find which line caused a plant to miss target. Results are sorted worst variance first.

    plant: the plant name exactly as 'Plant 1', 'Plant 2' or 'Plant 3'.
    date: the date in YYYY-MM-DD format.
    """
    rows = _query(
        """
        SELECT t.line AS line,
               SUM(t.target_units) AS target,
               SUM(o.actual_units) AS actual,
               SUM(o.actual_units) - SUM(t.target_units) AS variance,
               ROUND(100.0 * SUM(o.actual_units) / SUM(t.target_units), 1) AS attainment_pct
        FROM dbo_cleaned.production_targets_cleaned t
        JOIN dbo_cleaned.production_output_cleaned o
          ON t.[date] = o.[date] AND t.line_key = o.line_key
        WHERE t.plant = ? AND t.[date] = ?
        GROUP BY t.line
        ORDER BY variance
        """,
        plant, date,
    )
    if not rows:
        return {"error": f"No data found for {plant} on {date}"}
    return {"plant": plant, "date": date, "lines": rows}


def get_downtime_events(plant: str, date: str, line: str | None = None) -> dict:
    """List downtime events for a plant on one date, with total minutes and minutes per line.
    Each event has a timestamp, line, machine_id, downtime_minutes and reason_code.

    plant: the plant name exactly as 'Plant 1', 'Plant 2' or 'Plant 3'.
    date: the date in YYYY-MM-DD format.
    line: optional, one line such as 'Line 3'. Leave empty for all lines in the plant.
    """
    sql = """
        SELECT d.[timestamp] AS event_time, d.line, d.machine_id,
               d.downtime_minutes, d.reason_code
        FROM dbo_cleaned.downtime_events_cleaned d
        WHERE d.plant = ? AND d.event_date = ?
    """
    params = [plant, date]
    if line:
        sql += " AND d.line = ?"
        params.append(line)
    sql += " ORDER BY d.[timestamp]"
    rows = _query(sql, *params)

    by_line = {}
    for r in rows:
        by_line[r["line"]] = by_line.get(r["line"], 0) + r["downtime_minutes"]
    return {
        "plant": plant,
        "date": date,
        "total_minutes": sum(by_line.values()),
        "minutes_by_line": by_line,
        "events": rows,
    }


def get_machine_events(machine_id: str, start_date: str, end_date: str) -> dict:
    """Get a machine's details and all its downtime events between two dates (inclusive),
    with the count and total minutes per reason code. Use this to see how often a machine
    stopped and why.

    machine_id: the machine ID, for example 'CNC-104'.
    start_date: first date in YYYY-MM-DD format.
    end_date: last date in YYYY-MM-DD format.
    """
    info = _query(
        """
        SELECT machine_id, machine_type, plant, line, installation_date
        FROM dbo_cleaned.machines_cleaned
        WHERE machine_id = ?
        """,
        machine_id,
    )
    if not info:
        return {"error": f"Unknown machine {machine_id}"}
    events = _query(
        """
        SELECT d.[timestamp] AS event_time, d.downtime_minutes, d.reason_code
        FROM dbo_cleaned.downtime_events_cleaned d
        WHERE d.machine_id = ? AND d.event_date BETWEEN ? AND ?
        ORDER BY d.[timestamp]
        """,
        machine_id, start_date, end_date,
    )
    by_reason = {}
    for e in events:
        r = by_reason.setdefault(e["reason_code"], {"events": 0, "minutes": 0})
        r["events"] += 1
        r["minutes"] += e["downtime_minutes"]
    return {
        "machine": info[0],
        "start_date": start_date,
        "end_date": end_date,
        "event_count": len(events),
        "total_minutes": sum(e["downtime_minutes"] for e in events),
        "by_reason": by_reason,
        "events": events,
    }


def get_quality_defects(
    start_date: str,
    end_date: str,
    plant: str | None = None,
    machine_id: str | None = None,
) -> dict:
    """Get quality defect quantities between two dates (inclusive), grouped by date, machine
    and defect type, largest first (maximum 100 rows, so keep the date range narrow).
    Filter by plant, by machine, or both.

    start_date: first date in YYYY-MM-DD format.
    end_date: last date in YYYY-MM-DD format.
    plant: optional, 'Plant 1', 'Plant 2' or 'Plant 3'.
    machine_id: optional, for example 'CNC-104'.
    """
    sql = """
        SELECT TOP 100 [date], plant, line, machine_id, defect_type,
               SUM(quantity) AS quantity
        FROM dbo_cleaned.quality_defects_cleaned
        WHERE [date] BETWEEN ? AND ?
    """
    params = [start_date, end_date]
    if plant:
        sql += " AND plant = ?"
        params.append(plant)
    if machine_id:
        sql += " AND machine_id = ?"
        params.append(machine_id)
    sql += " GROUP BY [date], plant, line, machine_id, defect_type ORDER BY SUM(quantity) DESC"
    rows = _query(sql, *params)
    return {"start_date": start_date, "end_date": end_date, "rows": rows}


def get_inventory_status(plant: str, date: str, item_id: str | None = None) -> dict:
    """Get on-hand stock, reorder point and a below_reorder flag for each stocked item in a
    plant on one date. Call it again with an earlier date to see how stock changed.

    plant: the plant name exactly as 'Plant 1', 'Plant 2' or 'Plant 3'.
    date: the date in YYYY-MM-DD format.
    item_id: optional, for example 'BRKT-220'. Leave empty for all items.
    """
    sql = """
        SELECT item_id, item_name, on_hand_units, reorder_point,
               CASE WHEN on_hand_units < reorder_point THEN 1 ELSE 0 END AS below_reorder
        FROM dbo_cleaned.inventory_cleaned
        WHERE plant = ? AND [date] = ?
    """
    params = [plant, date]
    if item_id:
        sql += " AND item_id = ?"
        params.append(item_id)
    sql += " ORDER BY item_id"
    rows = _query(sql, *params)
    for r in rows:
        r["below_reorder"] = bool(r["below_reorder"])
    if not rows:
        return {"error": f"No inventory data for {plant} on {date}"}
    return {"plant": plant, "date": date, "items": rows}


def get_maintenance_history(machine_id: str) -> dict:
    """Get maintenance information for one machine from the maintenance system: its service
    schedule (component, interval in days, last service date, next due date), its most recent
    service records, and any work orders. Use this to check whether servicing is up to date.

    machine_id: the machine ID, for example 'CNC-104'.
    """
    try:
        r = requests.get(
            f"{MAINTENANCE_API_URL}/machines/{machine_id}/maintenance", timeout=10
        )
    except requests.RequestException as e:
        return {"error": f"Maintenance system unavailable: {e}"}
    if r.status_code == 404:
        return {"error": f"Unknown machine {machine_id}"}
    r.raise_for_status()
    return r.json()


if __name__ == "__main__":
    print(get_production_performance("Plant 2", "2026-10-01"))