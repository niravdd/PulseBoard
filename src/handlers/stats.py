"""Stats handler — aggregated telemetry data for the dashboard.

All endpoints require Cognito authentication.
Reads from the AggregatesTable for fast pre-computed stats, and EventsTable
for detailed queries.

Endpoints:
  GET /stats/{project_id}/overview   — Summary: total events, unique deployments, top version/OS/country
  GET /stats/{project_id}/timeseries — Daily/weekly/monthly event counts for charts
  GET /stats/{project_id}/breakdown  — Detailed breakdown by version, OS, country, event type
  GET /stats/{project_id}/events     — Recent raw events (paginated)
"""

import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from boto3.dynamodb.conditions import Key

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.db import events_table, aggregates_table, projects_table
from shared.response import ok, error


def handler(event, context):
    path = event.get("path", "")
    path_params = event.get("pathParameters") or {}
    project_id = path_params.get("project_id")
    qs = event.get("queryStringParameters") or {}

    if not project_id:
        return error("Project ID required")

    if path.endswith("/overview"):
        return _overview(project_id, qs)
    if path.endswith("/timeseries"):
        return _timeseries(project_id, qs)
    if path.endswith("/breakdown"):
        return _breakdown(project_id, qs)
    if path.endswith("/events"):
        return _events(project_id, qs)

    return error("Unknown stats endpoint", 404)


def _overview(project_id, qs):
    """High-level summary: totals for last 7d, 30d, all-time."""
    now = datetime.now(timezone.utc)

    # Fetch last 30 days of daily aggregates
    start_date = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    result = aggregates_table().query(
        KeyConditionExpression=Key("pk").eq(project_id) & Key("sk").between(f"day#{start_date}", f"day#9999"),
    )
    days = result.get("Items", [])

    total_30d = sum(_dec(d.get("total_events", 0)) for d in days)
    unique_30d = set()
    for d in days:
        unique_30d.update(d.get("unique_ids", set()))

    # Last 7 days
    start_7d = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    days_7 = [d for d in days if d.get("sk", "") >= f"day#{start_7d}"]
    total_7d = sum(_dec(d.get("total_events", 0)) for d in days_7)
    unique_7d = set()
    for d in days_7:
        unique_7d.update(d.get("unique_ids", set()))

    # Today
    today = now.strftime("%Y-%m-%d")
    days_today = [d for d in days if d.get("sk") == f"day#{today}"]
    total_today = sum(_dec(d.get("total_events", 0)) for d in days_today)

    # Top version, OS, country from 30d aggregates
    versions = {}
    os_breakdown = {}
    countries = {}
    for d in days:
        for v, c in (d.get("versions") or {}).items():
            versions[v] = versions.get(v, 0) + _dec(c)
        for o, c in (d.get("os_breakdown") or {}).items():
            os_breakdown[o] = os_breakdown.get(o, 0) + _dec(c)
        for co, c in (d.get("countries") or {}).items():
            countries[co] = countries.get(co, 0) + _dec(c)

    return ok({
        "project_id": project_id,
        "period": {"start": start_date, "end": today},
        "today": {"events": total_today},
        "last_7d": {"events": total_7d, "unique_deployments": len(unique_7d)},
        "last_30d": {"events": total_30d, "unique_deployments": len(unique_30d)},
        "top_version": _top_n(versions, 1),
        "top_os": _top_n(os_breakdown, 1),
        "top_country": _top_n(countries, 1),
    })


def _timeseries(project_id, qs):
    """Daily event counts for charting. Default: last 30 days."""
    period = qs.get("period", "daily")
    days_back = int(qs.get("days", 30))
    now = datetime.now(timezone.utc)

    if period == "monthly":
        # Fetch monthly aggregates
        start = (now - timedelta(days=days_back)).strftime("%Y-%m")
        result = aggregates_table().query(
            KeyConditionExpression=Key("pk").eq(project_id) & Key("sk").between(f"month#{start}", "month#9999"),
        )
    elif period == "weekly":
        start = (now - timedelta(days=days_back)).strftime("%Y-W%W")
        result = aggregates_table().query(
            KeyConditionExpression=Key("pk").eq(project_id) & Key("sk").between(f"week#{start}", "week#9999"),
        )
    else:
        start = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
        result = aggregates_table().query(
            KeyConditionExpression=Key("pk").eq(project_id) & Key("sk").between(f"day#{start}", "day#9999"),
        )

    items = result.get("Items", [])
    series = []
    for item in sorted(items, key=lambda x: x.get("sk", "")):
        label = item.get("sk", "").split("#", 1)[-1]
        series.append({
            "date": label,
            "events": _dec(item.get("total_events", 0)),
            "unique": len(item.get("unique_ids", set())),
        })

    return ok({"project_id": project_id, "period": period, "series": series})


def _breakdown(project_id, qs):
    """Detailed breakdown by dimension. Default: last 30 days."""
    days_back = int(qs.get("days", 30))
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")

    result = aggregates_table().query(
        KeyConditionExpression=Key("pk").eq(project_id) & Key("sk").between(f"day#{start}", "day#9999"),
    )
    days = result.get("Items", [])

    # Aggregate across all days
    versions = {}
    os_breakdown = {}
    countries = {}
    event_types = {}

    for d in days:
        for v, c in (d.get("versions") or {}).items():
            versions[v] = versions.get(v, 0) + _dec(c)
        for o, c in (d.get("os_breakdown") or {}).items():
            os_breakdown[o] = os_breakdown.get(o, 0) + _dec(c)
        for co, c in (d.get("countries") or {}).items():
            countries[co] = countries.get(co, 0) + _dec(c)
        for et, c in (d.get("event_types") or {}).items():
            event_types[et] = event_types.get(et, 0) + _dec(c)

    return ok({
        "project_id": project_id,
        "days": days_back,
        "versions": _sorted_map(versions),
        "os": _sorted_map(os_breakdown),
        "countries": _sorted_map(countries),
        "event_types": _sorted_map(event_types),
    })


def _events(project_id, qs):
    """Recent raw events, paginated."""
    limit = int(qs.get("limit", 50))
    start_key = qs.get("cursor")

    kwargs = {
        "KeyConditionExpression": Key("project_id").eq(project_id),
        "ScanIndexForward": False,  # newest first
        "Limit": limit,
    }
    if start_key:
        kwargs["ExclusiveStartKey"] = {"project_id": project_id, "timestamp_id": start_key}

    result = events_table().query(**kwargs)
    items = result.get("Items", [])

    # Parse properties JSON back to dict
    for item in items:
        try:
            item["properties"] = json.loads(item.get("properties", "{}"))
        except (json.JSONDecodeError, TypeError):
            pass

    next_cursor = None
    if result.get("LastEvaluatedKey"):
        next_cursor = result["LastEvaluatedKey"].get("timestamp_id")

    return ok({"events": items, "cursor": next_cursor, "count": len(items)})


# ── Helpers ──────────────────────────────────────────────────────────

def _dec(val) -> int:
    """Convert Decimal or any numeric to int."""
    if isinstance(val, Decimal):
        return int(val)
    return int(val) if val else 0


def _top_n(mapping: dict, n: int) -> list:
    """Return top N items from a {key: count} dict, sorted by count desc."""
    return sorted(
        [{"name": k, "count": v} for k, v in mapping.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:n]


def _sorted_map(mapping: dict) -> list:
    """Convert {key: count} to sorted list of {name, count}."""
    return sorted(
        [{"name": k, "count": v} for k, v in mapping.items()],
        key=lambda x: x["count"],
        reverse=True,
    )
