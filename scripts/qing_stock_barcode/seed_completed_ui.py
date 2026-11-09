"""Native PC-completed fixtures; only run in the disposable barcode test database."""
from odoo import Command

assert env.cr.dbname == 'qing_barcode_test'
warehouse = env['stock.warehouse'].search([('company_id', '=', env.company.id)], limit=1)
product = env['product.product'].create({'name': 'Completed View Widget', 'is_storable': True, 'barcode': 'COMPLETED-UI'})
user = env['res.users'].search([('login', '=', 'scan_ui_zh')], limit=1)
for operation, source, destination in [
    (warehouse.in_type_id, env.ref('stock.stock_location_suppliers'), warehouse.lot_stock_id),
    (warehouse.out_type_id, warehouse.lot_stock_id, env.ref('stock.stock_location_customers')),
    (warehouse.int_type_id, warehouse.lot_stock_id, warehouse.wh_output_stock_loc_id),
]:
    for done in (False, True):
        transfer = env['stock.picking'].create({
            'origin': f'Completed UI {operation.code} {done}', 'user_id': user.id,
            'picking_type_id': operation.id, 'location_id': source.id, 'location_dest_id': destination.id,
            'move_ids': [Command.create({'product_id': product.id, 'product_uom_qty': 3, 'uom_id': product.uom_id.id,
                'location_id': source.id, 'location_dest_id': destination.id})],
        })
        transfer.action_confirm()
        if done:
            transfer.move_ids.quantity = 3
            transfer.move_ids.picked = True
            transfer.button_validate()
            assert transfer.state == 'done'
env.cr.commit()
print('COMPLETED_UI_READY')
