"""Prepare synthetic browser acceptance data in an isolated rental test database.

Run through the target Odoo 20 shell, for example::

    QING_RENTAL_UI_SECRET_PATH=/private/task-dir/rental-ui.json \
        odoo shell -c /path/to/test.conf -d qing_rental_ui_test \
        --no-http < scripts/qing_rental/prepare_ui.py

The destination directory must exist with mode 0700. The script only accepts
database names starting with ``qing_rental_`` and refuses to replace an existing
credential file. It creates a new synthetic company and user on every run, never
changes the administrator password, and does not contact a production instance.
The randomly generated password is written only to the mode-0600 private file.
"""

import json
import os
from pathlib import Path
import secrets
import stat
from datetime import timedelta

from odoo import Command, api, fields


def prepare_ui(shell_env):
    """Create a Chinese-language quotation ready for the full UI walkthrough."""
    database = shell_env.cr.dbname
    if not database.startswith('qing_rental_'):
        raise RuntimeError('This script requires a database named qing_rental_*.')
    secret_value = os.environ.get('QING_RENTAL_UI_SECRET_PATH')
    if not secret_value:
        raise RuntimeError('Set QING_RENTAL_UI_SECRET_PATH to a private absolute path.')
    secret_path = Path(secret_value)
    if not secret_path.is_absolute() or secret_path.is_symlink():
        raise RuntimeError('The credential destination must be an absolute non-symlink path.')
    parent = secret_path.parent
    if not parent.is_dir() or parent.is_symlink() or stat.S_IMODE(parent.stat().st_mode) != 0o700:
        raise RuntimeError('The credential directory must already exist with mode 0700.')
    if secret_path.exists():
        raise RuntimeError('Refusing to replace an existing credential file.')
    module = shell_env['ir.module.module']._get('qing_rental')
    if module.state != 'installed':
        raise RuntimeError('Install qing_rental before preparing browser data.')

    descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        result = _create_data(shell_env)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as private_file:
            json.dump(result, private_file, ensure_ascii=False, indent=2)
            private_file.write('\n')
            private_file.flush()
            os.fsync(private_file.fileno())
        shell_env.cr.commit()
    except Exception:
        shell_env.cr.rollback()
        try:
            os.close(descriptor)
        except OSError:
            pass
        secret_path.unlink(missing_ok=True)
        raise

    print(json.dumps({key: value for key, value in result.items() if key != 'password'}, ensure_ascii=False))
    print('Private browser credentials saved to: %s' % secret_path)


def _create_data(shell_env):
    token = secrets.token_hex(4)
    shell_env['res.lang']._activate_and_install_lang('zh_CN')
    shell_env['ir.module.module']._get('qing_rental')._update_translations('zh_CN', overwrite=True)
    currency = shell_env.ref('base.CNY')
    currency.active = True
    company_values = {
        'name': '青 ERP 租赁验收公司 ' + token,
        'currency_id': currency.id,
    }
    if 'iap_enrich_auto_done' in shell_env['res.company']._fields:
        company_values['iap_enrich_auto_done'] = True
    company = shell_env['res.company'].with_context(tracking_disable=True).create(company_values)
    setup = api.Environment(shell_env.cr, shell_env.uid, dict(
        shell_env.context, allowed_company_ids=[company.id], lang='zh_CN',
        tracking_disable=True, mail_create_nosubscribe=True,
    ), su=True)
    china_module = setup['ir.module.module']._get('l10n_cn')
    template = 'cn' if china_module.state == 'installed' else 'generic_coa'
    setup['account.chart.template'].try_loading(template, company=company, install_demo=False)
    company.write({'country_id': shell_env.ref('base.cn').id, 'currency_id': currency.id})
    company.partner_id.lang = 'zh_CN'

    password = secrets.token_urlsafe(24)
    group_ids = [setup.ref(xmlid).id for xmlid in (
        'qing_rental.group_rental_manager',
        'stock.group_stock_manager',
        'stock.group_stock_multi_locations',
        'account.group_account_manager',
        'base.group_partner_manager',
        'product.group_product_manager',
    )]
    user = setup['res.users'].with_context(no_reset_password=True).create({
        'name': '租赁验收员 ' + token,
        'login': 'qing_rental_ui_' + token,
        'password': password,
        'lang': 'zh_CN',
        'tz': 'Asia/Shanghai',
        'company_id': company.id,
        'company_ids': [Command.set([company.id])],
        'group_ids': [Command.set(group_ids)],
    })
    ui = api.Environment(shell_env.cr, user.id, dict(
        setup.context, allowed_company_ids=[company.id], lang='zh_CN',
    ))
    warehouse = ui['stock.warehouse'].create({
        'name': '租赁验收仓库', 'code': 'QRUI', 'company_id': company.id,
    })
    warehouse.action_qing_setup_rental()
    unit = ui.ref('uom.product_uom_unit')
    service = ui['product.product'].create({
        'name': '设备日租服务', 'type': 'service', 'invoice_policy': 'order',
        'uom_id': unit.id, 'list_price': 100, 'company_id': company.id,
        'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()],
        'property_account_income_id': company.income_account_id.id,
    })
    equipment = ui['product.product'].create({
        'name': '便携式投影仪（租赁验收）', 'type': 'consu', 'is_storable': True,
        'uom_id': unit.id, 'company_id': company.id,
        'qing_rental_ok': True, 'qing_rental_service_id': service.id,
        'qing_rental_rate': 100, 'qing_rental_period': 'day',
        'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()],
    })
    ui['stock.quant']._update_available_quantity(equipment, warehouse.qing_rental_stock_location_id, 10)
    customer = ui['res.partner'].create({
        'name': '青岛合成会展客户（验收专用）', 'is_company': True,
        'company_id': company.id, 'lang': 'zh_CN',
        'country_id': ui.ref('base.cn').id,
    })
    pricelist = ui['product.pricelist'].create({
        'name': '租赁验收人民币价格表', 'currency_id': currency.id,
        'company_id': company.id,
    })
    start = fields.Datetime.now().replace(microsecond=0) - timedelta(minutes=10)
    order = ui['sale.order'].create({
        'partner_id': customer.id, 'company_id': company.id,
        'warehouse_id': warehouse.id, 'pricelist_id': pricelist.id,
        'user_id': user.id, 'is_qing_rental': True,
        'client_order_ref': 'RENTAL-UI-' + token,
        'qing_rental_start': start, 'qing_rental_end': start + timedelta(days=2),
        'qing_rental_line_ids': [Command.create({'product_id': equipment.id, 'quantity': 2})],
    })
    order.action_qing_rental_reprice()
    account = ui['account.account'].create({
        'name': '可退租赁押金（验收）', 'code': '224190',
        'account_type': 'liability_current', 'reconcile': True,
        'company_ids': [Command.set([company.id])],
    })
    journal = ui['account.journal'].search([
        ('company_id', '=', company.id), ('type', '=', 'bank'),
    ], limit=1)
    if not journal:
        journal = ui['account.journal'].create({
            'name': '租赁验收银行', 'code': 'QRBK', 'type': 'bank',
            'company_id': company.id,
        })
    if not journal.default_account_id:
        raise RuntimeError('The synthetic bank journal needs a native liquidity account.')
    journal.name = '租赁验收银行'
    deposit = ui['qing.rental.deposit'].create({
        'order_id': order.id, 'amount': 1000, 'kind': 'receipt',
        'journal_id': journal.id, 'liability_account_id': account.id,
        'reference': 'UI-RECEIPT-' + token,
        'note': '合成验收押金；确认订单后可测试过账及退还。',
    })
    return {
        'database': shell_env.cr.dbname, 'login': user.login, 'password': password,
        'company_id': company.id, 'company_name': company.name, 'chart_template': template,
        'order_id': order.id, 'order_name': order.name, 'order_total': order.amount_total,
        'order_start': str(start), 'order_end': str(order.qing_rental_end),
        'rental_action_id': ui.ref('qing_rental.action_rental_orders').id,
        'equipment_id': equipment.id, 'warehouse_id': warehouse.id,
        'deposit_id': deposit.id, 'deposit_journal_id': journal.id,
        'deposit_account_id': account.id,
    }


prepare_ui(env)
