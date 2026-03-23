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

import boto3

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.db import projects_table
from shared.response import ok, error, parse_body

USER_POOL_ID = os.environ.get("USER_POOL_ID", "")


def _get_user_role(event):
    """Extract user role from Cognito JWT claims. Returns 'Admin' or 'Viewer'."""
    try:
        claims = event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
        groups = claims.get("cognito:groups", "")
        if "Admins" in groups:
            return "Admin"
    except Exception:
        pass
    return "Viewer"


def handler(event, context):
    method = event.get("httpMethod", "GET")
    path = event.get("path", "")
    path_params = event.get("pathParameters") or {}
    project_id = path_params.get("project_id")

    role = _get_user_role(event)

    # Admin-only actions
    if method == "POST" and "regen-key" in path:
        if role != "Admin":
            return error("Admin access required", 403)
        return _regen_key(project_id)
    if method == "POST" and "admin/invite" in path:
        if role != "Admin":
            return error("Admin access required", 403)
        return _invite_admin(event)
    if method == "GET" and "admin/users" in path:
        return _list_admins()
    if method == "POST":
        if role != "Admin":
            return error("Admin access required to create projects", 403)
        return _create(event)
    if method == "GET" and project_id:
        return _get(project_id)
    if method == "GET":
        return _list()
    if method == "DELETE" and project_id:
        if role != "Admin":
            return error("Admin access required to delete projects", 403)
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


def _invite_admin(event):
    """Invite a new user to PulseBoard via Cognito with a role (Admin or Viewer)."""
    body = parse_body(event)
    email = body.get("email", "").strip()
    role = body.get("role", "Viewer")  # Default to Viewer
    if role not in ("Admin", "Viewer"):
        return error("Role must be 'Admin' or 'Viewer'")
    if not email:
        return error("Email is required")
    if not USER_POOL_ID:
        return error("User Pool not configured", 500)

    group_name = "Admins" if role == "Admin" else "Viewers"

    try:
        cognito = boto3.client("cognito-idp")
        cognito.admin_create_user(
            UserPoolId=USER_POOL_ID,
            Username=email,
            UserAttributes=[{"Name": "email", "Value": email}],
            DesiredDeliveryMediums=["EMAIL"],
        )
        # Add to the appropriate group
        cognito.admin_add_user_to_group(
            UserPoolId=USER_POOL_ID,
            Username=email,
            GroupName=group_name,
        )
        return ok({"invited": email, "role": role, "message": f"Temporary password sent via email ({role} access)"}, 201)
    except cognito.exceptions.UsernameExistsException:
        return error(f"User {email} already exists")
    except Exception as exc:
        return error(f"Failed to invite user: {exc}")


def _list_admins():
    """List all users in the Cognito User Pool with their roles."""
    if not USER_POOL_ID:
        return error("User Pool not configured", 500)
    try:
        cognito = boto3.client("cognito-idp")
        result = cognito.list_users(UserPoolId=USER_POOL_ID, Limit=50)
        users = []
        for u in result.get("Users", []):
            email = ""
            for attr in u.get("Attributes", []):
                if attr["Name"] == "email":
                    email = attr["Value"]
            # Get user's groups
            try:
                groups_result = cognito.admin_list_groups_for_user(
                    UserPoolId=USER_POOL_ID, Username=u["Username"], Limit=10,
                )
                groups = [g["GroupName"] for g in groups_result.get("Groups", [])]
            except Exception:
                groups = []
            role = "Admin" if "Admins" in groups else "Viewer" if "Viewers" in groups else "No role"
            users.append({
                "email": email,
                "username": u["Username"],
                "role": role,
                "status": u.get("UserStatus", ""),
                "created": str(u.get("UserCreateDate", "")),
                "enabled": u.get("Enabled", True),
            })
        return ok({"users": users})
    except Exception as exc:
        return error(f"Failed to list users: {exc}")


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
