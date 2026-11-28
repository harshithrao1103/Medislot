"""Pure domain rules shared by the Milestone 6 Lambda and its unit tests."""
import datetime as dt
import hashlib


def fifo_waiting_entries(entries):
    """Return only waiting entries in deterministic FIFO order (timestamp + entry id)."""
    return sorted((e for e in entries if e.get('status') == 'WAITING'), key=lambda e: e['SK'])


def offer_is_expired(expires_at, now):
    """Compare timezone-aware ISO timestamps; naive timestamps are rejected."""
    expiry = dt.datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
    if expiry.tzinfo is None or now.tzinfo is None:
        raise ValueError('Offer timestamps must be timezone-aware')
    return expiry <= now


def dispatch_id(event_identity, event_type, recipient_id):
    """Stable notification claim key; recipient-specific and event-specific."""
    return hashlib.sha256(f'{event_identity}#{event_type}#{recipient_id}'.encode()).hexdigest()


def reschedule_allowed(status, reschedule_count, start_time, now, max_count=2, cutoff_hours=6):
    """Return (allowed, reason) for frozen reschedule rules."""
    if status != 'CONFIRMED':
        return False, 'NOT_CONFIRMED'
    if int(reschedule_count) >= int(max_count):
        return False, 'LIMIT_REACHED'
    if start_time.tzinfo is None or now.tzinfo is None:
        raise ValueError('Reschedule timestamps must be timezone-aware')
    if (start_time - now).total_seconds() < int(cutoff_hours) * 3600:
        return False, 'CUTOFF_REACHED'
    return True, 'OK'
