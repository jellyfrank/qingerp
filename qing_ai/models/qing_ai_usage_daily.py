"""Per-user daily counters of provider calls made by the Qing AI assistant."""

from odoo import api, fields, models
from odoo.exceptions import UserError


DEFAULT_DAILY_LIMIT = 100
MAX_DAILY_LIMIT = 10000


class QingAiUsageDaily(models.Model):
    _name = 'qing.ai.usage.daily'
    _description = 'Qing AI Daily Usage'
    _order = 'day desc, request_count desc, id desc'

    _user_day_unique = models.Constraint(
        'UNIQUE (user_id, day)',
        'Only one daily AI usage row may exist per user and day.',
    )

    @api.model
    def _daily_limit(self):
        """Return the configured number of provider calls allowed per user and day."""
        value = self.env['ir.config_parameter'].sudo().get_int(
            'qing_ai.daily_request_limit', DEFAULT_DAILY_LIMIT
        )
        return min(max(value, 1), MAX_DAILY_LIMIT)

    @api.model
    def _today(self):
        """Use the user's calendar day instead of the UTC day."""
        return fields.Date.context_today(self)

    @api.model
    def _calls_today(self):
        domain = [('user_id', '=', self.env.uid), ('day', '=', self._today())]
        return self.sudo().search(domain, limit=1).request_count

    @api.model
    def _limit_error(self):
        return UserError(self.env._('Your daily AI request limit has been reached.'))

    @api.model
    def _check_available(self):
        """Fail fast before storing a new request when no call is left today."""
        if self._calls_today() >= self._daily_limit():
            raise self._limit_error()

    @api.model
    def _consume_call(self):
        """
        Atomically reserve one provider call for the current user and day.

        The reservation is rolled back when it would exceed the limit, so concurrent
        requests from the same user cannot overspend the quota.

        :return: the usage row that was charged
        """
        limit = self._daily_limit()
        day = self._today()
        with self.env.cr.savepoint():
            self.env.cr.execute("""
                INSERT INTO qing_ai_usage_daily
                    (user_id, day, request_count, input_tokens, output_tokens,
                     create_uid, write_uid, create_date, write_date)
                VALUES (%(uid)s, %(day)s, 1, 0, 0, %(uid)s, %(uid)s,
                        now() at time zone 'UTC', now() at time zone 'UTC')
                ON CONFLICT (user_id, day) DO UPDATE
                   SET request_count = qing_ai_usage_daily.request_count + 1,
                       write_uid = EXCLUDED.write_uid,
                       write_date = EXCLUDED.write_date
                RETURNING id, request_count
            """, {'uid': self.env.uid, 'day': day})
            usage_id, count = self.env.cr.fetchone()
            if count > limit:
                raise self._limit_error()
        self.invalidate_model(['request_count', 'write_date', 'write_uid'])
        return self.sudo().browse(usage_id)

    def _record_tokens(self, usage):
        """Add exact provider token counters; missing counters are not estimated."""
        self.ensure_one()
        summary = self.env['qing.ai.service']._usage_summary(usage)
        input_tokens = int(summary.get('input_tokens') or 0)
        output_tokens = int(summary.get('output_tokens') or 0)
        if not input_tokens and not output_tokens:
            return
        self.env.cr.execute("""
            UPDATE qing_ai_usage_daily
               SET input_tokens = input_tokens + %s, output_tokens = output_tokens + %s
             WHERE id = %s
        """, [input_tokens, output_tokens, self.id])
        self.invalidate_recordset(['input_tokens', 'output_tokens'])

    day = fields.Date(required=True, readonly=True, index=True)
    input_tokens = fields.Integer(string='Input Tokens', readonly=True)
    output_tokens = fields.Integer(string='Output Tokens', readonly=True)
    request_count = fields.Integer(string='Model Calls', readonly=True)
    user_id = fields.Many2one('res.users', string='User', required=True, readonly=True,
                              index=True, ondelete='cascade')
