from odoo import fields, models

from ..services.common import EVENT_STATES


class ScanEvent(models.Model):
    _name = 'qing.stock.scan.event'
    _inherit = 'qing.stock.scan.guard'
    _description = 'Warehouse Scan Event'
    _order = 'id desc'

    _request_unique = models.Constraint('UNIQUE(session_id, request_key)', 'This request has already been recorded.')

    company_id = fields.Many2one(related='session_id.company_id', store=True, index=True)
    device = fields.Char(readonly=True)
    message = fields.Char(readonly=True)
    payload = fields.Json(readonly=True)
    request_hash = fields.Char(required=True, readonly=True)
    request_key = fields.Char(required=True, readonly=True, index=True)
    result = fields.Json(readonly=True)
    session_id = fields.Many2one('qing.stock.scan.session', required=True, readonly=True, index=True, ondelete='restrict')
    state = fields.Selection(EVENT_STATES, required=True, readonly=True)
    undone = fields.Boolean(readonly=True)
    user_id = fields.Many2one('res.users', required=True, readonly=True)
