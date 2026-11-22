from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

RECHECK_MODELS = {
    'qing.channel.delivery', 'qing.channel.order', 'qing.channel.order.line',
    'qing.channel.action', 'qing.channel.request',
}


class ChannelException(models.Model):
    _name = 'qing.channel.exception'
    _description = 'Channel Exception'
    _check_company_auto = True

    @api.model
    def _flag(self, store, source, code):
        vals = {'store_id': store.id, 'object_model': source._name, 'object_id': source.id, 'code': code}
        self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))',
                            [f'qing-channel-review:{store.id}:{source._name}:{source.id}:{code}'])
        domain = [(key, '=', value) for key, value in vals.items()] + [('resolved', '=', False)]
        return self.search(domain, limit=1) or self.create(vals)

    @api.depends('code')
    @api.depends_context('lang')
    def _compute_display_name(self):
        for exception in self:
            exception.display_name = exception._get_exception_title()

    def _get_exception_title(self):
        return self.env._('Channel Exception')

    @api.depends('resolved')
    def _compute_status(self):
        for exception in self:
            exception.status = 'resolved' if exception.resolved else 'pending'

    @api.depends('resolved', 'object_model')
    def _compute_can_recheck(self):
        for exception in self:
            exception.can_recheck = not exception.resolved and exception._supports_recheck()

    def _supports_recheck(self):
        return self.object_model in RECHECK_MODELS

    can_recheck = fields.Boolean(compute='_compute_can_recheck')
    code = fields.Char(required=True)
    company_id = fields.Many2one(related='store_id.company_id', store=True, index=True, precompute=True)
    object_id = fields.Integer(required=True)
    object_model = fields.Char(required=True)
    resolved = fields.Boolean(default=False)
    status = fields.Selection([('pending', 'Pending'), ('resolved', 'Resolved')], compute='_compute_status')
    store_id = fields.Many2one('qing.channel.store', required=True, ondelete='restrict', index=True)

    _open_unique = models.UniqueIndex('(store_id, object_model, object_id, code) WHERE NOT resolved')

    def action_recheck(self):
        self.ensure_one()
        self.check_access('read')
        if not self.env.user.has_group('qing_channel.group_channel_manager'):
            raise AccessError(_('Channel Manager access is required.'))
        if self.resolved or self.object_model not in RECHECK_MODELS:
            raise UserError(_('This exception needs a configuration or manual correction.'))
        record = self.env[self.object_model].browse(self.object_id).exists()
        if not record:
            raise UserError(_('The referenced channel record no longer exists.'))
        record.check_access('read')
        if record._name == 'qing.channel.order.line':
            record = record.order_id
            record.check_access('read')
        record = record.sudo()
        if record.store_id != self.store_id:
            raise AccessError(_('The channel record is outside this shop.'))
        if record._name == 'qing.channel.delivery':
            if record.state not in ('review', 'retry'):
                raise UserError(_('This delivery is already being handled.'))
            record.write({'state': 'pending', 'retry_at': False, 'tries': 0})
        elif record._name == 'qing.channel.order':
            Delivery = self.env['qing.channel.delivery'].sudo()
            Delivery.create({'store_id': record.store_id.id, 'external_id': record.external_id,
                             'topic': 'orders/reconcile', 'identity': f'manual:{self.id}'})
        elif record._name == 'qing.channel.action':
            if record.state != 'review' or record.request_ids.filtered(lambda item: item.first_sent_at):
                raise UserError(_('Open the request exception to recheck an external operation.'))
            record.write({'state': 'pending', 'retry_at': False, 'tries': 0})
        elif record._name == 'qing.channel.request':
            if record.state != 'review':
                raise UserError(_('This request is already being handled.'))
            record._recheck_after_exception(self.code)
        self.sudo().resolved = True
        return True
