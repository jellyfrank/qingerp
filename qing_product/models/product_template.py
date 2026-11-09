from uuid import uuid4

from odoo import api, fields, models
from odoo.exceptions import AccessError


class ProductTemplate(models.Model):
    _inherit = "product.template"

    _qing_api_uid_unique = models.Constraint(
        "UNIQUE(qing_api_uid)",
        "A product API identifier must be unique.",
    )

    qing_api_uid = fields.Char(copy=False, index=True, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if "qing_api_uid" in values and not self.env.su:
                raise AccessError(self.env._("The API identifier cannot be specified."))
            values.setdefault("qing_api_uid", uuid4().hex)
        return super().create(vals_list)

    def write(self, values):
        if "qing_api_uid" in values and not self.env.su:
            raise AccessError(self.env._("The API identifier cannot be changed."))
        return super().write(values)
