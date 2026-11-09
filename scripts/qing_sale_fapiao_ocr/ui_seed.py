"""Prepare or verify a synthetic browser acceptance case in an isolated database.

Run with Odoo shell against qing_sale_invoice_* only. Set
QING_UPLOAD_UI_PASSWORD for preparation, or QING_UPLOAD_UI_VERIFY=1 after the
browser upload. Preparation creates no invoice and performs no OCR. It commits
only synthetic setup records; verification does not change business records.
"""

import hashlib
import json
import os
from pathlib import Path

from odoo import Command
from odoo.addons import qing_account_fapiao_ocr
from odoo.tests import new_test_user


KEY = 'qing_sale_fapiao_ocr.ui_acceptance'
LOGIN = 'qing_upload_ui'


def run_ui_fixture(shell_env):
    if not shell_env.cr.dbname.startswith('qing_sale_invoice_'):
        raise RuntimeError('Refusing to use UI fixtures outside qing_sale_invoice_* databases.')
    seed = shell_env(su=True, context={
        **shell_env.context,
        'tracking_disable': True,
        'mail_create_nosubscribe': True,
        'mail_notify_force_send': False,
        'no_reset_password': True,
        'no_vat_validation': True,
    })
    module = seed['ir.module.module'].search([
        ('name', '=', 'qing_sale_fapiao_ocr'), ('state', '=', 'installed'),
    ])
    if not module:
        raise RuntimeError('Install qing_sale_fapiao_ocr before preparing the UI fixture.')
    parameters = seed['ir.config_parameter']
    saved = parameters.get_str(KEY)
    snapshot = json.loads(saved) if saved else None
    fixture = Path(qing_account_fapiao_ocr.__file__).parent / 'tests/fixtures/synthetic_tax_invoice.pdf'
    source_sha256 = hashlib.sha256(fixture.read_bytes()).hexdigest()

    if os.environ.get('QING_UPLOAD_UI_VERIFY') == '1':
        if not snapshot:
            raise RuntimeError('Prepare the UI fixture before verifying it.')
        seed = seed(context={**seed.context, 'allowed_company_ids': [snapshot['company_id']]})
        order = seed['sale.order'].browse(snapshot['order_id']).exists()
        assert order and order.state == 'sale'
        assert order.company_id.id == snapshot['company_id']
        assert order.currency_id == seed.ref('base.CNY')
        assert (order.amount_untaxed, order.amount_tax, order.amount_total) == (100, 13, 113)
        assert source_sha256 == snapshot['source_sha256'], 'The reference PDF changed after preparation.'
        invoice = order.invoice_ids
        assert len(invoice) == 1, 'Expected exactly one customer invoice.'
        assert invoice.state == 'draft' and invoice.move_type == 'out_invoice'
        assert (invoice.amount_untaxed, invoice.amount_tax, invoice.amount_total) == (100, 13, 113)
        assert invoice.currency_id == order.currency_id
        assert invoice.invoice_line_ids.sale_line_ids == order.order_line
        assert order.order_line.qty_invoiced == 1 and order.order_line.qty_to_invoice == 0
        tickets = seed['qing.fapiao'].search([
            ('company_id', '=', order.company_id.id), ('move_ids', 'in', invoice.ids),
        ])
        assert len(tickets) == 1, 'Expected exactly one linked tax invoice.'
        assert tickets.state == 'draft' and tickets.direction == 'out' and tickets.color == 'blue'
        assert tickets.move_ids == invoice and tickets.amount_total == 113
        assert not tickets.allocation_ids
        assert hashlib.sha256(bytes(tickets.source_file)).hexdigest() == source_sha256
        attachments = seed['ir.attachment'].search([
            ('res_model', '=', 'account.move'), ('res_id', '=', invoice.id), ('type', '=', 'binary'),
        ])
        originals = attachments.filtered(
            lambda attachment: hashlib.sha256(bytes(attachment.raw)).hexdigest() == source_sha256)
        assert len(originals) == 1, 'Expected exactly one unchanged source PDF on the customer invoice.'
        assert invoice.message_main_attachment_id == originals
        print('QING_SALE_UPLOAD_UI_VERIFY_PASS ' + json.dumps({
            'order_id': order.id, 'invoice_id': invoice.id, 'tax_invoice_id': tickets.id,
            'attachment_id': originals.id, 'source_sha256': source_sha256,
            'invoice_state': invoice.state, 'tax_invoice_state': tickets.state,
            'total': invoice.amount_total,
        }, sort_keys=True))
        return

    password = os.environ.get('QING_UPLOAD_UI_PASSWORD')
    if not password:
        raise RuntimeError('Set QING_UPLOAD_UI_PASSWORD for the isolated browser test user.')
    if snapshot:
        manager = seed['res.users'].browse(snapshot['user_id']).exists()
        order = seed['sale.order'].browse(snapshot['order_id']).exists()
        if not manager or not order:
            raise RuntimeError('Saved UI fixture records are missing; use a fresh isolated database.')
        if (
            order.company_id.id != snapshot['company_id']
            or order.currency_id != seed.ref('base.CNY')
            or (order.amount_untaxed, order.amount_tax, order.amount_total) != (100, 13, 113)
        ):
            raise RuntimeError('Saved UI fixture amounts or company changed; rebuild the isolated fixture.')
        manager.password = password
        shell_env.cr.commit()
        print('QING_SALE_UPLOAD_UI_READY ' + json.dumps({
            'order_id': order.id, 'user_login': manager.login,
            'company_id': snapshot['company_id'],
        }, sort_keys=True))
        return

    company = seed['res.company'].create({
        'name': '合成销售公司', 'currency_id': seed.ref('base.CNY').id,
        'country_id': seed.ref('base.cn').id,
        'account_fiscal_country_id': seed.ref('base.cn').id,
        'vat': 'TESTSELLER',
    })
    # Company creation queues native chart loading after commit. Finish it before
    # creating the synthetic accounts and taxes so it cannot remove the fixture.
    company_id = company.id
    shell_env.cr.commit()
    shell_env.invalidate_all()
    company = seed['res.company'].browse(company_id).exists()
    seed = seed(context={**seed.context, 'allowed_company_ids': company.ids})
    accounts = {}
    for key, name, code, account_type in (
        ('receivable', '合成应收账款', '112200', 'asset_receivable'),
        ('payable', '合成应付账款', '220200', 'liability_payable'),
        ('income', '合成销售收入', '600100', 'income'),
        ('tax_due', '合成销项税额', '222101', 'liability_current'),
    ):
        accounts[key] = seed['account.account'].create({
            'name': name, 'code': code, 'account_type': account_type,
            'reconcile': key in ('receivable', 'payable'),
            'company_ids': [Command.set(company.ids)],
        })
    journal = seed['account.journal'].create({
        'name': '合成销售日记账', 'code': 'SUI', 'type': 'sale',
        'company_id': company.id, 'default_account_id': accounts['income'].id,
    })
    tax_group = seed['account.tax.group'].create({
        'name': '合成销项增值税', 'company_id': company.id,
        'country_id': company.account_fiscal_country_id.id,
    })
    tax = seed['account.tax'].create({
        'name': '合成销项税 13%', 'amount': 13, 'amount_type': 'percent',
        'type_tax_use': 'sale', 'company_id': company.id,
        'price_include_override': 'tax_excluded', 'tax_group_id': tax_group.id,
    })
    (tax.invoice_repartition_line_ids | tax.refund_repartition_line_ids).filtered(
        lambda line: line.repartition_type == 'tax').account_id = accounts['tax_due']
    manager = new_test_user(
        seed, login=LOGIN, password=password, name='销售上传验收', lang='zh_CN',
        groups='account.group_account_manager,sales_team.group_sale_manager',
        company_id=company.id, company_ids=[Command.set(company.ids)],
        context={'no_reset_password': True, 'tracking_disable': True},
    )
    partner = seed['res.partner'].create({
        'name': '合成采购公司', 'company_id': company.id, 'vat': 'TESTBUYER',
        'property_account_receivable_id': accounts['receivable'].id,
        'property_account_payable_id': accounts['payable'].id,
    })
    pricelist = seed['product.pricelist'].create({
        'name': '合成人民币价目表', 'company_id': company.id,
        'currency_id': company.currency_id.id,
    })
    product = seed['product.product'].create({
        'name': '合成验收服务', 'type': 'service', 'invoice_policy': 'order',
        'list_price': 100, 'company_id': company.id,
        'property_account_income_id': accounts['income'].id,
        'taxes_id': [Command.set(tax.ids)], 'supplier_taxes_id': [Command.clear()],
    })
    order = seed['sale.order'].create({
        'partner_id': partner.id, 'partner_invoice_id': partner.id,
        'company_id': company.id, 'pricelist_id': pricelist.id,
        'user_id': manager.id, 'journal_id': journal.id,
        'client_order_ref': '合成销售上传界面验收',
        'order_line': [Command.create({
            'name': product.name, 'product_id': product.id,
            'product_uom_qty': 1, 'price_unit': 100, 'tax_ids': [Command.set(tax.ids)],
        })],
    })
    order.action_confirm()
    assert (order.amount_untaxed, order.amount_tax, order.amount_total) == (100, 13, 113)
    assert not order.invoice_ids
    parameters.set_str(KEY, json.dumps({
        'order_id': order.id, 'user_id': manager.id, 'company_id': company.id,
        'source_sha256': source_sha256,
    }, sort_keys=True))
    shell_env.cr.commit()
    print('QING_SALE_UPLOAD_UI_READY ' + json.dumps({
        'order_id': order.id, 'user_login': manager.login, 'company_id': company.id,
    }, sort_keys=True))


run_ui_fixture(env)
