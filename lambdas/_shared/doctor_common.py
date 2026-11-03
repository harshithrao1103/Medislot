"""Milestone 3 shared API helpers; Milestone 2 auth_common remains unchanged."""
import json
import os
import re
import datetime as dt
from decimal import Decimal
import pymysql
from auth_common import db_connection, response, cid, body_json

class ApiError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message

def doctor_id(event):
    ctx = (event.get('requestContext') or {}).get('authorizer') or {}
    if str(ctx.get('role', '')).lower() != 'doctor':
        raise ApiError(403, 'Doctor role required')
    try:
        return int(ctx['userId'])
    except (KeyError, ValueError, TypeError):
        raise ApiError(401, 'Missing authorized identity')

def require_text(value, field, maxlen):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maxlen:
        raise ApiError(400, f'{field} must be 1-{maxlen} characters')
    return value.strip()

def parse_date(value):
    try:
        return dt.date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ApiError(400, 'Expected date YYYY-MM-DD')

def parse_time(value):
    try:
        parsed = dt.time.fromisoformat(value)
        if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
            raise ValueError()
        return parsed
    except (ValueError, TypeError):
        raise ApiError(400, 'Expected time HH:MM')

def serialize(obj):
    if isinstance(obj, (dt.date, dt.datetime, dt.time)):
        return obj.isoformat()
    if isinstance(obj, dt.timedelta):
        seconds = int(obj.total_seconds())
        return f'{seconds//3600:02d}:{(seconds%3600)//60:02d}'
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(str(type(obj)))

def respond(status, data, correlation):
    result = response(status, {}, correlation)
    result['body'] = json.dumps(data, default=serialize)
    return result

def dispatch_error(exc, correlation):
    if isinstance(exc, ApiError):
        return respond(exc.status, {'error': exc.message}, correlation)
    if isinstance(exc, pymysql.err.IntegrityError):
        return respond(409, {'error': 'Conflicting or referenced record'}, correlation)
    import logging
    logging.exception('Doctor API failure correlationId=%s', correlation)
    return respond(500, {'error': 'Internal server error'}, correlation)
