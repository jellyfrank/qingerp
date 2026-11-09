"""Synthetic SKU/UI fixtures for qing_barcode_test only, after seed_browser.py."""
import os
from odoo import Command
from odoo.tests import new_test_user

assert env.cr.dbname == 'qing_barcode_test'
ui_user = new_test_user(env(context=dict(env.context, no_reset_password=True)), login='scan_ui_zh',
              password=os.environ["QING_TEST_PASSWORD"], lang='zh_CN', groups='qing_stock_barcode.group_scan_user')
ui_user.name = '仓库负责人'
warehouse = env['stock.warehouse'].search([('company_id', '=', env.company.id)], limit=1)
product = env['product.product'].create({'name': 'Office Chair', 'is_storable': True, 'default_code': 'FURN_7777'})
source = env.ref('stock.stock_location_suppliers')
transfer = env['stock.picking'].create({
    'origin': 'SKU 界面验收', 'user_id': ui_user.id, 'picking_type_id': warehouse.in_type_id.id,
    'location_id': source.id, 'location_dest_id': warehouse.lot_stock_id.id,
    'move_ids': [Command.create({'product_id': product.id, 'product_uom_qty': 10, 'uom_id': product.uom_id.id,
                                'location_id': source.id, 'location_dest_id': warehouse.lot_stock_id.id})],
})
transfer.action_confirm()
transfer.action_assign()
env.cr.commit()
print('SKU_UI_READY', transfer.id)
