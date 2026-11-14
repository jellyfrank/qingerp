import json
from datetime import timedelta

from odoo import api, fields, models


EVENT_TYPES = [('denied', 'Request Denied'), ('restricted', 'Restricted Read')]
EVENT_IMPACTS = [('none', 'None'), ('masked_readonly', 'Fields Masked and Read-only')]
EVENT_CAUSES = [('unknown', 'Unknown'), ('company_hint', 'Possible company restriction')]
EVENT_OPERATIONS = [('read', 'Read'), ('write', 'Write'), ('create', 'Create'),
                    ('unlink', 'Delete'), ('other', 'Other')]


class QingAccessEvent(models.Model):
    _name = 'qing.access.event'
    _description = 'Permission Experience Diagnostic'
    _order = 'last_seen desc, id desc'
    _log_access = False

    _event_key_unique = models.UniqueIndex('(event_key)')
    _date_index = models.Index('(event_day, id)')

    active_companies = fields.Char(compute='_compute_company_scopes')
    active_companies_raw = fields.Char(groups='base.group_no_one')
    allowed_companies = fields.Char(compute='_compute_company_scopes')
    allowed_companies_raw = fields.Char(groups='base.group_no_one')
    cause = fields.Selection(EVENT_CAUSES, readonly=True)
    company_id = fields.Many2one('res.company', readonly=True, ondelete='set null', index=True)
    entry = fields.Char(readonly=True)
    event_day = fields.Date(readonly=True)
    event_key = fields.Char(required=True, readonly=True)
    event_type = fields.Selection(EVENT_TYPES, readonly=True)
    first_seen = fields.Datetime(readonly=True)
    impact = fields.Selection(EVENT_IMPACTS, readonly=True)
    last_seen = fields.Datetime(readonly=True)
    model_name = fields.Char(readonly=True)
    occurrences = fields.Integer(readonly=True)
    operation = fields.Selection(EVENT_OPERATIONS, readonly=True)
    user_id = fields.Many2one('res.users', readonly=True, ondelete='set null')

    @api.depends_context('uid')
    def _compute_company_scopes(self):
        # Never return company identifiers outside this viewer's authorized scope.
        authorized = set(self.env.user.company_ids.ids)
        self.env.cr.execute(
            'SELECT id, allowed_companies_raw, active_companies_raw FROM qing_access_event WHERE id IN %s',
            (tuple(self.ids) or (0,),),
        )
        rows = {row[0]: row[1:] for row in self.env.cr.fetchall()}
        for record in self:
            allowed, active = rows.get(record.id, ('[]', '[]'))
            try:
                record.allowed_companies = ', '.join(str(item) for item in json.loads(allowed or '[]') if item in authorized)
                record.active_companies = ', '.join(str(item) for item in json.loads(active or '[]') if item in authorized)
            except (ValueError, TypeError):
                record.allowed_companies = record.active_companies = ''

    @api.model
    def _cron_purge(self):
        days = self.env['ir.config_parameter'].sudo().get_int('qing_base.access_event_retention_days', 30)
        days = max(1, min(days, 365))
        cutoff = fields.Date.today() - timedelta(days=days)
        cron = self.env['ir.cron']
        while True:
            self.env.cr.execute(
                """DELETE FROM qing_access_event WHERE id IN
                   (SELECT id FROM qing_access_event WHERE event_day < %s ORDER BY event_day, id LIMIT 1000)
                   RETURNING id""",
                (cutoff,),
            )
            removed = len(self.env.cr.fetchall())
            if not removed or not cron._commit_progress(processed=removed, remaining=int(removed == 1000)):
                break
