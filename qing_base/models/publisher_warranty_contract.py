from odoo import api, models


class PublisherWarrantyContract(models.AbstractModel):
    _inherit = 'publisher_warranty.contract'

    @api.model
    def _get_sys_logs(self):
        """Do not send database metadata to Odoo's publisher service."""
        return {'messages': []}
