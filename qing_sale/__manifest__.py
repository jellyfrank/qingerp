{
    'name': 'Qing Sale',
    'summary': 'Sales row numbers and separate quotation/order menus',
    'version': '20.0.1.8',
    'category': 'Sales/Sales',
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'support': 'kevin@odoomommy.com',
    'license': 'LGPL-3',
    'depends': ['qing_base', 'sale_management'],
    'data': [
        'security/sales_team_access.xml',
        'security/ir.access.csv',
        'views/view_res_config_settings_form.xml',
        'data/sale_menu_actions.xml',
        'views/view_sale_order_payment.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'qing_sale/static/src/js/list_row_numbers.js',
            'qing_sale/static/src/xml/list_row_numbers.xml',
        ],
        'web.assets_unit_tests': ['qing_sale/static/tests/list_row_numbers.test.js'],
    },
    'application': False,
    'installable': True,
    'uninstall_hook': 'uninstall_hook',
    'description': 'Improve quotation and order navigation, show row numbers and display payment progress based on actual receipts.',
}
