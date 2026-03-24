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

    method = event.get("httpMethod", "GET")

    if path.endswith("/overview"):
        return _overview(project_id, qs)
    if path.endswith("/timeseries"):
        return _timeseries(project_id, qs)
    if path.endswith("/breakdown"):
        return _breakdown(project_id, qs)
    if path.endswith("/events"):
        return _events(project_id, qs)
    if path.endswith("/github"):
        return _github(project_id, qs)
    if path.endswith("/purge") and method == "DELETE":
        return _purge(project_id, qs)

    return error("Unknown stats endpoint", 404)


def _overview(project_id, qs):
    """High-level summary: totals for today, 7d, 30d, and lifetime."""
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    # Fetch ALL daily aggregates (lifetime) — paginate if needed
    days = _query_all(project_id, "day#", "day#9999")

    # Lifetime totals
    total_lifetime = sum(_dec(d.get("total_events", 0)) for d in days)
    cost_lifetime = sum(_dec_float(d.get("total_cost_usd", 0)) for d in days)
    unique_lifetime = set()
    for d in days:
        unique_lifetime.update(d.get("unique_ids", set()))

    # Last 30 days
    start_30d = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    days_30 = [d for d in days if d.get("sk", "") >= f"day#{start_30d}"]
    total_30d = sum(_dec(d.get("total_events", 0)) for d in days_30)
    cost_30d = sum(_dec_float(d.get("total_cost_usd", 0)) for d in days_30)
    unique_30d = set()
    for d in days_30:
        unique_30d.update(d.get("unique_ids", set()))

    # Last 7 days
    start_7d = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    days_7 = [d for d in days if d.get("sk", "") >= f"day#{start_7d}"]
    total_7d = sum(_dec(d.get("total_events", 0)) for d in days_7)
    cost_7d = sum(_dec_float(d.get("total_cost_usd", 0)) for d in days_7)
    unique_7d = set()
    for d in days_7:
        unique_7d.update(d.get("unique_ids", set()))

    # Today
    days_today = [d for d in days if d.get("sk") == f"day#{today}"]
    total_today = sum(_dec(d.get("total_events", 0)) for d in days_today)
    cost_today = sum(_dec_float(d.get("total_cost_usd", 0)) for d in days_today)

    # Top version, OS, country from lifetime
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
        "today": {"events": total_today, "cost_usd": round(cost_today, 4)},
        "last_7d": {"events": total_7d, "unique_deployments": len(unique_7d), "cost_usd": round(cost_7d, 4)},
        "last_30d": {"events": total_30d, "unique_deployments": len(unique_30d), "cost_usd": round(cost_30d, 4)},
        "lifetime": {"events": total_lifetime, "unique_deployments": len(unique_lifetime), "cost_usd": round(cost_lifetime, 4)},
        "top_version": _top_n(versions, 1),
        "top_os": _top_n(os_breakdown, 1),
        "top_country": _top_n(countries, 1),
    })


def _resolve_date_range(qs):
    """Resolve start/end dates from query params. Supports days=N, from/to, or days=0 (lifetime)."""
    now = datetime.now(timezone.utc)
    date_from = qs.get("from", "")  # YYYY-MM-DD
    date_to = qs.get("to", "")      # YYYY-MM-DD
    days_back = int(qs.get("days", 30))

    if date_from and date_to:
        return date_from, date_to
    if date_from:
        return date_from, now.strftime("%Y-%m-%d")
    if days_back == 0:
        return "0000-01-01", "9999-12-31"
    start = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")
    return start, end


def _timeseries(project_id, qs):
    """Event counts for charting. Supports daily/weekly/monthly, custom date range, or lifetime."""
    period = qs.get("period", "daily")
    date_from, date_to = _resolve_date_range(qs)

    if period == "monthly":
        prefix = "month#"
        sk_start = f"{prefix}{date_from[:7]}"
        sk_end = f"{prefix}{date_to[:7]}~"
        items = _query_all(project_id, sk_start, sk_end)
    elif period == "weekly":
        prefix = "week#"
        # Convert dates to week format for filtering
        items = _query_all(project_id, f"{prefix}0000", f"{prefix}9999")
    else:
        prefix = "day#"
        items = _query_all(project_id, f"{prefix}{date_from}", f"{prefix}{date_to}~")

    series = []
    for item in sorted(items, key=lambda x: x.get("sk", "")):
        label = item.get("sk", "").split("#", 1)[-1]
        series.append({
            "date": label,
            "events": _dec(item.get("total_events", 0)),
            "unique": len(item.get("unique_ids", set())),
            "cost_usd": round(_dec_float(item.get("total_cost_usd", 0)), 4),
        })

    return ok({"project_id": project_id, "period": period, "series": series})


def _breakdown(project_id, qs):
    """Detailed breakdown by dimension. Supports days=N, from/to, or days=0 (lifetime)."""
    date_from, date_to = _resolve_date_range(qs)
    days = _query_all(project_id, f"day#{date_from}", f"day#{date_to}~")

    # Aggregate across all days
    versions = {}
    os_breakdown = {}
    countries = {}
    event_types = {}
    models = {}
    total_cost = 0.0

    for d in days:
        total_cost += _dec_float(d.get("total_cost_usd", 0))
        for v, c in (d.get("versions") or {}).items():
            versions[v] = versions.get(v, 0) + _dec(c)
        for o, c in (d.get("os_breakdown") or {}).items():
            os_breakdown[o] = os_breakdown.get(o, 0) + _dec(c)
        for co, c in (d.get("countries") or {}).items():
            countries[co] = countries.get(co, 0) + _dec(c)
        for et, c in (d.get("event_types") or {}).items():
            event_types[et] = event_types.get(et, 0) + _dec(c)
        for m, c in (d.get("models") or {}).items():
            models[m] = models.get(m, 0) + _dec(c)

    # Query raw events for unique deployment counts per dimension
    # This gives accurate "unique users per country" etc.
    unique_countries = {}
    unique_os = {}
    unique_versions = {}
    # Track the LATEST version/OS/country per deployment (not cumulative).
    # For each distinct_id, keep only the most recent event's values.
    # This means versions show "currently running" not "ever used".
    latest_per_deployment = {}  # distinct_id → {version, os, country, timestamp_id}

    try:
        raw_kwargs = {"KeyConditionExpression": Key("project_id").eq(project_id)}
        if date_from != "0000-01-01":
            raw_kwargs["FilterExpression"] = Key("event_date").between(date_from, date_to.rstrip("~"))
        while True:
            result = events_table().query(**raw_kwargs,
                ProjectionExpression="distinct_id, country, os, version, timestamp_id")
            for item in result.get("Items", []):
                did = item.get("distinct_id", "")
                if not did:
                    continue
                ts = item.get("timestamp_id", "")
                existing = latest_per_deployment.get(did)
                if not existing or ts > existing["timestamp_id"]:
                    latest_per_deployment[did] = {
                        "version": item.get("version", ""),
                        "os": item.get("os", ""),
                        "country": item.get("country", ""),
                        "timestamp_id": ts,
                    }
            if "LastEvaluatedKey" not in result:
                break
            raw_kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]

        # Build unique counts from each deployment's LATEST state
        for did, info in latest_per_deployment.items():
            v = info["version"]
            o = info["os"]
            c = info["country"]
            if v:
                unique_versions.setdefault(v, set()).add(did)
            if o:
                unique_os.setdefault(o, set()).add(did)
            if c:
                unique_countries.setdefault(c, set()).add(did)
    except Exception:
        pass  # Fallback to event counts if raw query fails

    def _unique_map(m):
        return sorted([{"name": k, "count": len(v)} for k, v in m.items()], key=lambda x: x["count"], reverse=True)

    return ok({
        "project_id": project_id,
        "date_range": {"from": date_from, "to": date_to},
        "total_cost_usd": round(total_cost, 4),
        "versions": _unique_map(unique_versions) if unique_versions else _sorted_map(versions),
        "os": _unique_map(unique_os) if unique_os else _sorted_map(os_breakdown),
        "countries": _unique_map(unique_countries) if unique_countries else _sorted_map(countries),
        "event_types": _sorted_map(event_types),
        "models": _sorted_map(models),
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

def _github(project_id, qs):
    """Return GitHub traffic data: summary + daily clones/views timeseries.
    Respects from/to/days query params for filtering daily data."""
    # Fetch summary (always full — not filtered by date)
    summary_result = aggregates_table().get_item(
        Key={"pk": project_id, "sk": "github#summary"},
    )
    summary = summary_result.get("Item", {})

    # Fetch daily GitHub traffic (filtered by date range)
    date_from, date_to = _resolve_date_range(qs)
    gh_days = _query_all(project_id, f"ghday#{date_from}", f"ghday#{date_to}~")
    daily = []
    for d in sorted(gh_days, key=lambda x: x.get("sk", "")):
        date = d.get("sk", "").replace("ghday#", "")
        daily.append({
            "date": date,
            "clones": _dec(d.get("gh_clones", 0)),
            "clones_unique": _dec(d.get("gh_clones_unique", 0)),
            "views": _dec(d.get("gh_views", 0)),
            "views_unique": _dec(d.get("gh_views_unique", 0)),
        })

    # Parse referrers and paths from JSON strings
    referrers = []
    paths = []
    try:
        referrers = json.loads(summary.get("gh_referrers", "[]"))
    except Exception:
        pass
    try:
        paths = json.loads(summary.get("gh_popular_paths", "[]"))
    except Exception:
        pass

    # Parse languages
    languages = {}
    try:
        languages = json.loads(summary.get("gh_languages", "{}"))
    except Exception:
        pass

    has_traffic = summary.get("gh_has_traffic", False)

    return ok({
        "project_id": project_id,
        "has_traffic": has_traffic,
        "stars": _dec(summary.get("gh_stars", 0)),
        "forks": _dec(summary.get("gh_forks", 0)),
        "watchers": _dec(summary.get("gh_watchers", 0)),
        "open_issues": _dec(summary.get("gh_open_issues", 0)),
        "contributors": _dec(summary.get("gh_contributors", 0)),
        "language": summary.get("gh_language", ""),
        "languages": languages,
        "description": summary.get("gh_description", ""),
        "total_clones_14d": _dec(summary.get("gh_total_clones", 0)),
        "unique_cloners_14d": _dec(summary.get("gh_unique_cloners", 0)),
        "total_views_14d": _dec(summary.get("gh_total_views", 0)),
        "unique_visitors_14d": _dec(summary.get("gh_unique_visitors", 0)),
        "referrers": referrers,
        "popular_paths": paths,
        "daily": daily,
        "fetched_at": summary.get("gh_fetched_at", ""),
        "traffic_note": "" if has_traffic else "Traffic data unavailable. Add Administration:read permission to your Fine-Grained PAT, or use a Classic PAT with repo scope.",
    })


def _purge(project_id, qs):
    """Delete all events and aggregates for a project. Requires ?confirm=yes."""
    if qs.get("confirm") != "yes":
        return error("Add ?confirm=yes to purge all data. This cannot be undone.", 400)

    deleted_events = 0
    deleted_aggregates = 0

    # Purge events
    kwargs = {"KeyConditionExpression": Key("project_id").eq(project_id)}
    while True:
        result = events_table().query(**kwargs, ProjectionExpression="project_id, timestamp_id", Limit=25)
        items = result.get("Items", [])
        if not items:
            break
        with events_table().batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={"project_id": item["project_id"], "timestamp_id": item["timestamp_id"]})
                deleted_events += 1
        if "LastEvaluatedKey" not in result:
            break
        kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]

    # Purge aggregates
    kwargs = {"KeyConditionExpression": Key("pk").eq(project_id)}
    while True:
        result = aggregates_table().query(**kwargs, ProjectionExpression="pk, sk", Limit=25)
        items = result.get("Items", [])
        if not items:
            break
        with aggregates_table().batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
                deleted_aggregates += 1
        if "LastEvaluatedKey" not in result:
            break
        kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]

    return ok({"purged": True, "deleted_events": deleted_events, "deleted_aggregates": deleted_aggregates})


def _query_all(project_id: str, sk_start: str, sk_end: str) -> list:
    """Query aggregates table with pagination to get all matching items."""
    items = []
    kwargs = {
        "KeyConditionExpression": Key("pk").eq(project_id) & Key("sk").between(sk_start, sk_end),
    }
    while True:
        result = aggregates_table().query(**kwargs)
        items.extend(result.get("Items", []))
        if "LastEvaluatedKey" not in result:
            break
        kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]
    return items


def _dec(val) -> int:
    """Convert Decimal or any numeric to int."""
    if isinstance(val, Decimal):
        return int(val)
    return int(val) if val else 0


def _dec_float(val) -> float:
    """Convert Decimal or any numeric to float (for cost values)."""
    if isinstance(val, Decimal):
        return float(val)
    return float(val) if val else 0.0


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
