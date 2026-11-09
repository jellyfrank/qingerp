from datetime import timedelta

from odoo import api, fields, models


AUDIT_OPERATIONS = [
    ('create', 'Created'),
    ('write', 'Updated'),
    ('unlink', 'Deleted'),
]
AUDIT_SOURCES = [
    ('http', 'Web or API'),
    ('server', 'Server'),
]
PURGE_BATCH_SIZE = 1000


class QingAuditLog(models.Model):
    _name = 'qing.audit.log'
    _description = 'Qing ORM Audit Log'
    _order = 'event_date desc, id desc'
    _log_access = False

    _event_date_idx = models.Index('(event_date, id)')
    _model_record_date_idx = models.Index('(model_name, record_id, event_date)')
    _user_date_idx = models.Index('(user_id, event_date)')

    company_id = fields.Many2one('res.company', readonly=True, ondelete='set null')
    changes = fields.Text(readonly=True)
    event_date = fields.Datetime(required=True, readonly=True)
    field_names = fields.Char(readonly=True)
    is_sudo = fields.Boolean(readonly=True)
    model_name = fields.Char(required=True, readonly=True)
    operation = fields.Selection(AUDIT_OPERATIONS, required=True, readonly=True)
    record_id = fields.Integer(required=True, readonly=True)
    session_user_id = fields.Many2one('res.users', readonly=True, ondelete='set null')
    source = fields.Selection(AUDIT_SOURCES, required=True, readonly=True)
    user_id = fields.Many2one('res.users', readonly=True, ondelete='set null')

    @api.model
    def _purge_batch(self, cutoff, limit=PURGE_BATCH_SIZE):
        """Remove one bounded batch while keeping the oldest rows indexed."""
        self.env.cr.execute(
            """
            DELETE FROM qing_audit_log
            WHERE id IN (
                SELECT id FROM qing_audit_log
                WHERE event_date < %s
                ORDER BY event_date, id
                LIMIT %s
            )
            RETURNING id
            """,
            (cutoff, limit),
        )
        return len(self.env.cr.fetchall())

    @api.model
    def _cron_purge(self):
        parameters = self.env['ir.config_parameter'].sudo()
        days = parameters.get_int('qing_base.audit_retention_days', 7)
        days = max(1, min(days, 365))
        cutoff = fields.Datetime.now() - timedelta(days=days)
        cron = self.env['ir.cron']
        while True:
            removed = self._purge_batch(cutoff)
            if not removed:
                break
            if not cron._commit_progress(processed=removed, remaining=int(removed == PURGE_BATCH_SIZE)):
                break
