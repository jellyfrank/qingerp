from odoo import models


class MailThread(models.AbstractModel):
    _inherit = 'mail.thread'

    def _web_push_get_partners_parameters(self, partner_ids):
        """Keep inbox/email delivery while disabling browser push in QingERP."""
        return self.env['mail.push.device'].sudo(), None, None

    def _web_push_send_notification(self, devices, private_key, public_key,
                                    payload_by_lang=None, payload=None,
                                    force_direct_send=False):
        """Also block direct callers holding an existing device subscription."""
        return
