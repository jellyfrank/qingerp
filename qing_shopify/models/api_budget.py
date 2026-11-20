from contextlib import contextmanager
from math import ceil, isfinite
from time import time

from odoo import SUPERUSER_ID, api, fields, models
from odoo.addons.qing_channel.errors import DeferredChannelError


MAX_QUERY_COST = 1000
INITIAL_RESTORE_RATE = 100
UNMEASURED_QUERY_COST = 80


class ShopifyApiBudget(models.Model):
    _name = 'qing.shopify.api.budget'
    _description = 'Shopify API Budget'
    _log_access = False

    store_key = fields.Integer(required=True)
    available = fields.Float(default=MAX_QUERY_COST)
    capacity = fields.Float(default=MAX_QUERY_COST)
    restore_rate = fields.Float(default=INITIAL_RESTORE_RATE)
    measured_at = fields.Float()
    blocked_until = fields.Float()
    requested_costs = fields.Json(default=dict)

    _store_unique = models.UniqueIndex('(store_key)')

    def _qing_audit_enabled(self):
        return False

    @api.model
    @contextmanager
    def _locked_budget(self, store_id):
        """Commit capacity separately so rollbacks and concurrent workers share the same budget."""
        with self.env.registry.cursor() as cr:
            cr.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
            cr.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', [f'qing-shopify-budget:{store_id}'])
            env = api.Environment(cr, SUPERUSER_ID, {})
            model = env[self._name]
            bucket = model.search([('store_key', '=', store_id)], limit=1) or model.create({'store_key': store_id})
            yield bucket
            cr.commit()

    def _available_at(self, now):
        return min(self.capacity, self.available + max(0, now - self.measured_at) * self.restore_rate)

    @api.model
    def _reserve(self, store_id, key):
        with self._locked_budget(store_id) as bucket:
            now = time()
            if bucket.blocked_until > now:
                raise DeferredChannelError('shopify_throttled', retry_after=ceil(bucket.blocked_until - now))
            wanted = (bucket.requested_costs or {}).get(key, min(bucket.capacity, UNMEASURED_QUERY_COST))
            available = bucket._available_at(now)
            if wanted > available:
                delay = ceil((wanted - available) / bucket.restore_rate)
                raise DeferredChannelError('shopify_throttled', retry_after=max(1, delay))
            bucket.write({'available': available - wanted, 'measured_at': now})
        return wanted

    @api.model
    def _observe(self, store_id, key, reserved, response):
        extensions = response.get('extensions')
        costs = extensions.get('cost') if isinstance(extensions, dict) else None
        throttle = costs.get('throttleStatus') if isinstance(costs, dict) else None
        if not isinstance(throttle, dict):
            return None
        values = [throttle.get(field) for field in ('maximumAvailable', 'currentlyAvailable', 'restoreRate')]
        if not all(isinstance(value, (float, int)) and isfinite(value) for value in values):
            return None
        capacity, available, rate = values
        if capacity <= 0 or rate <= 0 or not 0 <= available <= capacity:
            return None
        requested = costs.get('requestedQueryCost')
        if not isinstance(requested, (int, float)) or not 0 < requested <= MAX_QUERY_COST:
            requested = None
        actual = costs.get('actualQueryCost')
        if not isinstance(actual, (int, float)) or not 0 <= actual <= MAX_QUERY_COST:
            actual = 0 if not response.get('data') else reserved
        with self._locked_budget(store_id) as bucket:
            now = time()
            estimate = dict(bucket.requested_costs or {})
            if requested is not None:
                estimate[key] = requested
            # A delayed response can reduce our allowance. It cannot restore tokens reserved by another worker.
            local = bucket._available_at(now) + reserved - actual
            bucket.write({'available': max(0, min(available, local)), 'capacity': capacity, 'restore_rate': rate,
                          'measured_at': now, 'requested_costs': estimate})
        wait = (requested or reserved) - available
        return max(1, ceil(wait / rate))

    @api.model
    def _pause(self, store_id, seconds):
        with self._locked_budget(store_id) as bucket:
            now = time()
            bucket.write({'blocked_until': max(bucket.blocked_until, now + (seconds or 2)),
                          'available': 0, 'measured_at': now})
