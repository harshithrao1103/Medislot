#!/usr/bin/env python3
"""Remove the retired heldCount attribute from existing MediSlot Slots items.

Dry-run is the default. Pass --apply to perform the updates.
The script does not delete or modify bookings, slot capacity, status, or version.
"""

import argparse
import os
import sys
from pathlib import Path

try:
    import boto3
    from boto3.dynamodb.conditions import Attr
except ImportError:
    # The repository includes the Lambda dependency bundle for local use.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "authorizer-code"))
    import boto3
    from boto3.dynamodb.conditions import Attr


def main():
    parser = argparse.ArgumentParser(
        description="Remove the retired heldCount attribute from a DynamoDB Slots table."
    )
    parser.add_argument(
        "--table",
        default=os.environ.get("SLOTS_TABLE"),
        help="DynamoDB Slots table name (defaults to SLOTS_TABLE).",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1",
        help="AWS region (default: AWS_REGION, AWS_DEFAULT_REGION, or us-east-1).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually remove heldCount. Without this flag, only report matching items.",
    )
    args = parser.parse_args()

    if not args.table:
        parser.error("Provide --table or set the SLOTS_TABLE environment variable.")

    table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)
    scan_kwargs = {
        "FilterExpression": Attr("heldCount").exists(),
        "ProjectionExpression": "#doctor, #slot",
        "ExpressionAttributeNames": {
            "#doctor": "doctorId",
            "#slot": "slotKey",
        },
    }

    scanned_matches = 0
    removed = 0
    last_key = None

    while True:
        if last_key:
            scan_kwargs["ExclusiveStartKey"] = last_key
        page = table.scan(**scan_kwargs)

        for item in page.get("Items", []):
            scanned_matches += 1
            key = {"doctorId": item["doctorId"], "slotKey": item["slotKey"]}

            if args.apply:
                try:
                    table.update_item(
                        Key=key,
                        UpdateExpression="REMOVE heldCount",
                        ConditionExpression="attribute_exists(heldCount)",
                    )
                    removed += 1
                except Exception as exc:
                    print(
                        f"ERROR removing heldCount for doctorId={key['doctorId']} "
                        f"slotKey={key['slotKey']}: {exc}",
                        file=sys.stderr,
                    )
                    raise
            else:
                print(
                    f"Would remove heldCount: doctorId={key['doctorId']} "
                    f"slotKey={key['slotKey']}"
                )

        last_key = page.get("LastEvaluatedKey")
        if not last_key:
            break

    if args.apply:
        print(f"Removed heldCount from {removed} slot item(s) in {args.table}.")
    else:
        print(
            f"Dry run: found {scanned_matches} slot item(s) with heldCount in "
            f"{args.table}. Re-run with --apply to remove the attribute."
        )


if __name__ == "__main__":
    main()
