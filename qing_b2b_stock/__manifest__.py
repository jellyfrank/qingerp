{
    "name": "Qing B2B Inventory",
    "summary": "Warehouse availability, confirmation warnings and shipment status for mobile orders",
    "version": "20.0.1.0.2",
    "category": "Sales",
    "author": "Qingdao Ohm Network Technology Co., Ltd.",
    "website": "https://www.qingsolution.com/",
    "license": "LGPL-3",
    "depends": ["qing_b2b_sale", "website_sale_stock"],
    "auto_install": True,
    "data": [
        "security/ir.access.csv",
        "views/view_website_form.xml",
        "views/view_sale_order_form.xml",
        "wizard/view_b2b_shortage_confirmation.xml",
    ],
}
