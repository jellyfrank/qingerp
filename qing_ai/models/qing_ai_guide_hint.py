from odoo import api, fields, models


class QingAiGuideHint(models.Model):
    _name = 'qing.ai.guide.hint'
    _description = 'Daily limit for QingERP guide upgrade hints'
    _log_access = False

    _user_topic_day_unique = models.Constraint('UNIQUE(user_id, topic, day)', 'The hint is already recorded.')

    @api.model
    def _claim(self, topic):
        self.env.cr.execute("""
            INSERT INTO qing_ai_guide_hint (user_id, topic, day)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, topic, day) DO NOTHING
            RETURNING id
        """, [self.env.uid, topic, self.env['qing.ai.usage.daily']._today()])
        return bool(self.env.cr.fetchone())

    @api.autovacuum
    def _purge_old_hints(self):
        domain = [('day', '<', fields.Date.subtract(fields.Date.today(), days=2))]
        self.sudo().search(domain, limit=10000).unlink()

    day = fields.Date(required=True, index=True)
    topic = fields.Char(required=True)
    user_id = fields.Many2one('res.users', required=True, ondelete='cascade')
