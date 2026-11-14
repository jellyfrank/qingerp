"""Best-effort permission diagnostics, isolated from business transactions."""

import hashlib
import json
import logging
import os
import queue
import threading
import time
from collections import Counter
from datetime import datetime, timezone

from odoo.modules.registry import Registry


_logger = logging.getLogger(__name__)
_QUEUE_LIMIT = 512
_USER_EVENTS_PER_MINUTE = 120
_events = queue.Queue(maxsize=_QUEUE_LIMIT)
_lock = threading.Lock()
_worker = None
_worker_pid = None
_minute = 0
_user_counts = Counter()
_drops = Counter()


_reported_drops = Counter()


def _ensure_worker():
    global _worker, _worker_pid
    try:
        with _lock:
            if _worker_pid != os.getpid() or not _worker or not _worker.is_alive():
                _worker = threading.Thread(target=_run, name='qing-access-events', daemon=True)
                _worker.start()
                _worker_pid = os.getpid()
        return True
    except Exception:
        with _lock:
            _drops['worker_start_failed'] += 1
        _logger.error("Permission diagnostic worker could not be started")
        return False


def _drop(reason, count=1):
    with _lock:
        _drops[reason] += count
    # Keep one bounded worker alive to report the final aggregate even after
    # a burst ends; individual dropped requests do not write to the log.
    _ensure_worker()


def _report_drops():
    with _lock:
        changed = {key: count for key, count in _drops.items()
                   if count != _reported_drops[key]}
        _reported_drops.update(changed)
    if changed:
        _logger.warning("Permission diagnostic drops (process totals): %s", changed)


def _write(dbname, event, count=1):
    registry = Registry(dbname)
    with registry.cursor() as cr:
        # Concurrent workers update the same daily counter; repeatable-read
        # transactions would otherwise lose counts to serialization failures.
        cr.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        cr.execute("SET LOCAL statement_timeout = '500ms'")
        cr.execute("SET LOCAL lock_timeout = '200ms'")
        day = event.get('event_day') or datetime.now(timezone.utc).date().isoformat()
        identity = [day, event['event_type'], event['impact'], event['user_id'], event['company_id'],
                    event['allowed_companies'], event['active_companies'], event['model_name'],
                    event['operation'], event['cause'], event['entry']]
        key = hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()
        cr.execute("""
            INSERT INTO qing_access_event
                (event_key, event_day, event_type, impact, user_id, company_id,
                 allowed_companies_raw, active_companies_raw, model_name, operation, cause,
                 entry, first_seen, last_seen, occurrences)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    NOW() AT TIME ZONE 'UTC', NOW() AT TIME ZONE 'UTC', %s)
            ON CONFLICT (event_key) DO UPDATE
            SET occurrences = qing_access_event.occurrences + EXCLUDED.occurrences,
                last_seen = EXCLUDED.last_seen
        """, (key, day, event['event_type'], event['impact'], event['user_id'],
              event['company_id'], event['allowed_companies'], event['active_companies'],
              event['model_name'], event['operation'], event['cause'], event['entry'], count))
        cr.commit()


def _run():
    last_report = time.monotonic()
    while True:
        try:
            first = _events.get(timeout=10)
        except queue.Empty:
            _report_drops()
            last_report = time.monotonic()
            continue
        batch = [first]
        deadline = time.monotonic() + 0.05
        while len(batch) < 50:
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                break
            try:
                batch.append(_events.get(timeout=timeout))
            except queue.Empty:
                break
        groups = {}
        for dbname, event in batch:
            key = (dbname, tuple(sorted(event.items())))
            if key not in groups:
                groups[key] = [dbname, event, 0]
            groups[key][2] += 1
        for dbname, event, count in groups.values():
            try:
                _write(dbname, event, count)
            except Exception:
                _drop('write_failed', count)
        for _ in batch:
            _events.task_done()
        if time.monotonic() - last_report >= 10:
            _report_drops()
            last_report = time.monotonic()


def enqueue(dbname, event):
    """Never block or fail the request for a diagnostic."""
    try:
        _enqueue(dbname, event)
    except Exception:
        # Post-commit callbacks must never turn a committed operation into an error.
        try:
            _drop('enqueue_failed')
        except Exception:
            pass


def _enqueue(dbname, event):
    global _minute
    now_minute = int(time.monotonic() // 60)
    with _lock:
        if _minute != now_minute:
            _minute = now_minute
            _user_counts.clear()
        key = (dbname, event['user_id'])
        if key not in _user_counts and len(_user_counts) >= 10000:
            limited = True
        else:
            _user_counts[key] += 1
            limited = _user_counts[key] > _USER_EVENTS_PER_MINUTE
    if limited:
        _drop('rate_limited')
        return
    if not _ensure_worker():
        return
    event = dict(event, event_day=datetime.now(timezone.utc).date().isoformat())
    try:
        _events.put_nowait((dbname, event))
    except queue.Full:
        _drop('queue_full')


def contains_restricted(value, limit=50000):
    """Inspect only structure and the marker, never retain business values."""
    stack = [value]
    while stack and limit:
        item = stack.pop()
        limit -= 1
        if isinstance(item, dict):
            fields = item.get('__qing_restricted_fields')
            if isinstance(fields, list) and fields:
                return True
            stack.extend(value for value in item.values() if isinstance(value, (dict, list)))
        elif isinstance(item, list):
            stack.extend(value for value in item if isinstance(value, (dict, list)))
    if stack:
        _drop('response_scan_limit')
    return False
