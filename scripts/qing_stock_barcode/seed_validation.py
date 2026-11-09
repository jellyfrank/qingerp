"""Create validation-policy fixtures in the disposable barcode database only."""
import os
import uuid

from odoo import Command
from odoo.tests import new_test_user

assert env.cr.dbname == 'qing_barcode_test'
user = env['res.users'].search([('login', '=', 'validation_operator')], limit=1)
if not user:
    user = new_test_user(env(context=dict(env.context, no_reset_password=True)), login='validation_operator',
                         password=os.environ["QING_TEST_PASSWORD"], groups='qing_stock_barcode.group_scan_user')
warehouse = env['stock.warehouse'].search([('company_id', '=', env.company.id)], limit=1)
product = env['product.product'].search([('barcode', '=', 'VALIDATE-ITEM')], limit=1)
if not product:
    product = env['product.product'].create({'name': 'Validation Widget', 'barcode': 'VALIDATE-ITEM', 'is_storable': True})
source = env.ref('stock.stock_location_suppliers')
for case, policy, demand in [('ask-create', 'ask', 2), ('ask-cancel', 'ask', 2),
                             ('always', 'always', 2), ('never', 'never', 2), ('full', 'ask', 1)]:
    operation = warehouse.in_type_id.copy({'name': 'Validation ' + case, 'create_backorder': policy,
                                           'sequence_code': 'VAL-' + case + '-' + uuid.uuid4().hex[:8] + '/'})
    transfer = env['stock.picking'].create({
        'origin': 'Validation ' + case, 'picking_type_id': operation.id,
        'location_id': source.id, 'location_dest_id': warehouse.lot_stock_id.id,
        'move_ids': [Command.create({'product_id': product.id, 'product_uom_qty': demand,
            'uom_id': product.uom_id.id, 'location_id': source.id, 'location_dest_id': warehouse.lot_stock_id.id})],
    })
    transfer.action_confirm()
    transfer.action_assign()
env.cr.commit()
print('VALIDATION_FIXTURES_READY')
