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

    # Build event record
    record = {
        "project_id": project_id,
        "timestamp_id": timestamp_id,
        "event_date": event_date,
        "event_type": event_type,
        "distinct_id": distinct_id,
        "country": country,
        "country_name": country_name,
        "city": city,
        "version": properties.get("version", ""),
        "os": properties.get("os", ""),
        "arch": properties.get("arch", ""),
        "properties": json.dumps(properties) if properties else "{}",
        "ttl": int(now.timestamp()) + (365 * 24 * 3600),  # 1 year TTL
    }

    # Write raw event
    events_table().put_item(Item=record)

    # Update real-time aggregates (atomic counters)
    _increment_aggregate(project_id, f"day#{event_date}", event_type, distinct_id,
                         properties.get("version", ""), properties.get("os", ""), country)
    _increment_aggregate(project_id, f"week#{event_week}", event_type, distinct_id,
                         properties.get("version", ""), properties.get("os", ""), country)
    _increment_aggregate(project_id, f"month#{event_month}", event_type, distinct_id,
                         properties.get("version", ""), properties.get("os", ""), country)

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


def _increment_aggregate(project_id, period_key, event_type, distinct_id, version, os_name, country):
    """Atomically increment counters and add to sets for the given time period.

    Aggregates table schema:
      pk: {project_id}
      sk: {period_key}  (e.g. "day#2026-03-23", "week#2026-W12", "month#2026-03")

    Stored attributes:
      total_events: atomic counter
      unique_ids: string set of distinct_ids
      versions: map of {version: count}
      os_breakdown: map of {os: count}
      countries: map of {country: count}
      event_types: map of {event_type: count}
    """
    table = aggregates_table()

    update_parts = [
        "SET total_events = if_not_exists(total_events, :zero) + :one",
        "project_name = if_not_exists(project_name, :empty)",
    ]
    attr_values = {
        ":zero": Decimal(0),
        ":one": Decimal(1),
        ":empty": "",
        ":did": set([distinct_id]) if distinct_id else set(["anonymous"]),
    }
    attr_names = {}

    # Unique IDs as a string set
    update_parts.append("ADD unique_ids :did")

    # Version breakdown (map counter)
    if version:
        safe_ver = version.replace(".", "_")
        update_parts[0] += f", versions.#v_{safe_ver} = if_not_exists(versions.#v_{safe_ver}, :zero) + :one"
        update_parts.insert(1, "versions = if_not_exists(versions, :empty_map)")
        attr_names[f"#v_{safe_ver}"] = version
        attr_values[":empty_map"] = {}

    # OS breakdown
    if os_name:
        safe_os = os_name.replace(" ", "_").replace(".", "_")
        if ":empty_map" not in attr_values:
            attr_values[":empty_map"] = {}
        update_parts[0] += f", os_breakdown.#os_{safe_os} = if_not_exists(os_breakdown.#os_{safe_os}, :zero) + :one"
        if "os_breakdown = if_not_exists(os_breakdown, :empty_map)" not in update_parts:
            update_parts.insert(1, "os_breakdown = if_not_exists(os_breakdown, :empty_map)")
        attr_names[f"#os_{safe_os}"] = os_name

    # Country breakdown
    if country:
        safe_country = country.replace(" ", "_").replace(".", "_")
        if ":empty_map" not in attr_values:
            attr_values[":empty_map"] = {}
        update_parts[0] += f", countries.#c_{safe_country} = if_not_exists(countries.#c_{safe_country}, :zero) + :one"
        if "countries = if_not_exists(countries, :empty_map)" not in update_parts:
            update_parts.insert(1, "countries = if_not_exists(countries, :empty_map)")
        attr_names[f"#c_{safe_country}"] = country

    # Event type breakdown
    if event_type:
        safe_et = event_type.replace(" ", "_").replace(".", "_")
        if ":empty_map" not in attr_values:
            attr_values[":empty_map"] = {}
        update_parts[0] += f", event_types.#et_{safe_et} = if_not_exists(event_types.#et_{safe_et}, :zero) + :one"
        if "event_types = if_not_exists(event_types, :empty_map)" not in update_parts:
            update_parts.insert(1, "event_types = if_not_exists(event_types, :empty_map)")
        attr_names[f"#et_{safe_et}"] = event_type

    # DynamoDB update expressions need SET and ADD separated
    set_parts = [p for p in update_parts if not p.startswith("ADD")]
    add_parts = [p for p in update_parts if p.startswith("ADD")]

    expression = ", ".join(set_parts)
    if add_parts:
        expression += " " + " ".join(add_parts)

    kwargs = {
        "Key": {"pk": project_id, "sk": period_key},
        "UpdateExpression": expression,
        "ExpressionAttributeValues": attr_values,
    }
    if attr_names:
        kwargs["ExpressionAttributeNames"] = attr_names

    try:
        table.update_item(**kwargs)
    except Exception:
        # Fallback: simpler update if the complex one fails (map initialization race)
        table.update_item(
            Key={"pk": project_id, "sk": period_key},
            UpdateExpression="SET total_events = if_not_exists(total_events, :zero) + :one ADD unique_ids :did",
            ExpressionAttributeValues={":zero": Decimal(0), ":one": Decimal(1), ":did": set([distinct_id or "anonymous"])},
        )
