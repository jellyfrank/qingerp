from uuid import uuid4

from odoo import fields, models, _
from odoo.exceptions import AccessError, UserError


class SmsScenarioTest(models.TransientModel):
    _name = 'qing.sms.scenario.test'
    _description = 'Preview and test a domestic SMS scenario'

    scenario_id = fields.Many2one('qing.sms.scenario', required=True)
    sample_model = fields.Char(related='scenario_id.model_id.model')
    sample_res_id = fields.Many2oneReference(model_field='sample_model', required=True)
    number = fields.Char(required=True)
    preview_number = fields.Char(readonly=True)
    preview_body = fields.Text(readonly=True)
    test_key = fields.Char(default=lambda self: uuid4().hex, readonly=True)

    def _prepare(self):
        self.ensure_one()
        if not self.env.user.has_group('qing_sms.group_sms_manager') or not self.env.user.has_group('qing_sms.group_sms_user'):
            raise AccessError(_('Scenario test sends require both SMS manager and sender permissions.'))
        scenario = self.scenario_id
        if scenario.company_id not in self.env.companies:
            raise AccessError(_('The scenario belongs to another company.'))
        record = self.env[scenario.model_id.model].with_company(scenario.company_id).browse(self.sample_res_id).exists()
        if not record:
            raise UserError(_('Select an existing sample business record.'))
        record.check_access('read')
        name = self.env['qing.sms.scenario.handler']._get_handlers().get(scenario.event_code)
        if not name:
            raise UserError(_('The scenario handler is unavailable.'))
        handler = self.env[name]
        applicable, reason = handler._is_applicable(record)
        if not applicable:
            raise UserError(_('The sample record is not eligible: %s', reason))
        values = handler._prepare_values(record, scenario)
        variant = handler._select_variant(record, scenario)
        binding = scenario.binding_ids.filtered(lambda row: row.variant_code == variant and row.accept_new)[:1]
        if not binding:
            raise UserError(_('No active binding exists for this scenario variant.'))
        keys = {item.key for item in binding.variable_ids if item.source == 'input'}
        if not keys <= values.keys():
            raise UserError(_('Captured business values do not cover the template mapping.'))
        parameters = {key: values[key] for key in keys}
        number, cloud_values, body = binding._prepare_payload(self.number, parameters, record.id)
        return binding, record, parameters, number, body

    def action_preview(self):
        binding, record, parameters, number, body = self._prepare()
        self.write({'preview_number': number, 'preview_body': body})
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'view_mode': 'form', 'target': 'new'}

    def action_send_test(self):
        binding, record, parameters, number, body = self._prepare()
        if number != self.preview_number or body != self.preview_body:
            raise UserError(_('Preview the current number and content before sending.'))
        message = binding.send_sms(number, parameters,
            event_key=f'manual.test:{self.test_key}', record_id=record.id)
        return {'type': 'ir.actions.act_window', 'res_model': 'qing.sms.message',
                'res_id': message.id, 'view_mode': 'form', 'target': 'current'}
