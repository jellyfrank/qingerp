{
    'name': 'Qing Account',
    'summary': 'Accounting improvements and batch reset to draft for billing users',
    'version': '20.0.1.24',
    'category': 'Accounting/Accounting',
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'support': 'kevin@odoomommy.com',
    'license': 'LGPL-3',
    'depends': ['qing_base', 'account'],
    'data': ['security/ir.access.csv', 'views/view_partner_statement_form.xml', 'views/view_statement_snapshot_form.xml', 'data/ir_actions_server.xml', 'views/account_menuitem.xml', 'views/view_bank_import_form.xml', 'views/view_account_journal_form.xml'],
    'assets': {
        'web.assets_backend': [
            'qing_account/static/src/bank_workbench/bank_workbench.js',
            'qing_account/static/src/bank_workbench/bank_workbench.xml',
            'qing_account/static/src/bank_workbench/bank_workbench.scss',
            'qing_account/static/src/js/list_row_numbers.js',
            'qing_account/static/src/xml/list_row_numbers.xml',
        ],
        'web.assets_unit_tests': [
            'qing_account/static/tests/list_row_numbers.test.js',
        ],
    },
    'uninstall_hook': 'uninstall_hook',
    'application': False,
    'installable': True,
    'description': 'Import and match bank statements, review partner statements and streamline daily accounting operations.',
}
