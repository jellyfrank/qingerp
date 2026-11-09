import os
# Run only against the disposable database created by run_isolated.sh.
from odoo import Command
assert env.cr.dbname == 'qing_spreadsheet_test'
user = env.ref('base.user_admin')
user.write({'login': 'sheet_admin', 'password': os.environ["QING_TEST_PASSWORD"]})
book = env['qing.spreadsheet'].search([('name', '=', 'Browser acceptance')], limit=1)
if not book:
    book = env['qing.spreadsheet'].create({'name': 'Browser acceptance', 'owner_id': user.id})
env.cr.commit()
print('WORKBOOK_ID', book.id)
# Persisted test users for independent HTTP sessions and concurrency checks.
company_a = env.company
company_b = env['res.company'].search([('name', '=', 'Sheet Test Company B')], limit=1) or env['res.company'].create({'name': 'Sheet Test Company B'})
for login in ['sheet_owner', 'sheet_editor', 'sheet_viewer', 'sheet_outsider', 'sheet_manager']:
    groups = [env.ref('base.group_user').id]
    if login in ['sheet_owner', 'sheet_editor']:
        groups.append(env.ref('base.group_allow_export').id)
    if login == 'sheet_manager':
        groups.append(env.ref('qing_spreadsheet.group_spreadsheet_manager').id)
    values = {'name': login, 'login': login, 'password': os.environ["QING_TEST_PASSWORD"],
              'group_ids': [Command.set(groups)], 'company_id': company_a.id,
              'company_ids': [Command.set([company_a.id, company_b.id] if login == 'sheet_owner' else [company_a.id])]}
    target = env['res.users'].search([('login', '=', login)])
    if target:
        target.with_context(no_reset_password=True).write(values)
    else:
        env['res.users'].with_context(no_reset_password=True).create(values)
env.cr.commit()
print('HTTP_TEST_USERS_READY')
