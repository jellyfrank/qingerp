{
    'name': 'Qing AI Sales Assistant',
    'summary': 'Built-in customer queries, creation and editing, sales orders and follow-ups',
    'version': '20.0.3.2.0',
    'category': 'Sales',
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'support': 'kevin@odoomommy.com',
    'license': 'LGPL-3',
    'depends': ['qing_ai', 'sale'],
    'data': [
        'security/ir.access.csv',
        'data/qing_ai_sale_tool.xml',
        'data/qing_ai_sale_agent.xml',
        'views/view_qing_ai_sale_tool.xml',
    ],
    'installable': True,
    'description': 'Query customers and sales documents through Qing AI, and preview and confirm permission-controlled business actions.',
}
