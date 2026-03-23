"""GitHub Traffic fetcher — scheduled Lambda that pulls repo traffic data daily.

GitHub only retains 14 days of traffic data. This function runs daily,
fetches clones + views for all projects with a github_repo configured,
and stores the data in the Aggregates table for permanent retention.

Triggered by: EventBridge scheduled rule (daily)
"""

import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timezone
from decimal import Decimal

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.db import projects_table, aggregates_table

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    """Scan all projects, fetch GitHub traffic for those with repos configured."""
    result = projects_table().scan()
    projects = result.get("Items", [])

    fetched = 0
    errors = 0

    for project in projects:
        repo = project.get("github_repo", "")
        token = project.get("github_token", "")
        if not repo:
            continue

        project_id = project["project_id"]
        try:
            _fetch_and_store(project_id, repo, token)
            fetched += 1
        except Exception as exc:
            logger.error("Failed to fetch traffic for %s (%s): %s", project_id, repo, exc)
            errors += 1

    logger.info("GitHub traffic fetch complete: %d fetched, %d errors", fetched, errors)
    return {"fetched": fetched, "errors": errors}


def _fetch_and_store(project_id: str, repo: str, token: str):
    """Fetch GitHub traffic data and store in aggregates table."""
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "PulseBoard"}
    if token:
        headers["Authorization"] = f"token {token}"

    # Fetch clones (14-day window)
    clones_data = _github_get(f"https://api.github.com/repos/{repo}/traffic/clones", headers)
    # Fetch views (14-day window)
    views_data = _github_get(f"https://api.github.com/repos/{repo}/traffic/views", headers)
    # Fetch referrers (top 10)
    referrers = _github_get(f"https://api.github.com/repos/{repo}/traffic/popular/referrers", headers)
    # Fetch popular paths (top 10)
    paths = _github_get(f"https://api.github.com/repos/{repo}/traffic/popular/paths", headers)
    # Fetch repo stats
    repo_data = _github_get(f"https://api.github.com/repos/{repo}", headers)

    if not clones_data or not views_data:
        logger.warning("No traffic data returned for %s (may lack push access)", repo)
        return

    # Store daily clone/view data (permanent — GitHub only keeps 14 days)
    for clone_day in clones_data.get("clones", []):
        date = clone_day["timestamp"][:10]  # "2026-03-23T00:00:00Z" → "2026-03-23"
        _upsert_github_day(project_id, date, {
            "gh_clones": Decimal(str(clone_day.get("count", 0))),
            "gh_clones_unique": Decimal(str(clone_day.get("uniques", 0))),
        })

    for view_day in views_data.get("views", []):
        date = view_day["timestamp"][:10]
        _upsert_github_day(project_id, date, {
            "gh_views": Decimal(str(view_day.get("count", 0))),
            "gh_views_unique": Decimal(str(view_day.get("uniques", 0))),
        })

    # Store summary (latest snapshot)
    now = datetime.now(timezone.utc).isoformat()
    summary = {
        "gh_total_clones": Decimal(str(clones_data.get("count", 0))),
        "gh_unique_cloners": Decimal(str(clones_data.get("uniques", 0))),
        "gh_total_views": Decimal(str(views_data.get("count", 0))),
        "gh_unique_visitors": Decimal(str(views_data.get("uniques", 0))),
        "gh_stars": Decimal(str(repo_data.get("stargazers_count", 0))) if repo_data else Decimal(0),
        "gh_forks": Decimal(str(repo_data.get("forks_count", 0))) if repo_data else Decimal(0),
        "gh_open_issues": Decimal(str(repo_data.get("open_issues_count", 0))) if repo_data else Decimal(0),
        "gh_watchers": Decimal(str(repo_data.get("subscribers_count", 0))) if repo_data else Decimal(0),
        "gh_referrers": json.dumps(referrers[:10]) if isinstance(referrers, list) else "[]",
        "gh_popular_paths": json.dumps(paths[:10]) if isinstance(paths, list) else "[]",
        "gh_fetched_at": now,
    }

    aggregates_table().update_item(
        Key={"pk": project_id, "sk": "github#summary"},
        UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in summary),
        ExpressionAttributeNames={f"#{k}": k for k in summary},
        ExpressionAttributeValues={f":{k}": v for k, v in summary.items()},
    )

    logger.info("Stored GitHub traffic for %s: %d clones, %d views, %d stars",
                repo, clones_data.get("count", 0), views_data.get("count", 0),
                repo_data.get("stargazers_count", 0) if repo_data else 0)


def _upsert_github_day(project_id: str, date: str, metrics: dict):
    """Upsert GitHub daily metrics into aggregates table."""
    set_parts = []
    attr_values = {}
    attr_names = {}

    for k, v in metrics.items():
        set_parts.append(f"#{k} = :{k}")
        attr_values[f":{k}"] = v
        attr_names[f"#{k}"] = k

    aggregates_table().update_item(
        Key={"pk": project_id, "sk": f"ghday#{date}"},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=attr_names,
        ExpressionAttributeValues=attr_values,
    )


def _github_get(url: str, headers: dict):
    """Make a GET request to the GitHub API. Returns parsed JSON or None."""
    try:
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 403:
            logger.warning("GitHub API 403 for %s (rate limit or no push access)", url)
        elif e.code == 404:
            logger.warning("GitHub API 404 for %s", url)
        else:
            logger.warning("GitHub API %d for %s", e.code, url)
        return None
    except Exception as exc:
        logger.warning("GitHub API error for %s: %s", url, exc)
        return None
