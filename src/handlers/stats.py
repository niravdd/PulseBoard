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

    # Last 30 days (today + 29 previous = 30 days total)
    start_30d = (now - timedelta(days=29)).strftime("%Y-%m-%d")
    days_30 = [d for d in days if d.get("sk", "") >= f"day#{start_30d}"]
    total_30d = sum(_dec(d.get("total_events", 0)) for d in days_30)
    cost_30d = sum(_dec_float(d.get("total_cost_usd", 0)) for d in days_30)
    unique_30d = set()
    for d in days_30:
        unique_30d.update(d.get("unique_ids", set()))

    # Last 7 days (today + 6 previous = 7 days total)
    start_7d = (now - timedelta(days=6)).strftime("%Y-%m-%d")
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
    # Top version, OS, country — use last 7 days for relevance (not all-time)
    # All-time counts bias toward old versions that accumulated more events.
    versions_recent = {}
    os_recent = {}
    countries_recent = {}
    for d in days_7:
        for v, c in (d.get("versions") or {}).items():
            versions_recent[v] = versions_recent.get(v, 0) + _dec(c)
        for o, c in (d.get("os_breakdown") or {}).items():
            os_recent[o] = os_recent.get(o, 0) + _dec(c)
        for co, c in (d.get("countries") or {}).items():
            countries_recent[co] = countries_recent.get(co, 0) + _dec(c)

    # Fallback to all-time if no recent data
    if not versions_recent:
        for d in days:
            for v, c in (d.get("versions") or {}).items():
                versions_recent[v] = versions_recent.get(v, 0) + _dec(c)
    if not os_recent:
        for d in days:
            for o, c in (d.get("os_breakdown") or {}).items():
                os_recent[o] = os_recent.get(o, 0) + _dec(c)

    return ok({
        "project_id": project_id,
        "today": {"events": total_today, "cost_usd": round(cost_today, 4)},
        "last_7d": {"events": total_7d, "unique_deployments": len(unique_7d), "cost_usd": round(cost_7d, 4)},
        "last_30d": {"events": total_30d, "unique_deployments": len(unique_30d), "cost_usd": round(cost_30d, 4)},
        "lifetime": {"events": total_lifetime, "unique_deployments": len(unique_lifetime), "cost_usd": round(cost_lifetime, 4)},
        "top_version": _top_n(versions_recent, 1),
        "top_os": _top_n(os_recent, 1),
        "top_country": _top_n(countries_recent, 1),
    })


def _resolve_date_range(qs):
    """Resolve start/end dates from query params. Supports days=N, from/to, or days=0 (lifetime).

    days=1 means "today" (current UTC date only).
    days=7 means "last 7 days" (today minus 6 days through today).
    days=0 means "lifetime" (all data).
    """
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
    if days_back == 1:
        # "Today" — just today's date
        today = now.strftime("%Y-%m-%d")
        return today, today
    start = (now - timedelta(days=days_back - 1)).strftime("%Y-%m-%d")
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
    # Also collects per-model costs and per-deployment costs.
    unique_countries = {}
    unique_os = {}
    unique_versions = {}
    model_costs = {}       # model_name → total_cost_usd
    deployment_costs = {}  # distinct_id → total_cost_usd
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
                ProjectionExpression="distinct_id, country, os, version, timestamp_id, cost_usd, model")
            for item in result.get("Items", []):
                did = item.get("distinct_id", "")
                ts = item.get("timestamp_id", "")

                # Per-model cost
                m = item.get("model", "")
                c_usd = _dec_float(item.get("cost_usd", 0))
                if m and c_usd > 0:
                    model_costs[m] = model_costs.get(m, 0.0) + c_usd
                # Per-deployment cost
                if did and c_usd > 0:
                    deployment_costs[did] = deployment_costs.get(did, 0.0) + c_usd

                if not did:
                    continue
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

    # Build per-deployment list with costs
    deployments = sorted(
        [
            {
                "id": did[:12],
                "full_id": did,
                "version": info["version"],
                "os": info["os"],
                "country": info["country"],
                "cost_usd": round(deployment_costs.get(did, 0.0), 4),
            }
            for did, info in latest_per_deployment.items()
        ],
        key=lambda x: x["version"] or "",
        reverse=True,
    )

    # Build models list with costs
    models_with_cost = []
    for item in _sorted_map(models):
        item["cost_usd"] = round(model_costs.get(item["name"], 0.0), 4)
        models_with_cost.append(item)

    return ok({
        "project_id": project_id,
        "date_range": {"from": date_from, "to": date_to},
        "total_cost_usd": round(total_cost, 4),
        "versions": _unique_map(unique_versions) if unique_versions else _sorted_map(versions),
        "os": _unique_map(unique_os) if unique_os else _sorted_map(os_breakdown),
        "countries": _unique_map(unique_countries) if unique_countries else _sorted_map(countries),
        "event_types": _sorted_map(event_types),
        "models": models_with_cost,
        "deployments": deployments,
    })


def _events(project_id, qs):
    """Recent raw events, paginated. Supports date filtering and event_type search."""
    limit = int(qs.get("limit", 50))
    start_key = qs.get("cursor")
    event_type_filter = qs.get("event_type", "").strip()

    # Date filtering — timestamp_id starts with ISO date so we can filter by range
    date_from, date_to = _resolve_date_range(qs)

    # Build key condition
    if date_from != "0000-01-01" and date_to != "9999-12-31":
        key_cond = (
            Key("project_id").eq(project_id) &
            Key("timestamp_id").between(date_from, date_to + "~")
        )
    else:
        key_cond = Key("project_id").eq(project_id)

    # Get total count for pagination (separate count query)
    # Substring match: "cost" matches image_studio.cost, video_studio.cost, etc.
    from boto3.dynamodb.conditions import Attr
    count_kwargs = {"KeyConditionExpression": key_cond, "Select": "COUNT"}
    if event_type_filter:
        count_kwargs["FilterExpression"] = Attr("event_type").contains(event_type_filter)
    total_count = 0
    while True:
        count_result = events_table().query(**count_kwargs)
        total_count += count_result.get("Count", 0)
        if "LastEvaluatedKey" not in count_result:
            break
        count_kwargs["ExclusiveStartKey"] = count_result["LastEvaluatedKey"]

    # Fetch events page
    kwargs = {
        "KeyConditionExpression": key_cond,
        "ScanIndexForward": False,  # newest first
    }

    if event_type_filter:
        kwargs["FilterExpression"] = Attr("event_type").contains(event_type_filter)
        # With FilterExpression, Limit applies pre-filter — fetch more to compensate
        kwargs["Limit"] = limit * 10
    else:
        kwargs["Limit"] = limit

    if start_key:
        kwargs["ExclusiveStartKey"] = {"project_id": project_id, "timestamp_id": start_key}

    # For filtered queries, paginate until we have enough matching items
    items = []
    next_cursor = None
    while len(items) < limit:
        result = events_table().query(**kwargs)
        for item in result.get("Items", []):
            items.append(item)
            if len(items) >= limit:
                break
        if "LastEvaluatedKey" not in result:
            break
        kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]

    # If we have more than limit, trim and set cursor from the last kept item
    if len(items) > limit:
        items = items[:limit]
    if len(items) == limit and "LastEvaluatedKey" in result:
        next_cursor = items[-1].get("timestamp_id")
    elif "LastEvaluatedKey" in result:
        next_cursor = result["LastEvaluatedKey"].get("timestamp_id")

    # Parse properties JSON back to dict
    for item in items:
        try:
            item["properties"] = json.loads(item.get("properties", "{}"))
        except (json.JSONDecodeError, TypeError):
            pass

    return ok({"events": items, "cursor": next_cursor, "count": len(items), "total_count": total_count})


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
        "fetch_interval_hours": int(os.environ.get("GITHUB_FETCH_INTERVAL_HOURS", "6")),
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
