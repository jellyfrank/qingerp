import ast
import json

from odoo import fields, models, _
from odoo.exceptions import UserError

PROVIDERS = [('default', 'Default'), ('aliyun', 'Aliyun SMS')]


class SmsSms(models.Model):
    _inherit = 'sms.sms'

    def _aliyun_parameters(self):
        self.ensure_one()
        try:
            data = json.loads(self.body or '{}')
        except (ValueError, TypeError):
            try:
                data = ast.literal_eval(self.body or '{}')
            except (ValueError, SyntaxError, TypeError):
                raise UserError(_('旧短信参数必须为 JSON 对象。')) from None
        if not isinstance(data, dict):
            raise UserError(_('短信参数必须为对象。'))
        return data

    def _send(self, unlink_sent=True, raise_exception=False):
        legacy = self.filtered(lambda sms: sms.service_provider == 'aliyun' and not sms.domestic_message_id)
        native = self - legacy
        if native:
            super(SmsSms, native)._send(unlink_sent=unlink_sent, raise_exception=raise_exception)
        for sms in legacy.filtered(lambda sms: sms.state == 'outgoing' and not sms.to_delete):
            sms.check_access('write')
            try:
                with self.env.cr.savepoint():
                    binding = sms.aliyun_template._get_binding(sms.aliyun_company_id)
                    number, values, body = binding._prepare_payload(sms.number, sms._aliyun_parameters())
                    message = self.env['qing.sms.message']._enqueue(binding, number, values, body,
                                                                    'legacy:' + sms.uuid, sms=sms)
                    sms.write({'aliyun_response': json.dumps({'Code': 'QUEUED', 'TaskId': message.id}),
                               'aliyun_error_msg': False})
            except UserError:
                if raise_exception:
                    raise
                sms.write({'aliyun_error_msg': 'TEMPLATE_REVIEW_REQUIRED'})
                sms._update_sms_state_and_trackers('error', failure_type='sms_server')

    def _domestic_result_hook(self, result):
        super()._domestic_result_hook(result)
        for sms in self:
            if sms.domestic_message_id.account_id.provider == 'aliyun':
                sms.write({'aliyun_biz_id': sms.domestic_message_id.provider_id,
                           'aliyun_request_id': sms.domestic_message_id.request_id,
                           'aliyun_error_msg': result.get('code') if result['state'] in ('failed', 'unknown') else False,
                           'aliyun_response': json.dumps({'Code': result.get('code'), 'State': result['state']})})

    aliyun_biz_id = fields.Char(readonly=True, copy=False)
    aliyun_company_id = fields.Many2one('res.company', default=lambda self: self.env.company, index=True)
    aliyun_error_msg = fields.Char(readonly=True, copy=False)
    aliyun_request_id = fields.Char(readonly=True, copy=False)
    aliyun_response = fields.Text(readonly=True, copy=False, groups='base.group_system')
    aliyun_template = fields.Many2one('qing.sms.aliyun.template')
    service_provider = fields.Selection(PROVIDERS, default='default')
