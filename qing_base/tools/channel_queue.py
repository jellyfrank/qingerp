import base64
import hashlib
import hmac
import json
from contextlib import contextmanager

from odoo import SUPERUSER_ID, api
from odoo.fields import Domain


def snapshot_digest(value):
    """Fingerprint normalised input without depending on JSON key order."""
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def verify_base64_hmac_sha256(payload, signature, secret):
    if not signature or not secret or not signature.isascii():
        return False
    mac = hmac.digest(secret.encode(), payload, 'sha256')
    return hmac.compare_digest(base64.b64encode(mac), signature.encode())


def due_channel_jobs(model, due, expedited_domain, limit):
    """Reserve up to half a batch for timely work, then fill it in FIFO order."""
    if not limit or limit < 2:
        return model.search(due, limit=limit, order='id')
    due = Domain(due)
    expedited = model.search(due & expedited_domain, limit=limit // 2, order='id')
    remaining = limit - len(expedited)
    if not remaining:
        return expedited
    return expedited + model.search(due & Domain('id', 'not in', expedited.ids), limit=remaining, order='id')


@contextmanager
def locked_job_environment(registry, key):
    """Keep an exclusive worker lock across durable intent and response commits."""
    with registry.cursor() as cr:
        cr.execute('SELECT pg_try_advisory_lock(hashtextextended(%s, 0))', [key])
        locked, = cr.fetchone()
        if not locked:
            yield None
            return
        try:
            yield api.Environment(cr, SUPERUSER_ID, {})
        finally:
            cr.rollback()
            cr.execute('SELECT pg_advisory_unlock(hashtextextended(%s, 0))', [key])
            cr.commit()
