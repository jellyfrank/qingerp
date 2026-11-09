"""Run with `odoo shell` in an isolated cnf_voucher_* database only.

Requires a running HTTP Odoo on the same database at 127.0.0.1:18079.
Creates synthetic records and commits them so the report asset HTTP worker sees
its configuration. Never run against a tenant/customer database.
Output: /tmp/qing-cn-voucher-sample.pdf inside the rendering container.
"""
from pathlib import Path
from uuid import uuid4

from odoo import Command

assert env.cr.dbname.startswith('cnf_voucher_'), 'Isolated sample database required'
company = env['res.company'].with_context(chart_template_load=True).create({
    'name': '青岛贸易凭证样例（虚构数据） ' + uuid4().hex[:6],
    'country_id': env.ref('base.cn').id, 'currency_id': env.ref('base.CNY').id,
})
env = env(context=dict(env.context, allowed_company_ids=company.ids))
company = company.with_env(env)
env['account.chart.template'].try_loading('cn', company, install_demo=False)
journal = env['account.journal'].search([
    ('company_id', '=', company.id), ('type', '=', 'general'),
], limit=1)
accounts = env['account.account'].search([
    ('company_ids', 'in', company.ids), ('account_type', '=', 'asset_current'),
], limit=2)
assert len(accounts) == 2
moves = env['account.move']
for count, currency in [(2, env.ref('base.USD')), (globals().get('sample_line_count', 36), company.currency_id)]:
    lines = []
    for index in range(count):
        sign = 1 if index % 2 == 0 else -1
        lines.append(Command.create({
            'name': '样例第%02d行：贸易期初核对，金额仅用于打印测试' % (index + 1),
            'account_id': accounts[index % 2].id, 'currency_id': currency.id,
            'amount_currency': sign * (100 if currency.name == 'USD' else 700),
            'debit': 700 if sign == 1 else 0, 'credit': 700 if sign == -1 else 0,
        }))
    move = env['account.move'].create({
        'journal_id': journal.id, 'date': '2026-09-01',
        'currency_id': currency.id, 'line_ids': lines,
    })
    if count > 2:
        move.action_post()
    moves |= move
params = env['ir.config_parameter'].sudo()
params.set_str('report.url', 'http://127.0.0.1:18079')
params.set_str('web.base.url', 'http://127.0.0.1:18079')
env.cr.commit()
company.qing_cn_voucher_style = globals().get('voucher_style', 'standard')
env.cr.commit()
report = env.ref('l10n_cn.account_voucher_cn')
content, output_type = report._render_qweb_pdf(report.report_name, moves.ids)
assert output_type == 'pdf' and content.startswith(b'%PDF')
Path('/tmp/qing-cn-voucher-sample.pdf').write_bytes(content)
print('SAMPLE_PDF', len(content), 'bytes', 'moves', moves.ids)
