from odoo import api, models


class IapAutocompleteApi(models.AbstractModel):
    _inherit = 'iap.autocomplete.api'

    @api.model
    def _contact_iap(self, local_endpoint, action, params, timeout=15):
        """Keep customer and company search data off Odoo's autocomplete service."""
        return {}
