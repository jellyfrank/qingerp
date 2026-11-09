from odoo.exceptions import AccessError

from odoo.addons.qing_api.api import ApiError


class ProductApiService:
    def __init__(self, env):
        self.env = env

    def _check_user(self):
        if not self.env.user._is_internal():
            raise AccessError(self.env._("An internal integration user is required."))

    def _get(self, model_name, prefix, public_id, *, field=None):
        Model = self.env[model_name].with_context(active_test=False)
        domain = [("qing_api_uid", "=", public_id.removeprefix(prefix))]
        record = Model.search(domain, limit=1)
        if record:
            return record
        if field:
            self._invalid(field, "The referenced resource was not found.")
        raise ApiError("RESOURCE_NOT_FOUND", "Resource not found.", 404)

    @staticmethod
    def _public_id(prefix, record):
        return prefix + record.qing_api_uid

    @staticmethod
    def _invalid(field, message):
        raise ApiError(
            "VALIDATION_ERROR", message, 422,
            [{"field": "body.%s" % field, "code": "INVALID_VALUE", "message": message}],
        )

    @staticmethod
    def _require_values(values):
        if not values:
            raise ApiError("VALIDATION_ERROR", "At least one field must be provided.", 422)

    @staticmethod
    def _name(value):
        name = value.strip()
        if not name:
            ProductApiService._invalid("name", "Name cannot be empty.")
        return name
