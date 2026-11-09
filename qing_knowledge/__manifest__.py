{
    'name': 'Qing Knowledge',
    'summary': 'Private Markdown knowledge spaces with review and revisions',
    'version': '20.0.1.0.15',
    'category': 'Productivity',
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'support': 'kevin@odoomommy.com',
    'license': 'LGPL-3',
    'depends': ['qing_base', 'web'],
    'external_dependencies': {'python': ['markdown_it', 'jieba']},
    'data': [
        'security/ir.access.csv',
        'views/view_knowledge_space.xml',
        'views/view_knowledge_member_wizard.xml',
        'views/view_knowledge_article.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'qing_knowledge/static/src/knowledge_action.js',
            'qing_knowledge/static/src/knowledge_action.xml',
            'qing_knowledge/static/src/knowledge.scss',
        ],
        'qing_knowledge.editor': [
            'qing_knowledge/static/lib/markdown_editors.js',
            'qing_knowledge/static/lib/markdown_editors.css',
        ],
    },
    'application': True,
    'installable': True,
    'description': 'Manage private knowledge spaces, articles, reviews, revision history and controlled attachments.',
}
