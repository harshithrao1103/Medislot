"""Shared DynamoDB slot retrieval used by doctor and patient APIs."""

import os

import boto3
from boto3.dynamodb.conditions import Key


def get_doctor_slots(doctor_id, start_utc, end_utc):
    """Return every slot for a doctor in the supplied UTC start-time range.

    This helper deliberately performs retrieval and pagination only. The
    calling API remains responsible for role-specific status, visibility,
    capacity, blackout, and response-enrichment rules.
    """
    table = boto3.resource("dynamodb").Table(os.environ["SLOTS_TABLE"])
    query = {
        "IndexName": "DoctorStartIndex",
        "KeyConditionExpression": (
            Key("doctorId").eq(str(doctor_id))
            & Key("startTimeUTC").between(start_utc, end_utc)
        ),
        "Limit": 100,
    }

    items = []
    while True:
        response = table.query(**query)
        items.extend(response.get("Items", []))

        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break

        query["ExclusiveStartKey"] = last_key

    return items
