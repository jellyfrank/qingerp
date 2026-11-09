from odoo import fields, models


class WhatsappAttempt(models.Model):
    _name = 'qing.whatsapp.attempt'

    _description = 'WhatsApp Delivery Attempt'

    _order = 'id desc'

    company_id = fields.Many2one(related='message_id.company_id', store=True, index=True)

    error_code = fields.Char()

    finished_at = fields.Datetime()

    message_id = fields.Many2one('qing.whatsapp.message', required=True, ondelete='restrict', index=True)

    outcome = fields.Char(default='sending')

    started_at = fields.Datetime(default=fields.Datetime.now)
