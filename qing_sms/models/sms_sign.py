from odoo import api, fields, models, _
from odoo.exceptions import AccessError
from .constants import AUDIT_STATES, fingerprint


class SmsSign(models.Model):
    _name = 'qing.sms.sign'
    _description = '云端短信签名'
    _check_company_auto = True

    def _fingerprint(self):
        self.ensure_one()
        return fingerprint([self.remote_id, self.name, self.audit_state, self.active])

    account_id = fields.Many2one('qing.sms.account', string='短信账户', required=True, ondelete='restrict', index=True)
    active = fields.Boolean(string='启用', default=True)
    audit_state = fields.Selection(AUDIT_STATES, default='unknown', required=True, string='Audit Status')
    company_id = fields.Many2one(string='公司', related='account_id.company_id', store=True, index=True)
    last_sync_at = fields.Datetime(readonly=True)
    name = fields.Char(string='签名', required=True)
    raw_status = fields.Char(readonly=True)
    reject_reason = fields.Text(string='驳回原因', readonly=True)
    remote_id = fields.Char(string='云端标识', required=True)

    _remote_unique = models.Constraint('unique(account_id, remote_id)', '同一账户不能重复登记签名。')

    @api.model
    def _create_synced(self, values):
        return super(SmsSign, self).create(values)

    def _write_synced(self, values):
        return super().write(values)

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if any(key in values for key in ['audit_state', 'raw_status', 'reject_reason', 'last_sync_at']):
                raise AccessError(_('云端审核信息只能通过同步写入。'))
        return super().create(vals_list)

    def write(self, values):
        if any(key in values for key in ['name', 'account_id', 'remote_id', 'audit_state', 'raw_status', 'reject_reason', 'last_sync_at']):
            raise AccessError(_('云端审核信息只能通过同步写入。'))
        return super().write(values)
