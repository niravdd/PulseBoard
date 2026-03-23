"""DynamoDB helpers for PulseBoard Lambda functions."""

import os
import boto3

_dynamodb = boto3.resource("dynamodb")

EVENTS_TABLE = os.environ.get("EVENTS_TABLE", "pulseboard-events-prod")
PROJECTS_TABLE = os.environ.get("PROJECTS_TABLE", "pulseboard-projects-prod")
AGGREGATES_TABLE = os.environ.get("AGGREGATES_TABLE", "pulseboard-aggregates-prod")


def events_table():
    return _dynamodb.Table(EVENTS_TABLE)


def projects_table():
    return _dynamodb.Table(PROJECTS_TABLE)


def aggregates_table():
    return _dynamodb.Table(AGGREGATES_TABLE)
