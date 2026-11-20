from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.addons.qing_base.tools.channel_queue import locked_job_environment


OPERATIONS = {
    'ops_inbox_count': ('qing.channel.delivery', 'qing_channel.action_channel_delivery',
                        [('state', 'in', ('pending', 'retry'))]),
    'ops_action_count': ('qing.channel.action', 'qing_channel.action_channel_job',
                         [('state', 'in', ('pending', 'waiting', 'external'))]),
    'ops_review_count': ('qing.channel.exception', 'qing_channel.action_channel_exception', [('resolved', '=', False)]),
    'ops_uncertain_count': ('qing.channel.request', 'qing_channel.action_channel_request',
                            [('state', 'in', ('uncertain', 'verify'))]),
}
MANAGER = 'qing_channel.group_channel_manager'


class ChannelStoreOperations(models.Model):
    _inherit = 'qing.channel.store'

    ops_inbox_count = fields.Integer(string='Pending Deliveries', compute='_compute_operations', groups=MANAGER)
    ops_action_count = fields.Integer(string='Pending Actions', compute='_compute_operations', groups=MANAGER)
    ops_review_count = fields.Integer(string='Open Exceptions', compute='_compute_operations', groups=MANAGER)
    ops_uncertain_count = fields.Integer(string='Unconfirmed Requests', compute='_compute_operations', groups=MANAGER)
    ops_paused_count = fields.Integer(string='Paused Inventory Items', compute='_compute_operations', groups=MANAGER)
    ops_orders_through = fields.Datetime(string='Orders Reconciled Through', compute='_compute_operations', groups=MANAGER)
    ops_operator_id = fields.Many2one('res.users', string='Operations Contact', copy=False, groups=MANAGER)
    ops_alerted_exception_id = fields.Integer(copy=False, readonly=True, groups=MANAGER)

    @api.constrains('ops_operator_id', 'company_id')
    def _check_operations_contact(self):
        for store in self.filtered('ops_operator_id'):
            if not store._operations_contact_valid():
                raise ValidationError(_('Choose an active Channel Manager who can access the store’s company.'))

    def _operations_contact_valid(self):
        contact = self.ops_operator_id
        return (contact and contact.active and not contact.share and self.company_id in contact.company_ids
                and contact.has_group(MANAGER))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and (
            'default_ops_alerted_exception_id' in self.env.context
            or any('ops_alerted_exception_id' in vals for vals in vals_list)
        ):
            raise AccessError(_('The channel controls the last operations reminder.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and 'ops_alerted_exception_id' in vals:
            raise AccessError(_('The channel controls the last operations reminder.'))
        changed = self.browse()
        if 'ops_operator_id' in vals:
            changed = self.filtered(lambda store: store.ops_operator_id.id != vals['ops_operator_id'])
        result = super().write(vals)
        if changed:
            changed.sudo().ops_alerted_exception_id = 0
        return result

    @api.model
    def _cron_channel_operations_alerts(self):
        stores = self.sudo().search([('active', '=', True), ('ops_operator_id', '!=', False)])
        for store in stores:
            with locked_job_environment(self.env.registry, f'qing-channel-ops-alert:{store.id}') as env:
                if env is not None:
                    env[self._name].browse(store.id)._notify_operations_contact()
                    env.cr.commit()

    def _operations_due_checks(self):
        """Let a platform flag a condition that needs an operator's attention."""

    def _notify_operations_contact(self):
        self.ensure_one()
        self.lock_for_update(allow_referencing=True)
        if not self.active or not self._operations_contact_valid():
            return
        self._operations_due_checks()
        domain = [('store_id', '=', self.id), ('resolved', '=', False), ('id', '>', self.ops_alerted_exception_id)]
        count, last = self.env['qing.channel.exception']._read_group(domain, aggregates=['__count', 'id:max'])[0]
        if not count:
            return
        self.ops_operator_id._bus_send('simple_notification', {
            'title': _('Sales Channel Operations'),
            'message': _('New channel exceptions for %(store)s (%(count)s). Open Sales Channels → Operations to review them.',
                         store=self.display_name, count=count),
            'type': 'warning',
        })
        self.ops_alerted_exception_id = last

    def _compute_operations(self):
        scope = [('store_id', 'in', self.ids)]
        for field, (model, _action, domain) in OPERATIONS.items():
            grouped = dict(self.env[model]._read_group(scope + domain, ['store_id'], ['__count']))
            for store in self:
                store[field] = grouped.get(store, 0)
        stock = self._operations_paused_bindings().grouped('store_id')
        cursors = self.env['qing.channel.cursor'].search(scope + [('stream', '=', 'orders')])
        coverage = {cursor.store_id: cursor.through for cursor in cursors}
        for store in self:
            store.ops_paused_count = len(stock.get(store, ()))
            store.ops_orders_through = coverage.get(store, False)

    def _operations_paused_bindings(self):
        scope = [('store_id', 'in', self.ids), ('kind', '=', 'inventory')]
        bindings = self.env['qing.channel.binding'].search(
            scope + ['|', ('stock_paused', '=', True), ('stock_hold_reason', '!=', False)],
        )
        reviewed = self.env['qing.channel.action'].search([
            ('store_id', 'in', self.ids), ('kind', '=', 'stock'), ('state', '=', 'review'),
        ])
        ids = [action.data.get('binding') for action in reviewed if action.data and action.data.get('binding')]
        return bindings | self.env['qing.channel.binding'].search(scope + [('id', 'in', ids)])

    def _check_operations_access(self):
        self.ensure_one()
        self.check_access('read')
        if not self.env.user.has_group(MANAGER):
            raise AccessError(_('Channel Manager access is required.'))

    def _operation_records(self, metric):
        self._check_operations_access()
        _model, xmlid, domain = OPERATIONS[metric]
        action = self.env['ir.actions.act_window']._for_xml_id(xmlid)
        action['domain'] = [('store_id', '=', self.id), *domain]
        return action

    def action_operations_inbox(self):
        return self._operation_records('ops_inbox_count')

    def action_operations_jobs(self):
        return self._operation_records('ops_action_count')

    def action_operations_exceptions(self):
        return self._operation_records('ops_review_count')

    def action_operations_unconfirmed(self):
        return self._operation_records('ops_uncertain_count')

    def action_operations_paused_inventory(self):
        self._check_operations_access()
        action = self.env['ir.actions.act_window']._for_xml_id('qing_channel.action_channel_binding')
        action['domain'] = [('id', 'in', self._operations_paused_bindings().ids)]
        return action
