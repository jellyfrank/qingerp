{
    'name': 'Qing Warehouse Barcode',
    'summary': 'Warehouse scanning, inventory counts and reliable stock execution',
    'version': '20.0.1.0.23',
    'category': 'Inventory/Inventory',
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'support': 'kevin@odoomommy.com',
    'license': 'LGPL-3',
    'depends': ['qing_stock', 'barcodes'],
    'data': [
        'security/groups.xml',
        'security/ir.access.csv',
        'views/view_stock_picking_type_form.xml',
        'views/view_stock_picking_form.xml',
        'views/view_stock_picking_kanban.xml',
        'views/view_qing_stock_scan_session_list.xml',
        'views/view_qing_stock_scan_session_form.xml',
        'views/menus.xml',
        'views/stock_help_message.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'qing_stock_barcode/static/src/**/*',
        ],
        'web.assets_unit_tests': [
            'qing_stock_barcode/static/tests/**/*.js',
        ],
    },
    'application': True,
    'installable': True,
    'description': 'Use a scanning workbench for receipts, deliveries, internal transfers and counts with native stock operations.',
}
