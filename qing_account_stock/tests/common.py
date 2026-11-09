from unittest import SkipTest

from freezegun import freeze_time

from odoo import Command, fields
from odoo.tests import TransactionCase


class ReturnValuationCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if cls.env['ir.module.module'].search_count([('name', '=', 'l10n_cn'), ('state', '=', 'installed')]) == 0:
            raise SkipTest('CNF fixture requires l10n_cn installed in the test database')
        company = cls.env['res.company'].with_context(chart_template_load=True).create({
            'name': 'Return valuation test', 'country_id': cls.env.ref('base.cn').id,
            'currency_id': cls.env.ref('base.CNY').id,
        })
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=company.ids))
        cls.company = company.with_env(cls.env)
        cls.env['account.chart.template'].try_loading('cn', cls.company, install_demo=False)
        cls.company.write({'inventory_valuation': 'real_time', 'cost_method': 'average'})
        cls.env['stock.warehouse'].create({'name': 'Return test', 'code': 'RVT', 'company_id': company.id})
        cls.vendor = cls.env['res.partner'].create({'name': 'Return test vendor'})
        cls.product = cls.env['product.product'].create({
            'name': 'Return test product', 'type': 'consu', 'is_storable': True,
            'standard_price': 100, 'company_id': company.id, 'purchase_method': 'receive',
            'supplier_taxes_id': [Command.clear()], 'taxes_id': [Command.clear()],
        })
        with freeze_time('2026-08-01'):
            cls.po = cls.env['purchase.order'].create({'partner_id': cls.vendor.id,
                'order_line': [Command.create({'product_id': cls.product.id,
                    'product_qty': 10, 'price_unit': 100, 'tax_ids': [Command.clear()]})]})
            cls.po.button_confirm()
            cls.receipt = cls.po.picking_ids
            cls.receipt.move_ids.quantity = 10
            cls.receipt.move_ids.picked = True
            cls.receipt.button_validate()
            cls.po.action_create_invoice()
            cls.bill = cls.po.invoice_ids
            cls.bill.invoice_date = '2026-08-01'
            cls.bill.invoice_line_ids.price_unit = 110
            cls.bill.action_post()
        cls.move = cls.receipt.move_ids

    def _return(self, quantity=2):
        with freeze_time('2026-09-01'):
            returned = self.receipt._create_return()
            returned.move_ids.product_uom_qty = quantity
            returned.action_assign()
            returned.move_ids.quantity = quantity
            returned.move_ids.picked = True
            returned.button_validate()
        return returned

    def _credit(self, quantity=2, price=110):
        with freeze_time('2026-09-02'):
            credit = self.bill._reverse_moves(default_values_list=[{
                'date': '2026-09-02', 'invoice_date': '2026-09-02'}])
            credit.invoice_line_ids.write({'quantity': quantity, 'price_unit': price})
            credit.action_post()
        return credit

