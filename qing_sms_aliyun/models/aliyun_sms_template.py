from odoo import fields, models, _
from odoo.exceptions import UserError


class AliyunSmsTemplate(models.Model):
    _name = 'qing.sms.aliyun.template'
    _description = 'Aliyun Sms Template'

    def _get_binding(self, company):
        self.ensure_one()
        self.check_access('read')
        config = company.sudo().aliyun_sms_config_id
        accounts = self.env['qing.sms.account'].sudo().search([
            ('company_id', '=', company.id), ('legacy_config_id', '=', config.id)])
        domain = [('company_id', '=', company.id), ('account_id', 'in', accounts.ids),
                  ('remote_template_id.remote_id', '=', self.code)]
        bindings = self.env['qing.sms.binding'].search(domain)
        if len(bindings) != 1:
            raise UserError(_('请先迁移旧配置、同步云端模板并核验唯一的业务绑定。'))
        return bindings

    code = fields.Char('Template Code', required=True)
    name = fields.Char('Name', required=True)

    def send_sms(self, tel, data, event_key=None):
        """Legacy signature retained. Returns QUEUED; acceptance is asynchronous."""
        binding = self._get_binding(self.env.company)
        message = binding.send_sms(tel, data, event_key=event_key)
        return {'Code': 'QUEUED', 'Message': 'Queued for asynchronous delivery', 'TaskId': message.id}
