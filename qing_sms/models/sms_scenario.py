from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError


class SmsScenario(models.Model):
    _name = 'qing.sms.scenario'
    _description = 'Domestic SMS scenario'
    _check_company_auto = True

    def _check_manager(self):
        self.check_access('write')
        if not self.env.user.has_group('qing_sms.group_sms_manager'):
            raise AccessError(_('Only SMS managers can configure scenarios.'))

    def _check_ready(self):
        self.ensure_one()
        if self.event_code not in self.env['qing.sms.scenario.handler']._get_handlers():
            raise UserError(_('The scenario handler is unavailable.'))
        sender = self.sender_user_id
        if not sender or not sender.active or self.company_id not in sender.company_ids:
            raise UserError(_('Select an active sender with access to this company.'))
        if not sender.has_group('qing_sms.group_sms_user'):
            raise UserError(_('The sender needs SMS send permission.'))
        if not self.env[self.model_id.model].with_user(sender).has_access('read'):
            raise UserError(_('The sender needs read access to the business model.'))
        binding = self.binding_ids.filtered(lambda item: item.accept_new and item.variant_code == 'default')[:1]
        if not binding:
            raise UserError(_('Select a reviewed default template binding.'))
        binding.with_user(sender).with_company(self.company_id)._check_usable()

    active = fields.Boolean(default=True)
    binding_ids = fields.One2many('qing.sms.binding', 'scenario_id')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    config_revision = fields.Integer(default=1, readonly=True)
    enabled = fields.Boolean(default=False, readonly=True)
    enabled_from = fields.Datetime(readonly=True)
    event_code = fields.Char(required=True)
    last_check_code = fields.Char(readonly=True)
    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade')
    name = fields.Char(required=True)
    recipient_policy = fields.Selection([('record_contact', 'Record contact')], default='record_contact', required=True)
    sender_user_id = fields.Many2one('res.users', ondelete='restrict')

    _company_event_unique = models.Constraint('unique(company_id, event_code)', 'A company can configure each SMS event once.')

    @api.constrains('event_code', 'model_id')
    def _check_handler_model(self):
        handlers = self.env['qing.sms.scenario.handler']._get_handlers()
        for scenario in self:
            name = handlers.get(scenario.event_code)
            if not name or self.env[name]._record_model != scenario.model_id.model:
                raise ValidationError(_('The event and business model do not match a registered handler.'))

    def action_enable(self):
        self._check_manager()
        for scenario in self:
            scenario._check_ready()
            super(SmsScenario, scenario).write({'enabled': True, 'enabled_from': fields.Datetime.now(),
                                                'last_check_code': 'READY', 'config_revision': scenario.config_revision + 1})
        return True

    def action_disable(self):
        self._check_manager()
        for scenario in self:
            super(SmsScenario, scenario).write({'enabled': False, 'config_revision': scenario.config_revision + 1})
        return True

    def action_open_test_send(self):
        self.ensure_one()
        self._check_manager()
        return {'type': 'ir.actions.act_window', 'res_model': 'qing.sms.scenario.test',
                'view_mode': 'form', 'target': 'new',
                'context': {'default_scenario_id': self.id}}

    def action_set_binding(self, binding):
        """Switch versions without deactivating old queued intent or changing its fingerprint."""
        self.ensure_one()
        self._check_manager()
        binding.ensure_one()
        self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))',
                            [f'sms-scenario:{self.company_id.id}:{self.event_code}'])
        if binding.company_id != self.company_id or binding.template_id.model != self.model_id.model:
            raise UserError(_('The binding belongs to another company or model.'))
        binding._check_usable()
        old = self.binding_ids.filtered(lambda row: row.variant_code == binding.variant_code and row.id != binding.id)
        for row in old:
            row._write_internal({'scenario_id': False, 'accept_new': False, 'retired_at': fields.Datetime.now()})
        old.flush_recordset(['scenario_id'])
        binding._write_internal({'scenario_id': self.id, 'accept_new': True, 'retired_at': False})
        super().write({'config_revision': self.config_revision + 1})
        return True

    def write(self, vals):
        if 'enabled' in vals or 'enabled_from' in vals or 'config_revision' in vals or 'last_check_code' in vals:
            raise AccessError(_('Use scenario actions to change activation.'))
        result = super().write(vals)
        if vals:
            for scenario in self:
                super(SmsScenario, scenario).write({'config_revision': scenario.config_revision + 1})
        return result

    @api.model_create_multi
    def create(self, vals_list):
        protected = {'enabled', 'enabled_from', 'config_revision', 'last_check_code'}
        if any(protected & vals.keys() for vals in vals_list):
            raise AccessError(_('Use scenario actions to change activation.'))
        return super().create(vals_list)
