import math
import re

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class FapiaoRequestLine(models.Model):
    _name = 'qing.fapiao.request.line'
    _description = 'Tax Invoice Request Line'
    _check_company_auto = True
    _order = 'id'

    def _lock_draft(self):
        self.check_access('write')
        self.request_id._lock()
        if any(request.state != 'draft' for request in self.request_id):
            raise UserError(self.env._('Request lines are editable only in draft.'))
        self.request_id._touch()

    def _valid_tax_code(self):
        self.ensure_one()
        return bool(re.fullmatch(r'[0-9]{19}', (self.tax_code or '').strip()))

    @api.model
    def _classification_snapshot(self, classification):
        classification.check_access('read')
        return {
            'tax_classification_id': classification.id or False,
            'tax_code': classification.code or False,
            'tax_classification_name': classification.name or False,
        }

    @api.model
    def _source_classification_snapshot(self, source):
        source.check_access('read')
        template = source.product_id.product_tmpl_id
        classification = (template._qing_get_fapiao_tax_classification()
                          if template else self.env['qing.fapiao.tax.classification'])
        return self._classification_snapshot(classification)

    @api.constrains('source_line_id', 'request_id', 'quantity', 'amount_untaxed', 'amount_tax')
    def _check_values(self):
        for line in self:
            source, request = line.source_line_id, line.request_id
            source.check_access('read')
            if (source.move_id.state != 'posted' or source.move_id.move_type != 'out_invoice'
                    or source.display_type != 'product' or source.company_id != request.company_id
                    or source.currency_id != request.currency_id
                    or source.move_id.commercial_partner_id != request.partner_id.commercial_partner_id):
                raise UserError(self.env._('Use posted customer invoice lines from the same company, currency and customer.'))
            amounts = (line.amount_untaxed, line.amount_tax)
            if (not math.isfinite(line.quantity) or line.quantity <= 0 or source.quantity <= 0
                    or source.price_subtotal <= 0 or source.price_total < source.price_subtotal
                    or not all(math.isfinite(v) and v >= 0 and abs(v - request.currency_id.round(v)) < 1e-8 for v in amounts)
                    or request.currency_id.is_zero(sum(amounts))):
                raise ValidationError(self.env._('Use positive quantities and nonnegative amounts at currency precision; negative or mixed-sign lines require separate handling.'))
            if (line.quantity > source.quantity + 1e-8
                    or request.currency_id.compare_amounts(line.amount_untaxed, source.price_subtotal) > 0
                    or request.currency_id.compare_amounts(line.amount_tax, source.price_total - source.price_subtotal) > 0):
                raise UserError(self.env._('The requested quantity, amount or tax exceeds the source line.'))

    @api.onchange('source_line_id')
    def _onchange_source(self):
        if self.source_line_id:
            self.description = self.source_line_id.name
            self.quantity = self.source_line_id.quantity
            self.amount_untaxed = self.source_line_id.price_subtotal
            self.amount_tax = self.source_line_id.price_total - self.source_line_id.price_subtotal
            self.tax_description = ', '.join(self.source_line_id.tax_ids.mapped('name'))
            self.update(self._source_classification_snapshot(self.source_line_id))

    @api.onchange('tax_classification_id')
    def _onchange_tax_classification(self):
        for line in self:
            line.update(self._classification_snapshot(line.tax_classification_id))

    amount_tax = fields.Monetary(string='Requested Tax', required=True, default=0)
    amount_untaxed = fields.Monetary(string='Requested Amount', required=True, default=0)
    company_id = fields.Many2one(related='request_id.company_id', store=True)
    currency_id = fields.Many2one(related='request_id.currency_id')
    description = fields.Char(string='Invoice Item', required=True)
    move_id = fields.Many2one(string='Source Invoice', related='source_line_id.move_id', store=True, index=True)
    quantity = fields.Float(string='Requested Quantity', required=True, default=1)
    request_id = fields.Many2one('qing.fapiao.request', string='Request', required=True, ondelete='cascade', check_company=True, index=True)
    source_line_id = fields.Many2one('account.move.line', string='Source Invoice Line', required=True, ondelete='restrict', check_company=True, index=True)
    tax_classification_id = fields.Many2one(
        'qing.fapiao.tax.classification', string='Tax Classification', ondelete='restrict',
        domain="[('invoicing_allowed', '=', True)]")
    tax_classification_name = fields.Char(string='Tax Classification Name', readonly=True)
    tax_code = fields.Char(string='Tax Classification Code')
    tax_description = fields.Char(string='Source Taxes', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        derived = {'move_id', 'company_id', 'currency_id', 'tax_description', 'tax_classification_name'}
        if any(derived & vals.keys() for vals in vals_list) or any('default_' + key in self.env.context for key in derived):
            raise AccessError(self.env._('Request line ownership and source snapshots cannot be changed directly.'))
        parents = self.env['qing.fapiao.request'].browse({v.get('request_id') or self.env.context.get('default_request_id') for v in vals_list} - {None, False})
        parents._lock()
        if any(p.state != 'draft' for p in parents):
            raise UserError(self.env._('Request lines are editable only in draft.'))
        parents._touch()
        prepared = []
        for vals in vals_list:
            self.env['qing.fapiao']._validate_input_amounts(vals)
            vals = dict(vals)
            source = self.env['account.move.line'].browse(vals.get('source_line_id') or self.env.context.get('default_source_line_id'))
            source.check_access('read')
            if 'tax_classification_id' in vals:
                classification = self.env['qing.fapiao.tax.classification'].browse(vals['tax_classification_id'])
                vals.update(self._classification_snapshot(classification))
            elif 'tax_code' not in vals:
                vals.update(self._source_classification_snapshot(source))
            else:
                vals.update(tax_classification_id=False, tax_classification_name=False,
                            tax_code=(vals.get('tax_code') or '').strip())
            vals['tax_description'] = ', '.join(source.tax_ids.mapped('name'))
            prepared.append(vals)
        return super().create(prepared)

    def write(self, vals):
        if {'request_id', 'move_id', 'company_id', 'currency_id', 'tax_description', 'tax_classification_name'} & vals.keys():
            raise AccessError(self.env._('Request line ownership and source snapshots cannot be changed directly.'))
        self._lock_draft()
        self.env['qing.fapiao']._validate_input_amounts(vals)
        vals = dict(vals)
        if 'tax_classification_id' in vals:
            classification = self.env['qing.fapiao.tax.classification'].browse(vals['tax_classification_id'])
            vals.update(self._classification_snapshot(classification))
        elif 'source_line_id' in vals:
            source = self.env['account.move.line'].browse(vals['source_line_id'])
            source.check_access('read')
            vals.update(self._source_classification_snapshot(source))
            vals['tax_description'] = ', '.join(source.tax_ids.mapped('name'))
        elif 'tax_code' in vals:
            vals.update(tax_classification_id=False, tax_classification_name=False,
                        tax_code=(vals.get('tax_code') or '').strip())
        return super().write(vals)

    def unlink(self):
        self._lock_draft()
        return super().unlink()
