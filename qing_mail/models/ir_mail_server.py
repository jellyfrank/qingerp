"""Company ownership and an authenticated sender policy for outgoing servers.

The field names match the earlier Solutions mommy_mail addon on purpose: a
database upgraded with mommy_mail still installed shares the same columns, so
no data migration is needed and commercial add-ons keep writing the same keys.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain

from odoo.addons.qing_base.tools import legacy_addon_loaded

SENDER_POLICIES = [
    ('native', 'Odoo Default'),
    ('authenticated', 'Authenticated Mailbox Only'),
]


class IrMailServer(models.Model):
    _inherit = 'ir.mail_server'

    @api.constrains('sender_policy', 'smtp_user', 'from_filter')
    def _check_authenticated_sender_configuration(self):
        for server in self:
            if server.sender_policy != 'authenticated':
                continue
            normalized_user = self._parse_from_filter(server.smtp_user or '')
            normalized_filter = self._parse_from_filter(server.from_filter or '')
            if not normalized_user or normalized_filter != normalized_user:
                raise ValidationError(self.env._(
                    'The authenticated sender policy requires an exact SMTP user in From Filtering.'
                ))

    def _check_forced_mail_server(self, mail_server, allow_archived, smtp_from):
        super()._check_forced_mail_server(mail_server, allow_archived, smtp_from)
        if mail_server.legacy_review_required and not self._qing_mail_legacy():
            raise UserError(self.env._(
                'This outgoing server requires a mail routing review before it can send.'
            ))

    def _find_mail_server_allowed_domain(self):
        domain = super()._find_mail_server_allowed_domain()
        if self._qing_mail_legacy():
            return domain
        company_domain = Domain('company_ids', '=', False) | Domain('company_ids', 'in', [self.env.company.id])
        return domain & company_domain & Domain('legacy_review_required', '=', False)

    @api.model
    def _qing_mail_legacy(self):
        """mommy_mail applies the same rules itself when it is still installed."""
        return legacy_addon_loaded(self.env, 'mommy_mail')

    company_ids = fields.Many2many(
        'res.company',
        string='Allowed Companies',
        help='Leave empty for a server shared by all companies. Assigned servers send only for their companies.',
    )
    legacy_review_required = fields.Boolean(
        string='Review Mail Routing',
        copy=False,
        help='The server is paused until an administrator reviews its sender and company routing.',
    )
    sender_policy = fields.Selection(
        SENDER_POLICIES,
        default='native',
        required=True,
        string='Sender Policy',
        help='Authenticated Mailbox Only requires the sender to match the SMTP login address.',
    )
