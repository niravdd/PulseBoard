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
    if method == "PATCH" and project_id:
        if role != "Admin":
            return error("Admin access required to edit projects", 403)
        return _update(project_id, event)
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

    # Optional GitHub repo (owner/repo format)
    github_repo = body.get("github_repo", "").strip()
    github_token = body.get("github_token", "").strip()
    github_status = {}
    if github_repo:
        github_status = _validate_github(github_repo, github_token)

    item = {
        "project_id": project_id,
        "name": name,
        "description": body.get("description", ""),
        "api_key": api_key,
        "created_at": now,
        "updated_at": now,
        "github_repo": github_repo,
        "github_token": github_token,
        "github_status": github_status,
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


def _update(project_id, event):
    """Update a project — name, description, GitHub repo, token."""
    body = parse_body(event)
    now = datetime.now(timezone.utc).isoformat()

    updates = {"updated_at": now}
    if "name" in body:
        updates["name"] = body["name"].strip()
    if "description" in body:
        updates["description"] = body["description"].strip()
    if "github_repo" in body:
        updates["github_repo"] = body["github_repo"].strip()
    if "github_token" in body:
        updates["github_token"] = body["github_token"].strip()

    # Validate GitHub if repo is provided/changed
    repo = updates.get("github_repo") or body.get("github_repo", "")
    token = updates.get("github_token") or body.get("github_token", "")
    if repo:
        # If only repo changed, try to get existing token
        if not token:
            existing = projects_table().get_item(Key={"project_id": project_id}).get("Item", {})
            token = existing.get("github_token", "")
        updates["github_status"] = _validate_github(repo, token)

    # Build DynamoDB update expression
    set_parts = []
    attr_values = {}
    for k, v in updates.items():
        safe_key = k.replace("#", "_")
        set_parts.append(f"#{safe_key} = :{safe_key}")
        attr_values[f":{safe_key}"] = v

    try:
        projects_table().update_item(
            Key={"project_id": project_id},
            UpdateExpression="SET " + ", ".join(set_parts),
            ExpressionAttributeNames={f"#{k.replace('#', '_')}": k for k in updates},
            ExpressionAttributeValues=attr_values,
            ConditionExpression="attribute_exists(project_id)",
        )
    except Exception:
        return error("Project not found", 404)

    # Return the full updated project
    result = projects_table().get_item(Key={"project_id": project_id})
    return ok(result.get("Item", {}))


def _validate_github(repo: str, token: str) -> dict:
    """Validate GitHub repo access and fetch basic info.

    Returns a status dict with repo info or error details.
    """
    import urllib.request
    import urllib.error

    if "/" not in repo:
        return {"valid": False, "error": "Format must be owner/repo"}

    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "PulseBoard"}
    if token:
        headers["Authorization"] = f"token {token}"

    # Check repo exists and is accessible
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}", headers=headers)
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
        result = {
            "valid": True,
            "full_name": data.get("full_name", repo),
            "private": data.get("private", False),
            "stars": data.get("stargazers_count", 0),
            "forks": data.get("forks_count", 0),
            "language": data.get("language", ""),
        }

        # Test traffic API access (requires push access or admin)
        try:
            treq = urllib.request.Request(f"https://api.github.com/repos/{repo}/traffic/views", headers=headers)
            urllib.request.urlopen(treq, timeout=10)
            result["traffic_access"] = True
        except urllib.error.HTTPError as e:
            result["traffic_access"] = False
            result["traffic_error"] = f"HTTP {e.code}" if e.code == 403 else str(e)

        return result
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"valid": False, "error": "Repository not found (may be private — provide a token)"}
        if e.code == 401:
            return {"valid": False, "error": "Invalid GitHub token"}
        return {"valid": False, "error": f"GitHub API error: HTTP {e.code}"}
    except Exception as exc:
        return {"valid": False, "error": str(exc)}


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
