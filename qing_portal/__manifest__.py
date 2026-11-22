{
    'name': 'QingERP Portal Theme',
    'summary': 'A QingERP look for the customer portal: account home, document lists and documents',
    'version': '20.0.1.0.5',
    'category': 'Website/Website',
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'support': 'kevin@odoomommy.com',
    'license': 'LGPL-3',
    'depends': ['portal', 'qing_base'],
    'data': [
        'views/portal_templates.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'qing_portal/static/src/scss/portal_theme.scss',
            'qing_portal/static/src/scss/portal_icons.scss',
        ],
        'portal.assets_chatter_style': [
            'qing_portal/static/src/scss/portal_chatter.scss',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'description': 'Restyles the native customer portal (/my) in the QingERP design: the account home '
                   'greets the customer, shows what is left to pay and lists the quotations and invoices '
                   'waiting for them as cards, with recent activity under the document cards; lists become '
                   'cards on phones and each document keeps its summary beside it. Pages, access rules, '
                   'signing, payment and the website editor options stay native. The portal content follows '
                   "the theme with or without the Website app (a neutral ink accent while the company keeps "
                   "Odoo's default email colours, else the company colour); without the Website app the "
                   'portal also gets a QingERP header with page links and footer.',
}
