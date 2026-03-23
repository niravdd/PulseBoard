"""Standard API response builders."""

import json


def ok(body, status=200):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization,X-Api-Key",
        },
        "body": json.dumps(body, default=str),
    }


def error(message, status=400):
    return ok({"error": message}, status)


def parse_body(event):
    """Parse JSON body from API Gateway event."""
    body = event.get("body", "")
    if not body:
        return {}
    try:
        return json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return {}
