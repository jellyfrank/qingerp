from odoo import models, _
from odoo.exceptions import UserError
from odoo.addons.qing_sms.tools import plain_number


class SmsScenarioHandler(models.AbstractModel):
    _inherit = 'qing.sms.scenario.handler'

    def _get_handlers(self):
        handlers = super()._get_handlers()
        return dict(handlers, **{'stock.picking.delivered': 'qing.sms.handler.stock.delivered'})


class SmsStockDeliveredHandler(models.AbstractModel):
    _name = 'qing.sms.handler.stock.delivered'
    _description = 'Stock delivery SMS handler'
    _record_model = 'stock.picking'

    def _is_applicable(self, record):
        if record.state != 'done' or record.picking_type_id.code != 'outgoing':
            return False, 'NOT_OUTGOING_DONE'
        if record.return_id:
            return False, 'RETURN_PICKING'
        if not any(move.quantity for move in record.move_ids if move.state == 'done'):
            return False, 'NO_SHIPPED_QUANTITY'
        return True, False

    def _get_recipient(self, record, scenario):
        partner = record.partner_id
        if not partner or not partner.phone:
            raise UserError(_('Delivery contact has no mobile number.'))
        return {'partner_id': partner.id, 'number': partner.phone}

    def _prepare_values(self, record, scenario):
        partner = record.partner_id
        return {'consignee': partner.name, 'phone': plain_number(partner.phone),
                'picking': record.name}

    def _select_variant(self, record, scenario):
        return 'default'

    def _check_before_send(self, record, event):
        applicable, reason = self._is_applicable(record)
        if not applicable:
            raise UserError(_('Delivery is no longer eligible for SMS: %s', reason))
