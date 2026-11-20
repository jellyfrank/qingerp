from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class ChannelRequestAttempt(models.Model):
    _name = 'qing.channel.request.attempt'
    _description = 'Channel Outbound Attempt'
    _order = 'id desc'

    company_id = fields.Many2one(related='request_id.company_id', store=True, precompute=True, index=True)
    request_id = fields.Many2one('qing.channel.request', required=True, ondelete='restrict', index=True)
    result = fields.Char(required=True)

    def write(self, vals):
        raise AccessError(_('Outbound attempt records are append-only.'))

    @api.ondelete(at_uninstall=False)
    def _unlink_except_module_uninstall(self):
        raise AccessError(_('Outbound attempt records are append-only.'))
