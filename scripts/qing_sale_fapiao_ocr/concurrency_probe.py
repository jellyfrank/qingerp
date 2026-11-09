"""Run from Odoo shell against a disposable qing_sale_invoice_* database.

Example: odoo shell <connection options> -d qing_sale_invoice_test --no-http \
    < scripts/qing_sale_fapiao_ocr/concurrency_probe.py

The probe commits synthetic records so independent database transactions can see
them. It never posts invoices or registers tax invoices. Drop the disposable
database afterwards. OCR itself is mocked; the separate module tests cover real
file recognition. Here, two live cursors establish a stale repeatable-read
snapshot before the first request commits, which deterministically exercises
the same race as overlapping web requests without threads or sleeps.
"""

import io
import json
import uuid
from unittest.mock import patch

from PIL import Image
from psycopg2.errors import SerializationFailure

from odoo import Command, api, fields
from odoo.exceptions import UserError
from odoo.tests import new_test_user
from odoo.tools.binary import BinaryBytes


def run_probe(shell_env):
    if not shell_env.cr.dbname.startswith('qing_sale_invoice_'):
        raise RuntimeError('Refusing to commit test data outside qing_sale_invoice_* databases.')
    module = shell_env['ir.module.module'].search([
        ('name', '=', 'qing_sale_fapiao_ocr'), ('state', '=', 'installed'),
    ])
    if not module:
        raise RuntimeError('Install qing_sale_fapiao_ocr before running this probe.')

    suffix = uuid.uuid4().hex[:10]
    seed = shell_env(su=True, context={
        **shell_env.context,
        'tracking_disable': True,
        'mail_create_nosubscribe': True,
        'mail_notify_force_send': False,
        'no_reset_password': True,
    })
    company = seed['res.company'].create({
        'name': 'Synthetic invoice concurrency ' + suffix,
        'currency_id': seed.ref('base.USD').id,
        'country_id': seed.ref('base.us').id,
        'account_fiscal_country_id': seed.ref('base.us').id,
    })
    seed = seed(context={**seed.context, 'allowed_company_ids': company.ids})
    receivable = seed['account.account'].create({
        'name': 'Synthetic receivable', 'code': '110000',
        'account_type': 'asset_receivable', 'reconcile': True,
        'company_ids': [Command.set(company.ids)],
    })
    income = seed['account.account'].create({
        'name': 'Synthetic sales income', 'code': '400000',
        'account_type': 'income', 'company_ids': [Command.set(company.ids)],
    })
    journal = seed['account.journal'].create({
        'name': 'Synthetic sales', 'code': 'SICP', 'type': 'sale',
        'company_id': company.id, 'default_account_id': income.id,
    })
    manager = new_test_user(
        seed, login='qing_invoice_probe_' + suffix,
        groups='account.group_account_manager,sales_team.group_sale_manager',
        company_id=company.id, company_ids=[Command.set(company.ids)],
        context={'no_reset_password': True, 'tracking_disable': True},
    )
    partner = seed['res.partner'].create({
        'name': 'Synthetic concurrency buyer ' + suffix,
        'company_id': company.id,
        'property_account_receivable_id': receivable.id,
    })
    product = seed['product.product'].create({
        'name': 'Synthetic concurrency service', 'type': 'service',
        'invoice_policy': 'order', 'list_price': 100,
        'company_id': company.id,
        'property_account_income_id': income.id,
        'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()],
    })
    orders = seed['sale.order']
    for label in ('native_native', 'upload_native'):
        order = seed['sale.order'].create({
            'partner_id': partner.id, 'company_id': company.id,
            'user_id': manager.id, 'journal_id': journal.id,
            'client_order_ref': 'Synthetic ' + label + ' ' + suffix,
            'order_line': [Command.create({
                'name': 'Synthetic concurrency line', 'product_id': product.id,
                'product_uom_qty': 1, 'price_unit': 100,
                'tax_ids': [Command.clear()],
            })],
        })
        order.action_confirm()
        orders |= order

    raw_file = io.BytesIO()
    Image.new('RGB', (64, 64), 'white').save(raw_file, format='PNG')
    wizard = seed['qing.fapiao.ocr'].with_user(manager).create({
        'sale_order_id': orders[1].id,
        'source_file': BinaryBytes(raw_file.getvalue()),
        'source_filename': 'synthetic-concurrency.png',
    })
    text = 'Synthetic concurrency fixture. Amount 100.00. Tax 0.00. Total 100.00.'
    result = {
        'text': text, 'engine': 'local',
        'pages': [{'number': 1, 'text': text, 'source': 'ocr'}], 'warnings': [],
    }
    with patch.object(type(seed['qing.ocr.service']), '_extract_text', return_value=result):
        wizard.action_recognize()
    wizard.write({
        'number': str(uuid.uuid4().int)[:20], 'invoice_date': fields.Date.today(),
        'buyer_name': partner.name, 'seller_name': company.name,
        'amount_untaxed': 100, 'amount_tax': 0, 'invoice_total': 100,
        'reviewed': True,
    })
    order_ids, wizard_id, manager_id = orders.ids, wizard.id, manager.id
    context = {
        'allowed_company_ids': company.ids, 'lang': 'en_US',
        'tracking_disable': True, 'mail_notify_force_send': False,
    }
    shell_env.cr.commit()
    registry = shell_env.registry

    def transaction_env(cursor):
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        cursor.execute("SET LOCAL statement_timeout = '30s'")
        cursor.execute('SHOW transaction_isolation')
        isolation = cursor.fetchone()[0]
        assert isolation == 'repeatable read', isolation
        return api.Environment(cursor, manager_id, context)

    evidence = []
    for label, order_id in zip(('native_native', 'upload_native'), order_ids):
        with registry.cursor() as stale_cr, registry.cursor() as first_cr:
            stale_env = transaction_env(stale_cr)
            stale_order = stale_env['sale.order'].browse(order_id)
            assert stale_order.order_line.qty_to_invoice == 1
            assert not stale_order.invoice_ids

            first_env = transaction_env(first_cr)
            if label == 'native_native':
                invoice = first_env['sale.order'].browse(order_id)._create_invoices(grouped=True)
                invoice_id = invoice.id
            else:
                action = first_env['qing.fapiao.ocr'].browse(wizard_id).action_create_draft()
                assert action['res_model'] == 'account.move'
                invoice_id = action['res_id']
            first_cr.commit()

            try:
                stale_order._create_invoices(grouped=True)
            except SerializationFailure as error:
                assert error.pgcode == '40001'
                stale_cr.rollback()
            else:
                stale_cr.rollback()
                raise AssertionError(label + ': stale request did not request a transaction retry')

        with registry.cursor() as fresh_cr:
            fresh_env = transaction_env(fresh_cr)
            fresh_order = fresh_env['sale.order'].browse(order_id)
            assert fresh_order.invoice_ids.ids == [invoice_id]
            assert fresh_order.order_line.qty_invoiced == 1
            assert fresh_order.order_line.qty_to_invoice == 0
            invoice = fresh_env['account.move'].browse(invoice_id)
            assert invoice.state == 'draft'
            assert invoice.amount_total == 100
            try:
                with fresh_cr.savepoint():
                    fresh_order._create_invoices(grouped=True)
            except UserError:
                pass
            else:
                raise AssertionError(label + ': fresh retry created a duplicate invoice')
            fresh_order.invalidate_recordset(['invoice_ids'])
            assert fresh_order.invoice_ids.ids == [invoice_id]
            if label == 'upload_native':
                done_wizard = fresh_env['qing.fapiao.ocr'].browse(wizard_id)
                assert done_wizard.fapiao_id.state == 'draft'
                assert done_wizard.fapiao_id.move_ids == invoice
                assert done_wizard.action_create_draft()['res_id'] == invoice_id
                assert fresh_env['qing.fapiao'].search_count([
                    ('move_ids', 'in', invoice.ids), ('state', '!=', 'cancelled'),
                ]) == 1
                assert fresh_env['ir.attachment'].search_count([
                    ('res_model', '=', 'account.move'), ('res_id', '=', invoice_id),
                    ('name', '=', 'synthetic-concurrency.png'),
                ]) == 1
            fresh_cr.rollback()
            evidence.append({
                'scenario': label, 'order_id': order_id, 'invoice_id': invoice_id,
                'stale_request': '40001', 'fresh_retry': 'nothing_to_invoice',
                'invoice_count': 1, 'qty_invoiced': 1, 'qty_to_invoice': 0,
            })
    print('QING_SALE_INVOICE_CONCURRENCY_PASS ' + json.dumps(evidence, sort_keys=True))


run_probe(env)
