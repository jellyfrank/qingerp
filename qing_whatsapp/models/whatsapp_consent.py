from odoo import fields, models, _
from odoo.exceptions import AccessError

CONSENT_STATES = [('granted', 'Granted'), ('revoked', 'Revoked')]
CONSENT_PURPOSES = [('marketing', 'Marketing'), ('service', 'Service notifications')]


class WhatsappConsent(models.Model):
    _name = 'qing.whatsapp.consent'

    _description = 'WhatsApp Consent Evidence'

    _order = 'id desc'

    account_id = fields.Many2one(related='conversation_id.account_id', store=True)

    company_id = fields.Many2one(related='conversation_id.company_id', store=True, index=True)

    conversation_id = fields.Many2one('qing.whatsapp.conversation', required=True, ondelete='restrict')

    evidence = fields.Char(required=True)

    purpose = fields.Selection(CONSENT_PURPOSES, required=True, default='marketing')

    state = fields.Selection(CONSENT_STATES, required=True)

    def write(self, vals):
        raise AccessError(_('Consent evidence is append-only. Record a new decision.'))

    def unlink(self):
        raise AccessError(_('Consent evidence cannot be deleted.'))
