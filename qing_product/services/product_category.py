from odoo.addons.qing_api.api import ApiError

from .common import ProductApiService


class ProductCategoryService(ProductApiService):
    PREFIX = "pcat_"

    def __init__(self, env):
        super().__init__(env)
        self.Categories = env["product.category"]

    def create(self, data):
        self._check_user()
        values = self._values(data)
        category = self.Categories.create(values)
        return self._serialize(category)

    def update(self, category_id, data):
        self._check_user()
        category = self._get("product.category", self.PREFIX, category_id)
        values = self._values(data)
        self._require_values(values)
        category.write(values)
        return self._serialize(category)

    def delete(self, category_id):
        self._check_user()
        category = self._get("product.category", self.PREFIX, category_id)
        category.check_access("unlink")
        child_domain = [("parent_id", "=", category.id)]
        product_domain = [("categ_id", "=", category.id)]
        has_children = bool(self.Categories.sudo().with_context(active_test=False).search(child_domain, limit=1))
        Products = self.env["product.template"].sudo().with_context(active_test=False)
        has_products = bool(Products.search(product_domain, limit=1))
        if has_children or has_products:
            raise ApiError("CONFLICT", "Category has children or products.", 409)
        public_id = self._public_id(self.PREFIX, category)
        category.unlink()
        return {"id": public_id, "deleted": True}

    def _values(self, data):
        values = {}
        if "name" in data:
            values["name"] = self._name(data["name"])
        if "parent_id" in data:
            parent_id = data["parent_id"]
            parent = (
                self._get("product.category", self.PREFIX, parent_id, field="parent_id")
                if parent_id else self.Categories
            )
            values["parent_id"] = parent.id or False
        return values

    def _serialize(self, category):
        return {
            "id": self._public_id(self.PREFIX, category),
            "name": category.name,
            "parent_id": self._public_id(self.PREFIX, category.parent_id) if category.parent_id else None,
        }
