from decimal import Decimal

from odoo.addons.qing_api.api import ApiError

from .common import ProductApiService


class UnitService(ProductApiService):
    PREFIX = "uom_"

    def __init__(self, env):
        super().__init__(env)
        self.Units = env["uom.uom"]

    def create(self, data):
        self._check_user()
        reference = self._reference(data["category_id"])
        factor = self._factor(data["factor"])
        unit = self.Units.create({
            "name": self._name(data["name"]),
            "relative_uom_id": reference.id,
            "relative_factor": factor,
        })
        return self._serialize(unit)

    def update(self, unit_id, data):
        self._check_user()
        unit = self._unit(unit_id)
        values = {}
        if "name" in data:
            values["name"] = self._name(data["name"])
        if "category_id" in data:
            if "factor" not in data:
                self._invalid("factor", "Factor is required when changing the unit category.")
            reference = self._reference(data["category_id"])
            values["relative_uom_id"] = reference.id
        if "factor" in data:
            factor = self._factor(data["factor"])
            parent = (
                self.Units.browse(values["relative_uom_id"])
                if "relative_uom_id" in values else unit.relative_uom_id
            )
            values["relative_factor"] = factor / parent.factor
        self._require_values(values)
        unit.write(values)
        return self._serialize(unit)

    def delete(self, unit_id):
        self._check_user()
        unit = self._unit(unit_id)
        unit.check_access("unlink")
        child_domain = [("relative_uom_id", "=", unit.id)]
        if self.Units.sudo().with_context(active_test=False).search(child_domain, limit=1):
            raise ApiError("CONFLICT", "Unit has related units.", 409)
        public_id = self._public_id(self.PREFIX, unit)
        unit.unlink()
        return {"id": public_id, "deleted": True}

    def _unit(self, unit_id):
        unit = self._get("uom.uom", self.PREFIX, unit_id)
        if not unit.relative_uom_id:
            raise ApiError("RESOURCE_NOT_FOUND", "Unit not found.", 404)
        return unit

    def _reference(self, category_id):
        reference = self._get("uom.uom", "uc_", category_id, field="category_id")
        if reference.relative_uom_id:
            self._invalid("category_id", "A reference unit category is required.")
        return reference

    @staticmethod
    def _factor(value):
        factor = Decimal(value)
        if factor <= 0:
            ProductApiService._invalid("factor", "Factor must be greater than zero.")
        return float(factor)

    def _serialize(self, unit):
        reference = unit
        while reference.relative_uom_id:
            reference = reference.relative_uom_id
        return {
            "id": self._public_id(self.PREFIX, unit),
            "name": unit.name,
            "category_id": self._public_id("uc_", reference),
            "factor": format(Decimal(str(unit.factor)), "f"),
        }
