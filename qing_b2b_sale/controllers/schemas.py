from odoo.addons.qing_api.api import ApiSchema, Boolean, Integer, List, Number, Object, String, UUID


class PageQuery(ApiSchema):
    search = String(max_length=100)
    offset = Integer(default=0, minimum=0, maximum=100000)
    limit = Integer(default=20, minimum=1, maximum=40)


class CatalogQuery(PageQuery):
    category = Integer(minimum=1)


class Identifier(ApiSchema):
    id = Integer(required=True, minimum=1)


class ItemName(ApiSchema):
    id = Integer(required=True)
    name = String(required=True)


class CategoriesResponse(ApiSchema):
    items = List(Object(ItemName), required=True)


class Stock(ApiSchema):
    quantity = Number()
    available = Boolean()


class ProductUnit(ItemName):
    price = Number(required=True)


class CatalogItem(ItemName):
    image = String(required=True)
    units = List(Object(ProductUnit), required=True)
    stock = Object(Stock, required=True)


class ProductDetail(CatalogItem):
    currency = String(required=True)
    description = String(required=True)
    reference = String(required=True)


class CatalogResponse(ApiSchema):
    items = List(Object(CatalogItem), required=True)
    currency = String(required=True)
    has_more = Boolean(required=True)


class CartLine(ApiSchema):
    product = Integer(required=True, minimum=1)
    unit = Integer(required=True, minimum=1)
    quantity = Integer(required=True, minimum=1, maximum=100000)


class CartRequest(ApiSchema):
    lines = List(Object(CartLine), required=True, min_items=1, max_items=150)
    address = Integer(minimum=1)
    note = String(max_length=2000)


class QuotationRequest(CartRequest):
    preview = String(required=True, min_length=1, max_length=30000)
    request_id = UUID(required=True)
    accept_backorder = Boolean(default=False)


class MoneyLine(ApiSchema):
    product = Integer(required=True)
    unit = Integer(required=True)
    name = String(required=True)
    unit_name = String(required=True)
    quantity = Number(required=True)
    unit_price = Number(required=True)
    discount = Number(required=True)
    subtotal = Number(required=True)
    tax = Number(required=True)
    total = Number(required=True)
    delivered = Number()


class Notice(ApiSchema):
    code = String(required=True)
    field = String(required=True)
    message = String(required=True)


class CheckoutResponse(ApiSchema):
    currency = String(required=True)
    untaxed = Number(required=True)
    tax = Number(required=True)
    total = Number(required=True)
    lines = List(Object(MoneyLine), required=True)
    notices = List(Object(Notice), required=True)
    preview = String(required=True)


class OrderSummary(ItemName):
    state = String(required=True)
    delivery = String()
    date = String(required=True)
    total = Number(required=True)
    currency = String(required=True)


class OrderDetail(OrderSummary):
    lines = List(Object(MoneyLine), required=True)


class OrdersResponse(ApiSchema):
    items = List(Object(OrderSummary), required=True)
    has_more = Boolean(required=True)


class Address(ItemName):
    display = String(required=True)


class AddressesResponse(ApiSchema):
    items = List(Object(Address), required=True)


class AddressInput(ApiSchema):
    name = String(required=True, min_length=1, max_length=180)
    street = String(required=True, min_length=1, max_length=200)
    city = String(required=True, min_length=1, max_length=100)
    country = String(required=True, pattern=r"^[A-Z]{2}$")
    state = Integer(minimum=1)
    zip = String(max_length=20)


class Country(ApiSchema):
    code = String(required=True)
    name = String(required=True)
    state_required = Boolean(required=True)
    zip_required = Boolean(required=True)


class CountriesResponse(ApiSchema):
    items = List(Object(Country), required=True)


class CountryQuery(ApiSchema):
    country = String(required=True, pattern=r"^[A-Z]{2}$")


class RepeatLine(CartLine):
    name = String(required=True)
    unit_name = String(required=True)
    quantity = Number(required=True)


class RepeatResponse(ApiSchema):
    lines = List(Object(RepeatLine), required=True)
    notices = List(Object(Notice), required=True)


class DocumentResponse(ApiSchema):
    name = String(required=True)
    pdf = String(required=True)
