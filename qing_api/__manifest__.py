{
    "name": "Qing API",
    "summary": "Stable OpenAPI foundation for QingERP integrations",
    "version": '20.0.1.9',
    "category": "Technical",
    "author": "Qingdao Ohm Network Technology Co., Ltd.",
    "website": "https://www.qingsolution.com/",
    "support": "kevin@odoomommy.com",
    "license": "LGPL-3",
    "depends": ["qing_base", "portal"],
    "data": [
        "data/res_groups.xml",
        "views/view_res_config_settings_form.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "qing_api/static/src/xml/portal_security.xml",
        ],
    },
    "application": False,
    "installable": True,
    'description': 'Provide controlled business endpoints, Bearer authentication, OpenAPI specifications and online API documentation.',
}
