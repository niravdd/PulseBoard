"""Ingest handler — receives telemetry pings from client applications.

Validates the project API key, extracts geographic data from CloudFront headers,
writes the raw event, and updates daily/weekly/monthly aggregates in real-time.

Endpoint: POST /ingest
Auth: API key (X-Api-Key header or api_key in body) — NOT Cognito
"""

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from boto3.dynamodb.conditions import Key

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.db import events_table, projects_table, aggregates_table
from shared.response import ok, error, parse_body


def handler(event, context):
    body = parse_body(event)
    headers = event.get("headers", {}) or {}

    # Extract API key from header or body
    api_key = (
        headers.get("x-api-key")
        or headers.get("X-Api-Key")
        or body.get("api_key", "")
    )
    if not api_key:
        return error("Missing API key. Send X-Api-Key header or api_key in body.", 401)

    # Validate API key → resolve project
    project = _resolve_project(api_key)
    if not project:
        return error("Invalid API key.", 403)

    project_id = project["project_id"]

    # Extract event data
    event_type = body.get("event", "ping")
    properties = body.get("properties", {})
    distinct_id = body.get("distinct_id", "anonymous")

    # Extract geographic data from CloudFront headers (free!)
    country = (
        headers.get("cloudfront-viewer-country")
        or headers.get("CloudFront-Viewer-Country")
        or properties.get("country", "")
    )
    country_name = (
        headers.get("cloudfront-viewer-country-name")
        or headers.get("CloudFront-Viewer-Country-Name")
        or ""
    )
    city = (
        headers.get("cloudfront-viewer-city")
        or headers.get("CloudFront-Viewer-City")
        or ""
    )

    now = datetime.now(timezone.utc)
    timestamp_id = f"{now.isoformat()}#{uuid.uuid4().hex[:8]}"
    event_date = now.strftime("%Y-%m-%d")
    event_week = now.strftime("%Y-W%W")
    event_month = now.strftime("%Y-%m")

    # Extract structured fields from properties for indexed querying
    cost_usd = Decimal(str(properties.get("cost_usd", 0))) if properties.get("cost_usd") else Decimal(0)
    duration_ms = Decimal(str(properties.get("duration_ms", 0))) if properties.get("duration_ms") else Decimal(0)

    # Build event record — store both indexed fields and full properties blob
    record = {
        "project_id": project_id,
        "timestamp_id": timestamp_id,
        "event_date": event_date,
        "event_type": event_type,
        "distinct_id": distinct_id,
        # Geography (from CloudFront headers)
        "country": country,
        "country_name": country_name,
        "city": city,
        # System info
        "version": properties.get("version", ""),
        "os": properties.get("os", ""),
        "os_version": properties.get("os_version", ""),
        "arch": properties.get("arch", ""),
        "python": properties.get("python", ""),
        "cpu_count": properties.get("cpu_count", 0),
        # Usage metrics
        "cost_usd": cost_usd,
        "duration_ms": duration_ms,
        "model": properties.get("model", ""),
        "feature": properties.get("feature", ""),
        "error_type": properties.get("error_type", ""),
        # Full properties for anything we didn't index
        "properties": json.dumps(properties) if properties else "{}",
    }

    # Write raw event
    events_table().put_item(Item=record)

    # Update real-time aggregates (atomic counters)
    # Cost-only events (*.cost suffix) only update total_cost_usd — they don't count
    # as separate events or inflate model/version/OS counts (the paired .generate
    # event already counted those).
    model = properties.get("model", "")
    is_cost_event = event_type.endswith(".cost")

    for period_key in [f"day#{event_date}", f"week#{event_week}", f"month#{event_month}"]:
        if is_cost_event:
            # Cost-only: update total_cost_usd only (no event count, no model/version inflation)
            _increment_cost_only(project_id, period_key, cost_usd)
        else:
            # Action event: count the event, but do NOT add cost to aggregate
            # (cost aggregation comes exclusively from .cost suffix events)
            _increment_aggregate(project_id, period_key, event_type, distinct_id,
                                 properties.get("version", ""), properties.get("os", ""),
                                 country, Decimal(0), model)

    return ok({"status": "ok", "project": project.get("name", project_id)})


def _resolve_project(api_key: str) -> dict | None:
    """Look up project by API key using the GSI."""
    result = projects_table().query(
        IndexName="api_key-index",
        KeyConditionExpression=Key("api_key").eq(api_key),
        Limit=1,
    )
    items = result.get("Items", [])
    return items[0] if items else None


def _increment_cost_only(project_id, period_key, cost_usd):
    """Update only total_cost_usd for cost-only events (*.cost suffix).

    These events are paired with a .generate event that already counted
    the action, model, version, etc. We only need the cost from this one.
    """
    if cost_usd <= 0:
        return
    table = aggregates_table()
    key = {"pk": project_id, "sk": period_key}
    try:
        table.update_item(
            Key=key,
            UpdateExpression="SET total_cost_usd = if_not_exists(total_cost_usd, :zero) + :cost",
            ExpressionAttributeValues={":cost": cost_usd, ":zero": Decimal(0)},
        )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Cost-only update failed for %s/%s: %s", project_id, period_key, exc)


def _increment_aggregate(project_id, period_key, event_type, distinct_id, version, os_name, country, cost_usd=Decimal(0), model=""):
    """Atomically increment counters and update breakdown maps.

    Two-step approach to avoid DynamoDB nested map initialization issues:
    Step 1: Ensure the item exists with all map fields initialized.
    Step 2: Increment counters and add to breakdown maps.
    """
    table = aggregates_table()
    key = {"pk": project_id, "sk": period_key}

    # Step 1: Ensure item + empty maps exist (idempotent — only sets if missing)
    try:
        table.update_item(
            Key=key,
            UpdateExpression=(
                "SET versions = if_not_exists(versions, :em), "
                "os_breakdown = if_not_exists(os_breakdown, :em), "
                "countries = if_not_exists(countries, :em), "
                "event_types = if_not_exists(event_types, :em), "
                "models = if_not_exists(models, :em), "
                "total_events = if_not_exists(total_events, :zero), "
                "total_cost_usd = if_not_exists(total_cost_usd, :zero)"
            ),
            ExpressionAttributeValues={":em": {}, ":zero": Decimal(0)},
        )
    except Exception:
        pass

    # Step 2: Increment counters + update maps (item guaranteed to exist now)
    set_parts = [
        "total_events = total_events + :one",
        "total_cost_usd = total_cost_usd + :cost",
    ]
    attr_values = {
        ":one": Decimal(1),
        ":cost": cost_usd,
        ":did": set([distinct_id or "anonymous"]),
    }
    attr_names = {}

    if version:
        set_parts.append(f"versions.#ver = if_not_exists(versions.#ver, :zero) + :one")
        attr_names["#ver"] = version
        attr_values[":zero"] = Decimal(0)

    if os_name:
        set_parts.append(f"os_breakdown.#osn = if_not_exists(os_breakdown.#osn, :zero) + :one")
        attr_names["#osn"] = os_name
        if ":zero" not in attr_values:
            attr_values[":zero"] = Decimal(0)

    if country:
        set_parts.append(f"countries.#cty = if_not_exists(countries.#cty, :zero) + :one")
        attr_names["#cty"] = country
        if ":zero" not in attr_values:
            attr_values[":zero"] = Decimal(0)

    if event_type:
        set_parts.append(f"event_types.#evt = if_not_exists(event_types.#evt, :zero) + :one")
        attr_names["#evt"] = event_type
        if ":zero" not in attr_values:
            attr_values[":zero"] = Decimal(0)

    if model:
        set_parts.append(f"models.#mdl = if_not_exists(models.#mdl, :zero) + :one")
        attr_names["#mdl"] = model
        if ":zero" not in attr_values:
            attr_values[":zero"] = Decimal(0)

    expression = "SET " + ", ".join(set_parts) + " ADD unique_ids :did"

    try:
        kwargs = {
            "Key": key,
            "UpdateExpression": expression,
            "ExpressionAttributeValues": attr_values,
        }
        if attr_names:
            kwargs["ExpressionAttributeNames"] = attr_names
        table.update_item(**kwargs)
    except Exception as exc:
        # Log but don't fail the ingest
        import logging
        logging.getLogger(__name__).warning("Aggregate update failed for %s/%s: %s", project_id, period_key, exc)
