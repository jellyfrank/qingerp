{
    'name': 'Qing Spreadsheet',
    'summary': 'Editable workbooks, live Odoo data and reusable record editors',
    'version': '20.0.1.0.8',
    'category': 'Productivity/Dashboard',
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'support': 'kevin@odoomommy.com',
    'license': 'LGPL-3',
    'depends': ['qing_base', 'spreadsheet'],
    'data': [
        'security/groups.xml',
        'security/ir.access.csv',
        'views/view_spreadsheet.xml',
        'views/view_spreadsheet_source.xml',
        'data/sources.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'qing_spreadsheet/static/src/assets/**/*',
        ],
        'spreadsheet.o_spreadsheet': [
            'qing_spreadsheet/static/src/bundle/**/*',
        ],
        'web.assets_unit_tests': [
            ('include', 'spreadsheet.o_spreadsheet'),
            'qing_spreadsheet/static/tests/**/*',
        ],
    },
    'application': True,
    'installable': True,
    'description': 'Edit workbooks, read permission-controlled Odoo data and manage revisions and business-record adapters.',
}
