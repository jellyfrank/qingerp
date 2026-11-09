"""Apply outgoing server company ownership and sender policy when sending."""
from odoo import models, tools
from odoo.exceptions import UserError


class MailMail(models.Model):
    _inherit = 'mail.mail'

    def _qing_server_allows(self, server):
        self.ensure_one()
        company = self.mail_message_id.record_company_id
        return (
            not server.legacy_review_required
            and (not server.company_ids or company in server.company_ids)
            and (
                server.sender_policy != 'authenticated'
                or tools.email_normalize(self.email_from) == tools.email_normalize(server.smtp_user)
            )
        )

    def _split_by_mail_configuration(self):
        # Native routing skips the candidate filter for an explicitly chosen
        # server, so validate that choice against the business record first.
        if not self.env['ir.mail_server']._qing_mail_legacy():
            for mail in self.filtered('mail_server_id'):
                if not mail._qing_server_allows(mail.mail_server_id):
                    raise UserError(self.env._(
                        'The selected outgoing server is paused, belongs to another company, '
                        'or requires its authenticated mailbox as sender.'
                    ))
        yield from super()._split_by_mail_configuration()

    def _filter_mail_mail_servers(self, mail_servers):
        candidates = super()._filter_mail_mail_servers(mail_servers)
        if self.env['ir.mail_server']._qing_mail_legacy():
            return candidates
        for mail in self:
            candidates = candidates.filtered(mail._qing_server_allows)
        return candidates
