{
    'name': 'Qing Display',
    'summary': 'Live sales display for office and workshop screens',
    'version': '20.0.1.0.6',
    'category': 'Productivity/Dashboard',
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'support': 'kevin@odoomommy.com',
    'license': 'LGPL-3',
    'depends': ['qing_base', 'sale_management'],
    'data': [
        'security/groups.xml',
        'security/ir.access.csv',
        'data/ir_cron_data.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'qing_display/static/src/js/display.js',
            'qing_display/static/src/xml/display.xml',
            'qing_display/static/src/scss/display.scss',
        ],
    },
    'application': True,
    'installable': True,
    'description': 'Display read-only, real-time sales information on office and workshop screens.',
}
