from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    # Module updates preserve existing translations; rename only this moved menu.
    if env['res.lang'].search_count([('code', '=', 'zh_CN'), ('active', '=', True)]):
        env.ref('qing_stock_barcode.menu_scan_workbench').with_context(lang='zh_CN').write({'name': '扫码'})
