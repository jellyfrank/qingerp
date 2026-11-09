"""Run with odoo shell only in the disposable qing_barcode_test database."""
import os
from odoo import Command

assert env.cr.dbname == 'qing_barcode_test', 'This script is restricted to the disposable database.'
admin = env.ref('base.user_admin')
admin.with_context(no_reset_password=True).write({'login': 'barcode_admin', 'password': os.environ["QING_TEST_PASSWORD"],
    'lang': 'en_US',
    'group_ids': [Command.link(env.ref('qing_stock_barcode.group_scan_manager').id)]})
warehouse = env['stock.warehouse'].search([('company_id', '=', env.company.id)], limit=1)
product = env['product.product'].search([('barcode', '=', 'UI-WIDGET')], limit=1)
if not product:
    product = env['product.product'].create({'name': 'Browser Widget', 'is_storable': True, 'barcode': 'UI-WIDGET'})
transfer = env['stock.picking'].create({'origin': 'Browser acceptance', 'picking_type_id': warehouse.in_type_id.id,
    'location_id': env.ref('stock.stock_location_suppliers').id, 'location_dest_id': warehouse.lot_stock_id.id,
    'move_ids': [Command.create({'product_id': product.id, 'product_uom_qty': 12, 'uom_id': product.uom_id.id,
        'location_id': env.ref('stock.stock_location_suppliers').id, 'location_dest_id': warehouse.lot_stock_id.id})]})
transfer.action_confirm()
transfer.action_assign()
env.cr.commit()
print('BROWSER_FIXTURE', transfer.id, transfer.name)
