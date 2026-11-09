from datetime import timedelta

from odoo import fields, models


class ResCurrency(models.Model):
    _inherit = 'res.currency'

    def _get_rates(self, company, date):
        # Odoo 20 uses a strict `< date` cutoff. Currency rates are Date values,
        # so the next day's exclusive cutoff includes today without future rates.
        # Keep native company priority, fallback, cache and invalidation behavior.
        # Re-audit this adapter if upstream changes its cutoff to inclusive.
        cutoff = fields.Date.to_date(date) + timedelta(days=1)
        return super()._get_rates(company, cutoff)
