import base64
import binascii
from decimal import Decimal
import uuid

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.binary import BinaryBytes
from odoo.addons.qing_account_fapiao.models.fapiao_request import RESULT_FIELDS

ORDER_FIELDS = {'provider_order_ids', 'provider_order_id', 'provider_order_state', 'provider_registration_state'}


class FapiaoRequest(models.Model):
    _inherit = 'qing.fapiao.request'

    def _lock(self):
        # All paths, including native request actions, use order -> request locking.
        self.check_access('write')
        self.provider_order_ids._lock()
        return super()._lock()

    @api.depends('provider_order_ids')
    def _compute_provider_order(self):
        for request in self:
            request.provider_order_id = request.provider_order_ids[:1]

    def _provider_payload(self, request_key):
        self.ensure_one()
        if self.kind not in ('normal', 'special'):
            raise UserError(self.env._('Provider invoicing currently supports normal and special blue VAT invoices only.'))
        return {
            'schema_version': '1.0', 'request_key': request_key,
            'invoice_type': self.kind, 'color': 'blue', 'currency': self.currency_id.name,
            'buyer': {'name': self.buyer_name.strip(), 'tax_id': (self.buyer_tax_id or '').strip()},
            'seller': {'name': self.seller_name.strip(), 'tax_id': (self.seller_tax_id or '').strip()},
            'lines': [line._provider_line_payload() for line in self.line_ids],
            'amount_untaxed': format(self.amount_untaxed, '.%df' % self.currency_id.decimal_places),
            'amount_tax': format(self.amount_tax, '.%df' % self.currency_id.decimal_places),
            'amount_total': format(self.amount_total, '.%df' % self.currency_id.decimal_places),
        }

    def _validate_provider_order(self, order):
        self.ensure_one()
        order._check_provider_identity()
        if order != self.provider_order_id or order.qing_request_id != self:
            raise UserError(self.env._('The provider task does not belong to this invoice request.'))
        if (order.company_id != self.company_id or order.provider_id.company_id != self.company_id
                or order.provider_snapshot['seller_tax_id'] != (self.seller_tax_id or '').strip()):
            raise UserError(self.env._('The provider company and seller tax ID must match the approved request.'))
        if order.snapshot != self._provider_payload(order.request_key):
            raise UserError(self.env._('The approved request does not match the immutable provider snapshot.'))

    def _create_provider_order(self, provider):
        with self.env.cr.savepoint():
            self._check_scope()
            self._lock()
            if self.provider_order_id:
                if self.provider_order_id.provider_id != provider:
                    raise UserError(self.env._('This request is already bound to another provider. Open the existing task.'))
                return self.provider_order_id
            if self.state != 'approved':
                raise UserError(self.env._('Approve the invoice request before selecting an invoicing provider.'))
            if any(self[field] for field in RESULT_FIELDS):
                raise UserError(self.env._('This request already has manually entered results. Clear them before creating a provider task.'))
            provider.check_access('read')
            if provider.company_id != self.company_id or provider.seller_tax_id != (self.seller_tax_id or '').strip():
                raise UserError(self.env._('The provider company and seller tax ID must match the approved request.'))
            self._validate_submission()
            moves = self._lock_sources()
            moves._qing_validate_request_capacity()
            self._touch()
            payload = self._provider_payload(str(uuid.uuid4()))
            order = self.env['qing.fapiao.order']._create_order(provider, payload)
            order._write_internal({'qing_request_id': self.id})
            self.invalidate_recordset(['provider_order_ids', 'provider_order_id'])
            return order

    def _retry_provider_order(self, failed_order):
        self.ensure_one()
        self._check_scope()
        self._lock()
        failed_order.ensure_one()
        if failed_order.qing_request_id != self or failed_order != self.provider_order_id:
            raise UserError(self.env._('Only the latest provider task for this request can be retried.'))
        if failed_order.state != 'rejected':
            raise UserError(self.env._('Only a definitively rejected invoice task can be retried. Query tasks with an unknown result instead.'))
        if self.state != 'approved':
            raise UserError(self.env._('The invoice request must remain approved before retrying the provider task.'))
        provider = failed_order.provider_id
        provider.check_access('read')
        if provider.company_id != self.company_id or provider.seller_tax_id != (self.seller_tax_id or '').strip():
            raise UserError(self.env._('The provider company and seller tax ID must match the approved request.'))
        self._validate_submission()
        self._lock_sources()._qing_validate_request_capacity()
        payload = self._provider_payload(str(uuid.uuid4()))
        order = self.env['qing.fapiao.order']._create_order(provider, payload)
        order._write_internal({'qing_request_id': self.id})
        order._append_event('queued', {'retry_of_task_id': failed_order.id})
        self.invalidate_recordset(['provider_order_ids', 'provider_order_id'])
        return order

    def _get_registered_seller_name(self):
        self.ensure_one()
        order = self.provider_order_id
        if not order or order.state != 'issued':
            return super()._get_registered_seller_name()
        invoices = (order.result or {}).get('invoices', [])
        if len(invoices) != 1:
            return super()._get_registered_seller_name()
        return invoices[0]['seller']['name']

    def _record_provider_invoice(self, order):
        self._check_scope()
        self._lock()
        self._validate_provider_order(order)
        if order.state != 'issued':
            raise UserError(self.env._('Only an issued provider task can be recorded.'))
        allow_test = self.env['ir.config_parameter'].sudo().get_bool('qing_fapiao.allow_test_registration', False)
        if order.provider_snapshot['environment'] == 'test' and not allow_test:
            raise UserError(self.env._('Test provider invoices cannot be registered in the financial ledger. Enable test registration only in an isolated test database.'))
        if self.state == 'done':
            return self.action_open_invoice()
        if self.state != 'approved':
            raise UserError(self.env._('The invoice request must remain approved until the issued invoice is recorded.'))
        invoices = (order.result or {}).get('invoices', [])
        if len(invoices) != 1:
            raise UserError(self.env._('This workflow records exactly one issued invoice per request. Multiple invoices require a dedicated allocation review.'))
        invoice = invoices[0]
        if (invoice.get('buyer') != order.snapshot['buyer']
                or invoice.get('currency') != order.snapshot['currency']
                or invoice.get('seller', {}).get('tax_id') != order.snapshot['seller']['tax_id']):
            raise UserError(self.env._('The issued invoice buyer, seller tax ID and currency must match the approved request.'))
        try:
            amount = Decimal(invoice['amount_untaxed'])
            tax = Decimal(invoice['amount_tax'])
        except (KeyError, TypeError, ValueError, ArithmeticError) as error:
            raise UserError(self.env._('The issued invoice has invalid amounts.')) from error
        if (not amount.is_finite() or not tax.is_finite()
                or self.currency_id.compare_amounts(float(amount), self.amount_untaxed)
                or self.currency_id.compare_amounts(float(tax), self.amount_tax)):
            raise UserError(self.env._('The issued amount and tax must match the approved request.'))
        files = invoice.get('files', [])
        if not files or not files[0].get('data') or not files[0].get('name'):
            raise UserError(self.env._('The issued invoice must include its original file before registration.'))
        try:
            original_file = BinaryBytes(base64.b64decode(files[0]['data'], validate=True), filename=files[0]['name'])
        except (ValueError, TypeError, binascii.Error) as error:
            raise UserError(self.env._('The issued invoice original file has invalid encoding.')) from error
        # Trusted private path, not a forgeable context flag: reuse the original
        # manual validation and atomic reservation -> allocation transaction.
        values = {
            'result_number': invoice.get('number'), 'result_code': invoice.get('code'),
            'result_date': invoice.get('date'), 'result_amount_untaxed': float(amount),
            'result_amount_tax': float(tax), 'result_file': original_file,
            'result_filename': files[0]['name'], 'result_checked': True,
        }
        super(FapiaoRequest, self).write(values)
        return super(FapiaoRequest, self).action_record_invoice()

    provider_order_id = fields.Many2one('qing.fapiao.order', string='Provider Task', compute='_compute_provider_order')
    provider_order_ids = fields.One2many('qing.fapiao.order', 'qing_request_id', string='Provider Tasks', readonly=True)
    provider_order_state = fields.Selection(related='provider_order_id.state', string='Provider Status',
                                           depends=['provider_order_ids.state'])
    provider_registration_state = fields.Selection(related='provider_order_id.registration_state', string='Provider Registration',
                                                   depends=['provider_order_ids.registration_state'])

    @api.model_create_multi
    def create(self, vals_list):
        if any(ORDER_FIELDS & vals.keys() for vals in vals_list) or any('default_' + key in self.env.context for key in ORDER_FIELDS):
            raise AccessError(self.env._('Provider task links are managed by invoicing actions.'))
        return super().create(vals_list)

    def write(self, vals):
        if ORDER_FIELDS & vals.keys():
            raise AccessError(self.env._('Provider task links are managed by invoicing actions.'))
        self._lock()
        if RESULT_FIELDS & vals.keys() and self.provider_order_ids:
            raise UserError(self.env._('This request is handled by its provider task. Manual result changes are disabled.'))
        return super().write(vals)

    def action_reset_draft(self):
        with self.env.cr.savepoint():
            self._check_scope()
            self._lock()
            if self.provider_order_ids:
                raise UserError(self.env._('Requests linked to an invoicing task cannot be reset. Process the original task instead.'))
            return super().action_reset_draft()

    def action_cancel(self):
        with self.env.cr.savepoint():
            self._check_scope()
            self._lock()
            order = self.provider_order_id
            if order and order.state not in ('cancelled', 'rejected'):
                raise UserError(self.env._('Cancel the unsent provider task first. Pending, unknown or issued tasks keep their reservation and cannot be cancelled here.'))
            return super().action_cancel()

    def action_record_invoice(self):
        self._check_scope()
        self._lock()
        if self.provider_order_id:
            raise UserError(self.env._('Record this invoice from its provider task. An issued task must not be submitted for invoicing again.'))
        return super().action_record_invoice()

    def action_select_provider(self):
        self._check_scope()
        self._lock()
        if self.provider_order_id:
            return self.action_open_provider_order()
        if self.state != 'approved':
            raise UserError(self.env._('Approve the invoice request before selecting an invoicing provider.'))
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('Select Invoicing Provider'),
            'res_model': 'qing.fapiao.provider.select', 'view_mode': 'form', 'target': 'new',
            'context': {'default_request_id': self.id},
        }

    def action_open_provider_order(self):
        self.ensure_one()
        self.check_access('read')
        order = self.provider_order_id
        order.check_access('read')
        if not order:
            raise UserError(self.env._('This request has no provider task.'))
        return {'type': 'ir.actions.act_window', 'res_model': 'qing.fapiao.order',
                'res_id': order.id, 'view_mode': 'form', 'target': 'current'}
