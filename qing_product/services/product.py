from decimal import Decimal

from .common import ProductApiService


class ProductService(ProductApiService):
    PREFIX = "prd_"

    def __init__(self, env):
        super().__init__(env)
        self.Products = env["product.template"]

    def create(self, data):
        self._check_user()
        product = self.Products.create(self._values(data))
        return self._serialize(product)

    def update(self, product_id, data):
        self._check_user()
        product = self._get("product.template", self.PREFIX, product_id)
        values = self._values(data)
        self._require_values(values)
        product.write(values)
        return self._serialize(product)

    def delete(self, product_id):
        self._check_user()
        product = self._get("product.template", self.PREFIX, product_id)
        public_id = self._public_id(self.PREFIX, product)
        product.unlink()
        return {"id": public_id, "deleted": True}

    def _values(self, data):
        values = {}
        for field in ("active", "barcode", "default_code", "purchase_ok", "sale_ok", "type"):
            if field in data:
                values[field] = data[field] if data[field] is not None else False
        if "name" in data:
            values["name"] = self._name(data["name"])
        if "list_price" in data:
            values["list_price"] = float(Decimal(data["list_price"]))
        if "category_id" in data:
            category_id = data["category_id"]
            category = (
                self._get("product.category", "pcat_", category_id, field="category_id")
                if category_id else None
            )
            values["categ_id"] = category.id if category else False
        if "unit_id" in data:
            unit = self._get("uom.uom", "uom_", data["unit_id"], field="unit_id")
            values["uom_id"] = unit.id
        return values

    def _serialize(self, product):
        return {
            "id": self._public_id(self.PREFIX, product),
            "name": product.name,
            "active": bool(product.active),
            "barcode": product.barcode or None,
            "default_code": product.default_code or None,
            "category_id": self._public_id("pcat_", product.categ_id) if product.categ_id else None,
            "unit_id": self._public_id("uom_", product.uom_id),
            "type": product.type,
            "list_price": format(Decimal(str(product.list_price)), "f"),
            "sale_ok": bool(product.sale_ok),
            "purchase_ok": bool(product.purchase_ok),
        }
