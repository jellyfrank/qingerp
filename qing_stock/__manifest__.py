{
    'name': 'Qing Stock',
    'summary': 'QingERP inventory integration and scanner onboarding',
    'version': '20.0.1.0.11',
    'category': 'Inventory/Inventory',
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'support': 'kevin@odoomommy.com',
    'license': 'LGPL-3',
    'depends': ['qing_base', 'stock'],
    'data': [
        'views/view_res_users_form.xml',
        'views/view_res_config_settings_form.xml',
        'views/view_stock_picking_form.xml',
        'views/stock_help_message.xml',
        'data/delivery_provider_action.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'qing_stock/static/src/js/stock_move_product_link.js',
            'qing_stock/static/src/xml/stock_move_product_link.xml',
        ],
    },
    'auto_install': True,
    'application': False,
    'installable': True,
    'description': 'Provide QingERP inventory settings and barcode-app onboarding using native stock operations.',
}
