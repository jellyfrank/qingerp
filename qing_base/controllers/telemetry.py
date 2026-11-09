from odoo import http
from odoo.http import request


class QingTelemetryController(http.Controller):
    @http.route('/qing/telemetry/enqueue', type='jsonrpc', auth='user', methods=['POST'])
    def enqueue_heartbeat(self):
        if not request.env.user.has_group('base.group_system'):
            return {'queued': False}
        parameters = request.env['ir.config_parameter'].sudo()
        return {'queued': parameters._qing_telemetry_enqueue_if_due()}
