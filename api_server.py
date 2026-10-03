import json
import os
from datetime import date, timedelta

from fastapi import FastAPI, HTTPException

DATA_PATH = os.environ.get("MAINTENANCE_DATA_PATH", "mock_api/maintenance_data.json")
with open(DATA_PATH, encoding="utf-8") as f:
    DATA = json.load(f)

app = FastAPI(title="Mock Maintenance API")


@app.get("/machines/{machine_id}/maintenance")
def machine_maintenance(machine_id: str, recent: int = 10):
    machine_id = machine_id.upper()
    schedule = []
    for s in DATA["maintenance_schedule"]:
        if s["machine_id"] == machine_id:
            last = date.fromisoformat(s["last_service_date"])
            schedule.append({
                **s,
                "next_due_date": (last + timedelta(days=s["interval_days"])).isoformat(),
            })
    if not schedule:
        raise HTTPException(status_code=404, detail=f"Unknown machine {machine_id}")

    records = sorted(
        (r for r in DATA["maintenance_records"] if r["machine_id"] == machine_id),
        key=lambda r: r["date"],
        reverse=True,
    )[:recent]
    work_orders = [w for w in DATA["work_orders"] if w["machine_id"] == machine_id]

    return {
        "machine_id": machine_id,
        "schedule": schedule,
        "recent_records": records,
        "work_orders": work_orders,
    }