import json

from odoo import models

from ..tools.presentation import style_dashboard


class SpreadsheetDashboard(models.Model):
    _inherit = 'spreadsheet.dashboard'

    def _get_serialized_readonly_dashboard(self):
        data = json.loads(super()._get_serialized_readonly_dashboard())
        data['snapshot'] = style_dashboard(data['snapshot'])
        return json.dumps(data)

    def _get_sample_dashboard(self):
        snapshot = super()._get_sample_dashboard()
        return style_dashboard(snapshot) if snapshot else snapshot
