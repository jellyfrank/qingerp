from odoo.addons.qing_api.api import ApiError

from .common import ProductApiService


class UnitCategoryService(ProductApiService):
    PREFIX = "uc_"

    def __init__(self, env):
        super().__init__(env)
        self.Units = env["uom.uom"]

    def create(self, data):
        self._check_user()
        reference = self.Units.create({"name": self._name(data["name"]), "relative_factor": 1.0})
        return self._serialize(reference)

    def update(self, category_id, data):
        self._check_user()
        reference = self._reference(category_id)
        values = {"name": self._name(data["name"])} if "name" in data else {}
        self._require_values(values)
        reference.write(values)
        return self._serialize(reference)

    def delete(self, category_id):
        self._check_user()
        reference = self._reference(category_id)
        reference.check_access("unlink")
        child_domain = [("relative_uom_id", "=", reference.id)]
        if self.Units.sudo().with_context(active_test=False).search(child_domain, limit=1):
            raise ApiError("CONFLICT", "Unit category contains units.", 409)
        public_id = self._public_id(self.PREFIX, reference)
        reference.unlink()
        return {"id": public_id, "deleted": True}

    def _reference(self, category_id):
        reference = self._get("uom.uom", self.PREFIX, category_id)
        if reference.relative_uom_id:
            raise ApiError("RESOURCE_NOT_FOUND", "Unit category not found.", 404)
        return reference

    def _serialize(self, reference):
        return {
            "id": self._public_id(self.PREFIX, reference),
            "name": reference.name,
            "reference_unit_id": self._public_id("uom_", reference),
        }
