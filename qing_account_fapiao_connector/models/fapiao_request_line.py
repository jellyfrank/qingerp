from decimal import Decimal

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

SNAPSHOT_FIELDS = {'provider_tax_snapshot', 'provider_tax_rate_display'}


class FapiaoRequestLine(models.Model):
    _inherit = 'qing.fapiao.request.line'

    @api.model
    def _source_provider_tax_snapshot(self, source):
        source.check_access('read')
        taxes = source.tax_ids
        if len(taxes) != 1:
            return {'supported': False}
        tax = taxes
        repartition = tax.invoice_repartition_line_ids
        supported = (
            tax.company_id == source.company_id and tax.amount_type == 'percent'
            and tax.type_tax_use == 'sale' and 0 <= tax.amount <= 100
            and not tax.include_base_amount and tax.tax_exigibility == 'on_invoice'
            and not source.extra_tax_data
            and len(repartition) == 2
            and set(repartition.mapped('repartition_type')) == {'base', 'tax'}
            and all(line.factor_percent == 100 for line in repartition)
        )
        return {'supported': supported, 'tax_id': tax.id,
                'tax_rate': format(Decimal(str(tax.amount)) / 100, 'f'),
                'price_include': tax._is_price_included(document_tax_mode=source.document_tax_mode)}

    @api.depends('provider_tax_snapshot')
    def _compute_provider_tax_rate_display(self):
        for line in self:
            snapshot = line.provider_tax_snapshot or {}
            line.provider_tax_rate_display = (
                '%s%%' % (Decimal(snapshot['tax_rate']) * 100)
                if snapshot.get('supported') else False
            )

    def _provider_line_payload(self):
        self.ensure_one()
        snapshot = self.provider_tax_snapshot or {}
        if not snapshot:
            raise UserError(self.env._('This request predates structured tax snapshots. Keep using manual recording, or recreate and approve a new request for provider invoicing.'))
        if not snapshot.get('supported'):
            raise UserError(self.env._('Provider invoicing currently supports one standard percentage sales tax per line. Use manual recording for other tax configurations.'))
        currency = self.currency_id
        expected_tax = currency.round(float(Decimal(str(self.amount_untaxed)) * Decimal(snapshot['tax_rate'])))
        if currency.compare_amounts(self.amount_tax, expected_tax):
            raise UserError(self.env._('The requested line amount and tax do not match its approved tax rate. Correct the request before provider invoicing.'))
        return {
            'line_key': str(self.id), 'description': self.description,
            'quantity': format(Decimal(str(self.quantity)), 'f'),
            'amount_untaxed': format(self.amount_untaxed, '.%df' % currency.decimal_places),
            'amount_tax': format(self.amount_tax, '.%df' % currency.decimal_places),
            'tax_rate': snapshot['tax_rate'], 'price_include': snapshot['price_include'],
            'tax_code': self.tax_code,
        }

    provider_tax_rate_display = fields.Char(string='Provider Tax Rate', compute='_compute_provider_tax_rate_display')
    provider_tax_snapshot = fields.Json(string='Structured Source Tax', readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        if any(SNAPSHOT_FIELDS & vals.keys() for vals in vals_list) or any('default_' + key in self.env.context for key in SNAPSHOT_FIELDS):
            raise AccessError(self.env._('Structured tax snapshots are captured from the source invoice and cannot be supplied directly.'))
        prepared = []
        source_ids = {vals.get('source_line_id') or self.env.context.get('default_source_line_id') for vals in vals_list}
        source_model = self.env['account.move.line']
        sources = source_model.browse(source_ids - {False, None})
        source_by_id = {source.id: source for source in sources}
        for vals in vals_list:
            source_id = vals.get('source_line_id') or self.env.context.get('default_source_line_id')
            source = source_by_id.get(source_id, source_model)
            prepared.append(dict(vals, provider_tax_snapshot=self._source_provider_tax_snapshot(source)))
        return super().create(prepared)

    def write(self, vals):
        if SNAPSHOT_FIELDS & vals.keys():
            raise AccessError(self.env._('Structured tax snapshots are captured from the source invoice and cannot be supplied directly.'))
        if 'source_line_id' in vals:
            source = self.env['account.move.line'].browse(vals['source_line_id'])
            vals = dict(vals, provider_tax_snapshot=self._source_provider_tax_snapshot(source))
        return super().write(vals)
