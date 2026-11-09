from odoo import api, models


class MailPushDevice(models.Model):
    _inherit = 'mail.push.device'

    @api.model
    def register_devices(self, **kw):
        """Ignore registration from clients still using cached native assets."""
        return
