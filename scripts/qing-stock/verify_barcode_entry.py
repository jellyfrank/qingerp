"""Run with Odoo shell in a disposable database, before and after scanner install."""
import json

from lxml import html

from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user


assert env.cr.dbname.startswith('qing_scan_onboarding_test')
installed = env['ir.module.module'].search([('name', '=', 'qing_stock_barcode')])
assert installed.state in ('installed', 'uninstalled')
assert env['ir.module.module'].search([('name', '=', 'qing_stock')]).state == 'installed'
state_before = installed.state
admin = env.ref('base.user_admin')
operator = new_test_user(env, login='scan_onboarding_operator', groups='base.group_user,stock.group_stock_user')
result = {'scanner': state_before, 'bridge': 'installed', 'operations': {}}


def footer(user, operation):
    model = env['stock.picking'].with_user(user).with_context(
        lang='zh_CN', allowed_company_ids=user.company_ids.ids,
        restricted_picking_type_code=operation,
    )
    rendered = model.get_empty_list_help('')
    root = html.fromstring(str(rendered))
    nodes = root.xpath("//p[@name='footer_help_message']")
    return nodes[0] if nodes else None


for operation in ('incoming', 'outgoing', 'internal'):
    node = footer(admin, operation)
    text = ''.join(node.itertext()).strip()
    assert node.xpath('.//a')[0].get('name') == 'qing_stock.action_open_barcode'
    action = env.ref('qing_stock.action_open_barcode').with_user(admin).with_context(
        restricted_picking_type_code=operation,
    ).run()
    if state_before == 'uninstalled':
        assert text == '安装 QingERP 仓库扫码', text
        assert action['res_model'] == 'ir.module.module'
        assert action['res_id'] == installed.id
        assert action['views'] == [(env.ref('base.module_form').id, 'form')]
        # Opening the native module page must not change any installation state.
        assert installed.state == state_before
        ordinary_footer = footer(operator, operation)
        assert '请联系管理员安装 QingERP 仓库扫码。' in ''.join(ordinary_footer.itertext())
        assert not ordinary_footer.xpath('.//a')
    else:
        assert text == '打开 QingERP 仓库扫码', text
        assert action['tag'] == 'qing_stock_barcode.workbench'
        assert action['params'] == {'operation': operation}
        assert footer(operator, operation) is None
    result['operations'][operation] = {'text': text, 'action_type': action['type']}

try:
    env.ref('qing_stock.action_open_barcode').with_user(operator).run()
except (UserError, AccessError):
    result['unauthorized'] = 'denied'
else:
    raise AssertionError('An inventory-only user may not install or access the scanner')

if state_before == 'installed':
    scanner = new_test_user(env, login='scan_onboarding_scanner', groups='qing_stock_barcode.group_scan_user')
    assert '打开 QingERP 仓库扫码' in ''.join(footer(scanner, 'incoming').itertext())
    assert not env['stock.picking.type'].with_user(scanner).has_access('write')
    opened = env.ref('qing_stock.action_open_barcode').with_user(scanner).run()
    assert opened['tag'] == 'qing_stock_barcode.workbench'
    result['scan_operator'] = 'allowed'

assert 'stock_barcode' not in env['ir.module.module'].search([('state', '=', 'installed')]).mapped('name')
print('ONBOARDING_RESULT=' + json.dumps(result, ensure_ascii=False))
env.cr.rollback()
