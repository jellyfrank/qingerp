from odoo import models

from ..tools.telemetry import HEARTBEAT_CRON_XMLID


class IrActionsActWindow(models.Model):
    _inherit = 'ir.actions.act_window'

    def _get_action_dict(self):
        action = super()._get_action_dict()
        if action['res_model'] != 'ir.cron':
            return action
        if self != self.env.ref('base.ir_cron_act', raise_if_not_found=False):
            return action
        heartbeat = self.env.ref(HEARTBEAT_CRON_XMLID, raise_if_not_found=False)
        if heartbeat:
            domain = action['domain'] or '[]'
            action['domain'] = f"({domain}) + [('id', '!=', {heartbeat.id})]"
        return action
