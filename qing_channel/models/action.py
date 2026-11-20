import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.addons.qing_base.tools.channel_queue import due_channel_jobs, locked_job_environment
from odoo.fields import Domain

from ..errors import ChannelReviewRequired, DeferredChannelError, retry_delay


_logger = logging.getLogger(__name__)
STATES = [
    ('pending', 'Pending'), ('waiting', 'Waiting for Dependency'), ('external', 'Waiting for Shopify'),
    ('done', 'Done'), ('review', 'Needs Review'), ('obsolete', 'Superseded'),
]


class ChannelAction(models.Model):
    _name = 'qing.channel.action'
    _description = 'Channel Business Action'
    _check_company_auto = True

    @api.model
    def _ensure_action(self, store, key, kind, **values):
        self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))',
                            [f'qing-action:{store.id}:{key}'])
        domain = [('store_id', '=', store.id), ('key', '=', key)]
        return self.search(domain, limit=1) or self.create(dict(values, store_id=store.id, key=key, kind=kind))

    @api.model
    def _cron_process(self, limit=25):
        due = [('state', 'in', ('pending', 'waiting')),
               '|', ('retry_at', '=', False), ('retry_at', '<=', fields.Datetime.now())]
        stores = self.env['qing.channel.store']._channel_worker_domain()
        due = Domain(due) & Domain('store_id', 'any', stores)
        for item in due_channel_jobs(self.sudo(), due, Domain('expedited', '=', True), limit):
            key = f'qing-action-order:{item.store_id.id}:{item.order_id.id or item.id}'
            with locked_job_environment(self.env.registry, key) as env:
                if env is not None:
                    env[self._name].browse(item.id)._process_one()

    def _process_one(self):
        self = self.with_company(self.company_id)
        if self.state not in ('pending', 'waiting') or not self.store_id._channel_worker_ready():
            return
        try:
            with self.env.cr.savepoint():
                if self.snapshot_version and self.order_id and self.snapshot_version != self.order_id.snapshot_version:
                    self.write({'state': 'obsolete', 'retry_at': False})
                elif self.dependency_ids.filtered(lambda job: job.state in ('review', 'obsolete')):
                    raise ChannelReviewRequired('dependency_requires_review')
                elif self.dependency_ids.filtered(lambda job: job.state != 'done'):
                    self.write({'state': 'waiting', 'retry_at': fields.Datetime.now() + timedelta(minutes=1)})
                else:
                    state = self.store_id._execute_channel_action(self) or 'done'
                    self.write({'state': state, 'retry_at': False})
        except ChannelReviewRequired as exc:
            if self.store_id._channel_operation_failed(exc.code):
                self._review(exc.code)
        except DeferredChannelError as exc:
            if self.store_id._channel_operation_failed(exc.code):
                self._retry(exc.code, exc.retry_after)
        except Exception:
            _logger.error('Channel action %s failed', self.id)
            self._retry('action_error')
        self.env.cr.commit()

    def _retry(self, code, retry_after=None):
        if retry_after is None:
            self.tries += 1
        self.retry_at = fields.Datetime.now() + retry_delay(self.tries, retry_after)
        if self.tries >= 8:
            self._review(code)

    def _review(self, code):
        self.state = 'review'
        self.env['qing.channel.exception']._flag(self.store_id, self, code)

    company_id = fields.Many2one(related='store_id.company_id', store=True, index=True, precompute=True)
    data = fields.Json(groups='qing_channel.group_channel_manager')
    dependency_ids = fields.Many2many('qing.channel.action', 'qing_channel_action_dependency_rel',
                                     'action_id', 'dependency_id', check_company=True)
    expedited = fields.Boolean(default=False, index=True)
    key = fields.Char(required=True)
    kind = fields.Char(required=True)
    order_id = fields.Many2one('qing.channel.order', ondelete='restrict', check_company=True, index=True)
    request_ids = fields.One2many('qing.channel.request', 'action_id')
    retry_at = fields.Datetime(index=True)
    snapshot_version = fields.Integer()
    state = fields.Selection(STATES, required=True, default='pending', index=True)
    store_id = fields.Many2one('qing.channel.store', required=True, ondelete='restrict', index=True)
    tries = fields.Integer(default=0)

    _key_unique = models.UniqueIndex('(store_id, key)')
