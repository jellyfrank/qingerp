from odoo import fields, models


DOCUMENTATION_UI_SELECTION = [
    ("scalar", "Scalar"),
    ("swagger", "Swagger UI"),
    ("redoc", "ReDoc"),
]
DOCUMENTATION_ACCESS_SELECTION = [
    ("authenticated", "Signed-in users"),
    ("public", "Public"),
]


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    qing_api_documentation_access = fields.Selection(
        selection=DOCUMENTATION_ACCESS_SELECTION,
        string="Documentation Access",
        config_parameter="qing_api.documentation_access",
        default="authenticated",
        required=True,
    )
    qing_api_documentation_ui = fields.Selection(
        selection=DOCUMENTATION_UI_SELECTION,
        string="Documentation Interface",
        config_parameter="qing_api.documentation_ui",
        default="scalar",
        required=True,
    )
