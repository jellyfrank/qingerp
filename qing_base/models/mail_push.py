from odoo import api, models


class MailPush(models.Model):
    _inherit = 'mail.push'

    @api.model
    def _push_notification_to_endpoint(self, batch_size=50):
        """Do not send legacy queued browser notifications or delete them."""
        return
