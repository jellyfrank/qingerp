from datetime import timedelta

from odoo import api, fields, models

DEFAULT_RETENTION_DAYS = 90
RETENTION_PARAM = 'qing_base.login_log_retention_days'


class QingLoginLog(models.Model):
    _name = 'qing.login.log'
    _description = 'Sign-in Log'
    _order = 'attempt_datetime desc, id desc'

    @api.model
    def _cron_purge(self):
        days = self.env['ir.config_parameter'].sudo().get_int(RETENTION_PARAM, default=DEFAULT_RETENTION_DAYS)
        deadline = fields.Datetime.now() - timedelta(days=days if days > 0 else DEFAULT_RETENTION_DAYS)
        domain = [('attempt_datetime', '<', deadline)]
        self.sudo().search(domain, limit=10000).unlink()

    attempt_datetime = fields.Datetime(
        string='Time', default=fields.Datetime.now, required=True, readonly=True, index=True,
    )
    company_id = fields.Many2one('res.company', string='Company', readonly=True, index=True)
    failure_reason = fields.Char(readonly=True)
    ip_address = fields.Char(string='IP Address', required=True, readonly=True, index=True)
    login = fields.Char(required=True, readonly=True, index=True)
    success = fields.Boolean(readonly=True, index=True)
    user_id = fields.Many2one('res.users', string='User', readonly=True, index=True, ondelete='set null')
