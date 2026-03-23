"""Projects handler — CRUD for project registration.

All endpoints require Cognito authentication (dashboard admin).
Each project gets a unique API key for telemetry ingestion.

Endpoints:
  POST   /projects              — Register a new project
  GET    /projects              — List all projects
  GET    /projects/{project_id} — Get project details
  DELETE /projects/{project_id} — Delete a project
  POST   /projects/{project_id}/regen-key — Regenerate API key
"""

import uuid
from datetime import datetime, timezone

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.db import projects_table
from shared.response import ok, error, parse_body


def handler(event, context):
    method = event.get("httpMethod", "GET")
    path = event.get("path", "")
    path_params = event.get("pathParameters") or {}
    project_id = path_params.get("project_id")

    if method == "POST" and "regen-key" in path:
        return _regen_key(project_id)
    if method == "POST":
        return _create(event)
    if method == "GET" and project_id:
        return _get(project_id)
    if method == "GET":
        return _list()
    if method == "DELETE" and project_id:
        return _delete(project_id)

    return error("Method not allowed", 405)


def _create(event):
    body = parse_body(event)
    name = body.get("name", "").strip()
    if not name:
        return error("Project name is required")

    project_id = str(uuid.uuid4())[:12]
    api_key = f"pb_{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc).isoformat()

    item = {
        "project_id": project_id,
        "name": name,
        "description": body.get("description", ""),
        "api_key": api_key,
        "created_at": now,
        "updated_at": now,
        "settings": {
            "retention_days": body.get("retention_days", 365),
            "allowed_events": body.get("allowed_events", []),
        },
    }

    projects_table().put_item(Item=item)

    return ok(item, 201)


def _list():
    result = projects_table().scan()
    items = result.get("Items", [])
    # Sort by name
    items.sort(key=lambda x: x.get("name", ""))
    # Mask API keys in list view (show only prefix)
    for item in items:
        key = item.get("api_key", "")
        item["api_key_preview"] = key[:7] + "..." if len(key) > 7 else key
        del item["api_key"]
    return ok({"projects": items})


def _get(project_id):
    result = projects_table().get_item(Key={"project_id": project_id})
    item = result.get("Item")
    if not item:
        return error("Project not found", 404)
    return ok(item)


def _delete(project_id):
    projects_table().delete_item(Key={"project_id": project_id})
    return ok({"deleted": project_id})


def _regen_key(project_id):
    if not project_id:
        return error("Project ID required")

    new_key = f"pb_{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc).isoformat()

    try:
        projects_table().update_item(
            Key={"project_id": project_id},
            UpdateExpression="SET api_key = :key, updated_at = :now",
            ExpressionAttributeValues={":key": new_key, ":now": now},
            ConditionExpression="attribute_exists(project_id)",
        )
    except Exception:
        return error("Project not found", 404)

    return ok({"project_id": project_id, "api_key": new_key, "regenerated_at": now})
