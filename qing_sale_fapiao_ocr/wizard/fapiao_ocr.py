import math

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError


class FapiaoOcr(models.TransientModel):
    _inherit = 'qing.fapiao.ocr'

    @api.model
    def _sale_defaults(self, order):
        order._check_upload_fapiao_access()
        return {
            'sale_order_id': order.id,
            'company_id': order.company_id.id,
            'partner_id': order.partner_invoice_id.commercial_partner_id.id,
            'currency_id': order.currency_id.id,
            'direction': 'out',
            'color': 'blue',
            'original_number': False,
        }

    def _check_sale_context(self):
        self.ensure_one()
        order = self.sale_order_id
        if not order:
            return
        expected = self._sale_defaults(order)
        for name, value in expected.items():
            actual = self[name].id if self._fields[name].type == 'many2one' else self[name]
            if actual != value:
                raise UserError(self.env._('The sales order context has changed. Open a new upload from the sales order.'))

    def _lock(self):
        super()._lock()
        self._check_sale_context()

    def _reopen(self):
        action = super()._reopen()
        if self.sale_order_id:
            view = self.env.ref('qing_sale_fapiao_ocr.view_sale_fapiao_upload_form')
            action.update(name=self.env._('Upload Issued Tax Invoice'), views=[(view.id, 'form')])
        return action

    @api.depends('sale_order_id', 'sale_order_id.invoice_ids.state')
    def _compute_available_sale_invoices(self):
        for wizard in self:
            order = wizard.sale_order_id
            wizard.available_sale_invoice_ids = order.invoice_ids.filtered(
                lambda move: move.state in ('draft', 'posted') and move.move_type == 'out_invoice'
                and move.company_id == order.company_id and move.currency_id == order.currency_id
                and move.commercial_partner_id == order.partner_invoice_id.commercial_partner_id
                and move.invoice_line_ids.sale_line_ids.order_id == order
            )

    def _validate_sale_invoice(self, invoice):
        self.ensure_one()
        invoice.ensure_one()
        invoice.check_access('write')
        order = self.sale_order_id
        if (invoice.state not in ('draft', 'posted') or invoice.move_type != 'out_invoice'
                or invoice.company_id != order.company_id or invoice.currency_id != order.currency_id
                or invoice.commercial_partner_id != order.partner_invoice_id.commercial_partner_id
                or invoice.invoice_line_ids.sale_line_ids.order_id != order):
            raise UserError(self.env._('Select a draft or posted customer invoice belonging to this sales order, company, customer and currency.'))

    def _validate_sale_amounts(self, invoice):
        self.ensure_one()
        currency = self.currency_id
        comparisons = (
            (self.env._('Untaxed Amount'), self.amount_untaxed, invoice.amount_untaxed),
            (self.env._('Tax'), self.amount_tax, invoice.amount_tax),
            (self.env._('Total'), self.invoice_total, invoice.amount_total),
        )
        differences = [self.env._('%(label)s: file %(file)s / invoice %(invoice)s',
                                label=label, file=currency.round(file_amount), invoice=currency.round(invoice_amount))
                       for label, file_amount, invoice_amount in comparisons
                       if currency.compare_amounts(file_amount, invoice_amount)]
        if differences:
            raise UserError(self.env._(
                'The uploaded tax invoice does not match the customer invoice:\n%s\n'
                'Review the recognized values or select the corresponding existing invoice. '
                'For partial invoicing, prepare the matching customer invoice first. No invoice was created.',
                '\n'.join(differences)))

    def _validate_sale_parties(self):
        self.ensure_one()
        pairs = (
            (self.buyer_tax_id, self.partner_id.vat),
            (self.seller_tax_id, self.company_id.vat),
        )
        for document_vat, partner_vat in pairs:
            if document_vat and partner_vat and ''.join(document_vat.split()).upper() != ''.join(partner_vat.split()).upper():
                raise UserError(self.env._('The buyer or seller tax ID differs from the sales customer or company. Review the document and master data.'))

    def _check_existing_sale_ticket(self):
        self.ensure_one()
        domain = [('company_id', '=', self.company_id.id), ('direction', '=', 'out'),
                  ('code', '=', (self.code or '').strip()), ('number', '=', (self.number or '').strip()),
                  ('state', '!=', 'cancelled')]
        if self.env['qing.fapiao'].search_count(domain):
            raise UserError(self.env._('This tax invoice number is already recorded. Open the existing tax invoice instead of creating another customer invoice.'))

    def _open_sale_invoice(self):
        self.ensure_one()
        invoice = self.sale_invoice_id
        if not invoice or invoice not in self.fapiao_id.move_ids:
            raise UserError(self.env._('The saved customer invoice link is missing. Review the tax invoice record.'))
        invoice.check_access('read')
        return self.sale_order_id.action_view_invoice(invoices=invoice)

    available_sale_invoice_ids = fields.Many2many('account.move', compute='_compute_available_sale_invoices')
    deduct_down_payments = fields.Boolean(string='Deduct Down Payments', default=True)
    sale_invoice_id = fields.Many2one('account.move', string='Existing Customer Invoice', check_company=True)
    sale_order_id = fields.Many2one('sale.order', string='Sales Order', readonly=True, check_company=True, ondelete='cascade')

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        order_id = values.get('sale_order_id') or self.env.context.get('default_sale_order_id')
        if order_id:
            order = self.env['sale.order'].browse(order_id).exists()
            defaults = self._sale_defaults(order)
            values.update({key: value for key, value in defaults.items() if key in field_names})
        return values

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for vals in vals_list:
            vals = dict(vals)
            order_id = vals.get('sale_order_id', self.env.context.get('default_sale_order_id'))
            if order_id:
                order = self.env['sale.order'].browse(order_id).exists()
                vals.update(self._sale_defaults(order))
            elif vals.get('sale_invoice_id') or self.env.context.get('default_sale_invoice_id'):
                raise UserError(self.env._('Open the upload from a sales order before selecting a customer invoice.'))
            prepared.append(vals)
        return super().create(prepared)

    def write(self, vals):
        for wizard in self:
            wizard.check_access('write')
            if 'sale_order_id' in vals and vals['sale_order_id'] != wizard.sale_order_id.id:
                raise AccessError(self.env._('The source sales order cannot be changed. Open a new upload.'))
            if wizard.sale_order_id:
                expected = wizard._sale_defaults(wizard.sale_order_id)
                if any(key in vals and vals[key] != value for key, value in expected.items()):
                    raise AccessError(self.env._('Company, customer, currency and tax invoice direction are fixed by the sales order.'))
            elif vals.get('sale_invoice_id'):
                raise UserError(self.env._('Open the upload from a sales order before selecting a customer invoice.'))
        return super().write(vals)

    def action_create_draft(self):
        self.ensure_one()
        if not self.sale_order_id:
            return super().action_create_draft()
        # Keep invoice generation, duplicate checks, tax draft and original file atomic.
        with self.env.cr.savepoint():
            self._lock()
            if self.fapiao_id:
                return self._open_sale_invoice()
            if not self.recognized or not self.reviewed:
                raise UserError(self.env._('Recognize the file and review its details before generating an invoice.'))
            if not all(math.isfinite(value) and value >= 0 for value in (
                    self.amount_untaxed, self.amount_tax, self.invoice_total)):
                raise UserError(self.env._('Invoice amounts must be finite and non-negative.'))
            self._validate_sale_parties()
            order = self.sale_order_id
            order._qing_lock_invoice_upload()
            self._check_existing_sale_ticket()
            invoice = self.sale_invoice_id
            if not invoice:
                # Reuse an unambiguous matching invoice even if the user left the selector empty.
                matching = self.available_sale_invoice_ids.filtered(
                    lambda move: not self.currency_id.compare_amounts(move.amount_untaxed, self.amount_untaxed)
                    and not self.currency_id.compare_amounts(move.amount_tax, self.amount_tax)
                    and not self.currency_id.compare_amounts(move.amount_total, self.invoice_total))
                if len(matching) > 1:
                    raise UserError(self.env._('Several customer invoices match this file. Select the intended existing invoice.'))
                invoice = matching
            created = not invoice
            if created:
                invoice = order._create_invoices(final=self.deduct_down_payments, grouped=True)
            self.invalidate_recordset(['available_sale_invoice_ids'])
            self._validate_sale_invoice(invoice)
            invoice._qing_lock_fapiao_moves()
            invoice._qing_touch_allocation_revision()
            self._validate_sale_amounts(invoice)
            domain = [('state', '!=', 'cancelled'), '|',
                      ('move_ids', 'in', invoice.ids), ('allocation_ids.move_id', 'in', invoice.ids)]
            if self.env['qing.fapiao'].search_count(domain):
                raise UserError(self.env._('This customer invoice already has an active tax invoice source. Review that record first.'))
            domain = [('move_id', '=', invoice.id), ('request_id.state', 'in', ['submitted', 'approved', 'done'])]
            if self.env['qing.fapiao.request.line'].search_count(domain):
                raise UserError(self.env._('This customer invoice has an invoice request. Complete or cancel that request before importing its tax invoice.'))
            if created:
                invoice.write({'invoice_date': self.invoice_date})
            self.write({'sale_invoice_id': invoice.id, 'reviewed': True})
            super().action_create_draft()
            self.fapiao_id.write({'move_ids': [Command.link(invoice.id)]})
            attachment = self.env['ir.attachment'].create({
                'name': self.source_filename,
                'raw': bytes(self.source_file),
                'res_model': 'account.move',
                'res_id': invoice.id,
            })
            invoice.message_post(
                body=self.env._('Issued tax invoice uploaded from the sales order. Tax invoice number: %s. Registration is pending.', self.number),
                attachment_ids=attachment.ids,
            )
            if not invoice.message_main_attachment_id:
                invoice.message_main_attachment_id = attachment
            return self._open_sale_invoice()
