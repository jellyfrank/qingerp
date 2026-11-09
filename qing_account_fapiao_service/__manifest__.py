{
    'name': 'Qing 云端开票服务连接',
    'summary': '申请并连接 QingERP 托管开票服务',
    'version': '20.0.1.0.11',
    'category': 'Accounting/Accounting',
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'support': 'kevin@odoomommy.com',
    'license': 'LGPL-3',
    'depends': ['qing_account_fapiao_connector', 'qing_service_client'],
    'data': [
        'security/ir.access.csv',
        'views/service_connection_views.xml',
        'views/service_menu.xml',
    ],
    'installable': True,
    'application': False,
    'description': 'Apply for hosted QingERP invoicing and manage access credentials and service status. Activation requires review and subscription.',
}
