"""Run after seed_browser.py, seed_sku_ui.py and seed_completed_ui.py in qing_barcode_test."""
import os
import json
from odoo.tests import new_test_user

assert env.cr.dbname == 'qing_barcode_test'
new_test_user(env(context=dict(env.context, no_reset_password=True)), login='stock_no_scan',
              password=os.environ["QING_TEST_PASSWORD"], lang='zh_CN', groups='stock.group_stock_user')
action = env['ir.actions.act_window'].create({
    'name': 'Mobile entry test', 'res_model': 'stock.picking', 'view_mode': 'kanban,form',
    'domain': "[('origin', 'ilike', 'Completed UI')]",
})
print('MOBILE_ENTRY_FIXTURES', json.dumps({
    'action': action.id,
    'pickings': env['stock.picking'].search([('origin', 'ilike', 'Completed UI')]).read(['name', 'state', 'picking_type_code']),
}))
env.cr.commit()
