from odoo import http
from odoo.http import request

from odoo.addons.qing_api.api import (
    ApiResponse,
    ApiSchema,
    Boolean,
    Integer,
    List,
    Object,
    String,
    api_route,
)

from ..services.partner_service import PartnerService


class PartnerPath(ApiSchema):
    partner_id = String(
        required=True,
        max_length=36,
        pattern=r'^par_[0-9a-f]{32}$',
    )


class PartnerListQuery(ApiSchema):
    limit = Integer(default=50, minimum=1, maximum=200)
    offset = Integer(default=0, minimum=0, maximum=1000000)
    search = String(max_length=200)


class PartnerWriteFields(ApiSchema):
    name = String(min_length=1, max_length=256)
    is_company = Boolean()
    email = String(max_length=240, nullable=True)
    phone = String(max_length=128, nullable=True)
    mobile = String(max_length=128, nullable=True)
    website = String(max_length=256, nullable=True)
    vat = String(max_length=64, nullable=True)
    street = String(max_length=256, nullable=True)
    street2 = String(max_length=256, nullable=True)
    zip = String(max_length=24, nullable=True)
    city = String(max_length=128, nullable=True)
    country_code = String(max_length=2, nullable=True)
    state_code = String(max_length=16, nullable=True)


class PartnerCreateRequest(PartnerWriteFields):
    name = String(required=True, min_length=1, max_length=256)
    is_company = Boolean(default=False)


class PartnerResource(ApiSchema):
    id = String(required=True)
    name = String(required=True)
    is_company = Boolean(required=True)
    email = String(required=True, nullable=True)
    phone = String(required=True, nullable=True)
    mobile = String(required=True, nullable=True)
    website = String(required=True, nullable=True)
    vat = String(required=True, nullable=True)
    street = String(required=True, nullable=True)
    street2 = String(required=True, nullable=True)
    zip = String(required=True, nullable=True)
    city = String(required=True, nullable=True)
    country_code = String(required=True, nullable=True)
    state_code = String(required=True, nullable=True)


class PartnerListResponse(ApiSchema):
    items = List(Object(PartnerResource), required=True)
    limit = Integer(required=True)
    offset = Integer(required=True)
    total = Integer(required=True)


class PartnerDeleteResponse(ApiSchema):
    id = String(required=True)
    deleted = Boolean(required=True)


class PartnerController(http.Controller):
    @api_route(
        '/api/v1/partners',
        methods=['GET'],
        operation_id='listPartners',
        tags=['Partners'],
        summary='List accessible contacts',
        description='Returns contacts visible to the API user, with optional text search and pagination.',
        query_model=PartnerListQuery,
        response_model=PartnerListResponse,
        errors=(401, 403, 422, 500),
        readonly=True,
    )
    def list_partners(self, query):
        return PartnerService(request.env).list(query)

    @api_route(
        '/api/v1/partners',
        methods=['POST'],
        operation_id='createPartner',
        tags=['Partners'],
        summary='Create a contact',
        description=(
            'Requires Qing Partner editor or manager access. The request is also subject '
            'to contact record rules.'
        ),
        request_model=PartnerCreateRequest,
        response_model=PartnerResource,
        response_status=201,
        errors=(401, 403, 409, 415, 422, 500),
    )
    def create_partner(self, data):
        result = PartnerService(request.env).create(data)
        return ApiResponse(
            result,
            201,
            {'Location': '/api/v1/partners/%s' % result['id']},
        )

    @api_route(
        '/api/v1/partners/<string:partner_id>',
        methods=['GET'],
        operation_id='getPartner',
        tags=['Partners'],
        summary='Get a contact',
        path_model=PartnerPath,
        response_model=PartnerResource,
        errors=(401, 403, 404, 500),
        readonly=True,
    )
    def get_partner(self, partner_id):
        return PartnerService(request.env).get(partner_id)

    @api_route(
        '/api/v1/partners/<string:partner_id>',
        methods=['PATCH'],
        operation_id='updatePartner',
        tags=['Partners'],
        summary='Update a contact',
        description=(
            'Requires Qing Partner editor or manager access. Only fields allowed by the '
            'user access rules can be changed.'
        ),
        path_model=PartnerPath,
        request_model=PartnerWriteFields,
        response_model=PartnerResource,
        errors=(401, 403, 404, 409, 415, 422, 500),
    )
    def update_partner(self, partner_id, data):
        return PartnerService(request.env).update(partner_id, data)

    @api_route(
        '/api/v1/partners/<string:partner_id>',
        methods=['DELETE'],
        operation_id='deletePartner',
        tags=['Partners'],
        summary='Delete a contact',
        description='Deletes a contact if Odoo access rules and Qing Partner manager permissions allow it.',
        path_model=PartnerPath,
        response_model=PartnerDeleteResponse,
        errors=(401, 403, 404, 409, 500),
    )
    def delete_partner(self, partner_id):
        return PartnerService(request.env).delete(partner_id)
