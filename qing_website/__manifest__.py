{
    'name': 'Qing Website',
    'summary': 'QingERP favicon, "Powered by QingERP" footer and generator tag on websites',
    'version': '20.0.1.0.5',
    'category': 'Hidden',
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'support': 'kevin@odoomommy.com',
    'license': 'LGPL-3',
    'depends': ['qing_base', 'website'],
    'data': [
        'views/website_templates.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'auto_install': True,
    'application': False,
    'installable': True,
    'description': 'Websites that still show the stock Odoo favicon get the QingERP Q mark, '
                   'including new websites. A favicon uploaded in the website settings is kept. '
                   '/favicon.ico is a short-lived redirect, so browsers pick up a new favicon within the hour. '
                   'Footers and portal pages say "Powered by QingERP" instead of Odoo, and pages carry '
                   'a QingERP generator tag. The /website/info page names QingERP as well.',
}
