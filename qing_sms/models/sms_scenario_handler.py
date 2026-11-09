from odoo import models


class SmsScenarioHandler(models.AbstractModel):
    _name = 'qing.sms.scenario.handler'
    _description = 'Registered SMS scenario handlers'

    def _get_handlers(self):
        """Return code-owned event mappings; user records never name Python methods."""
        return {}
