{
    'name': 'Qing Website',
    'summary': 'Use the QingERP Q mark as the website favicon until one is uploaded',
    'version': '20.0.1.0.1',
    'category': 'Hidden',
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'support': 'kevin@odoomommy.com',
    'license': 'LGPL-3',
    'depends': ['qing_base', 'website'],
    'post_init_hook': 'post_init_hook',
    'auto_install': True,
    'application': False,
    'installable': True,
    'description': 'Websites that still show the stock Odoo favicon get the QingERP Q mark, '
                   'including new websites. A favicon uploaded in the website settings is kept. '
                   '/favicon.ico is a short-lived redirect, so browsers pick up a new favicon within the hour.',
}
