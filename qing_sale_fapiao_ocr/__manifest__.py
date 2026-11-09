{
    'name': 'Qing Sales Tax Invoice Upload',
    'summary': 'Create or link customer invoices from reviewed issued tax invoices',
    'description': 'Upload and review issued tax invoices, then link recognized results to customer invoices.',
    'version': '20.0.1.4',
    'category': 'Sales/Sales',
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'support': 'kevin@odoomommy.com',
    'license': 'LGPL-3',
    'depends': ['qing_sale', 'qing_account_fapiao_ocr'],
    'data': [
        'views/view_sale_order_form.xml',
        'views/view_sale_fapiao_upload_form.xml',
    ],
    'application': False,
    'installable': True,
}
