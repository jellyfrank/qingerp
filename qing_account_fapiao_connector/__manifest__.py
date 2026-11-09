{
    'name': 'Qing 开票服务衔接',
    'summary': '审批申请与公共开票任务衔接，保留额度及登记控制',
    'description': 'Connect approved invoice requests to provider tasks and manage issuance results and tax-invoice registration.',
    'version': '20.0.1.15',
    'category': 'Accounting/Accounting',
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'support': 'kevin@odoomommy.com',
    'license': 'LGPL-3',
    'depends': ['qing_account_fapiao', 'qing_fapiao'],
    'data': [
        'security/fapiao_groups.xml',
        'security/ir.access.csv',
        'views/account_menuitem.xml',
        'views/view_fapiao_provider_select_form.xml',
        'views/view_fapiao_request_form.xml',
        'views/view_qing_fapiao_order_list.xml',
        'views/view_qing_fapiao_order_form.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'qing_account_fapiao_connector/static/src/scss/fapiao_order_json.scss',
        ],
    },
    'installable': True,
    'application': False,
}
