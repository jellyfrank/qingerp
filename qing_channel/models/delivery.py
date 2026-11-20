import logging

from psycopg2.errors import UniqueViolation

from odoo.addons.qing_base.tools.channel_queue import locked_job_environment
from ..errors import ChannelReviewRequired, DeferredChannelError, retry_delay

from odoo import fields, models, api
from odoo.fields import Domain

_logger = logging.getLogger(__name__)
STATES = [('pending', 'Pending'), ('retry', 'Retry'), ('done', 'Done'), ('review', 'Needs Review')]


class ChannelDelivery(models.Model):
    _name = 'qing.channel.delivery'
    _description = 'Channel Inbox Delivery'
    _check_company_auto = True

    @api.model
    def _receive(self, store, identity, external_id, topic, event_id=None, occurred_at=None):
        self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))',
                            [f'qing-channel-delivery:{store.id}:{identity}'])
        domain = [('store_id', '=', store.id), ('identity', '=', identity)]
        delivery = self.search(domain, limit=1)
        if delivery:
            return delivery
        try:
            with self.env.cr.savepoint():
                return self.create({
                    'store_id': store.id,
                    'identity': identity,
                    'external_id': external_id,
                    'event_id': event_id,
                    'occurred_at': occurred_at,
                    'topic': topic,
                })
        except UniqueViolation as exc:
            if exc.diag.constraint_name != 'qing_channel_delivery_identity_unique':
                raise
            # Another delivery committed after this transaction's snapshot.
            return self.browse()

    @api.model
    def _cron_process(self, limit=20):
        due = [('state', 'in', ['pending', 'retry']), '|', ('retry_at', '=', False),
               ('retry_at', '<=', fields.Datetime.now())]
        stores = self.env['qing.channel.store']._channel_worker_domain()
        for item in self.sudo().search(Domain(due) & Domain('store_id', 'any', stores), limit=limit, order='id'):
            with locked_job_environment(self.env.registry, f'qing-channel-inbox:{item.store_id.id}:{item.external_id}') as env:
                if env is not None:
                    env[self._name].browse(item.id)._process_one()

    def _process_one(self):
        self = self.with_company(self.company_id)
        if self.state not in ('pending', 'retry') or not self.store_id._channel_worker_ready():
            return
        try:
            with self.env.cr.savepoint():
                self.store_id._import_delivery(self)
                self.state = 'done'
        except ChannelReviewRequired as exc:
            if self.store_id._channel_operation_failed(exc.code):
                self.state = 'review'
                self.env['qing.channel.exception']._flag(self.store_id, self, exc.code)
        except DeferredChannelError as exc:
            if self.store_id._channel_operation_failed(exc.code):
                self._retry(exc.code, exc.retry_after)
        except Exception:
            _logger.error('Channel inbox item %s failed', self.id)
            self._retry('inbox_error')
        self.env.cr.commit()

    def _retry(self, code, retry_after=None):
        if retry_after is None:
            self.tries += 1
        self.state = 'review' if self.tries >= 8 else 'retry'
        self.retry_at = fields.Datetime.now() + retry_delay(self.tries, retry_after)
        if self.state == 'review':
            self.env['qing.channel.exception']._flag(self.store_id, self, code)

    company_id = fields.Many2one(related='store_id.company_id', store=True, index=True, precompute=True)
    event_id = fields.Char()
    external_id = fields.Char(required=True)
    identity = fields.Char(required=True)
    occurred_at = fields.Datetime()
    received_at = fields.Datetime(required=True, default=fields.Datetime.now)
    retry_at = fields.Datetime(index=True)
    state = fields.Selection(STATES, required=True, default='pending', index=True)
    store_id = fields.Many2one('qing.channel.store', required=True, ondelete='restrict', index=True)
    topic = fields.Char(required=True)
    tries = fields.Integer(default=0)

    _identity_unique = models.UniqueIndex('(store_id, identity)')
