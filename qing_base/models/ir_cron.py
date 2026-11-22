from odoo import models
from odoo.exceptions import UserError

from ..tools.telemetry import HEARTBEAT_CRON_XMLID, MANUAL_HEARTBEAT


class IrCron(models.Model):
    _inherit = 'ir.cron'

    def method_direct_trigger(self):
        heartbeat = self.env.ref(HEARTBEAT_CRON_XMLID, raise_if_not_found=False)
        if self != heartbeat:
            return super().method_direct_trigger()
        self.check_access('write')
        if not self.active:
            raise UserError(self.env._("Enable the heartbeat check before running it manually."))

        # Odoo runs the job in this thread using a new cursor and a fresh ORM context.
        token = MANUAL_HEARTBEAT.set(True)
        try:
            return super().method_direct_trigger()
        finally:
            MANUAL_HEARTBEAT.reset(token)
