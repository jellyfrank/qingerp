import logging
from collections import defaultdict

_logger = logging.getLogger(__name__)
PREVIOUS_VIEW_PARAMETER = 'qing_address_cn.previous_address_view_id'


def convert_legacy_addresses(env):
    """Convert only unambiguous native county references and preserve postal/detail data."""
    District = env['qing.address.cn.district'].with_context(lang='en_US')
    Partner = env['res.partner'].with_context(active_test=False, lang='en_US')
    candidates = defaultdict(set)
    districts = District.search([])
    for district in districts:
        state_id = district.state_id.id
        candidates[(state_id, district.name)].add(district.id)
        candidates[(state_id, district.city_id.name + district.name)].add(district.id)
    identifiers = env['ir.model.data'].search([('module', '=', 'l10n_cn'), ('model', '=', 'res.city')])
    legacy_cities = env['res.city'].with_context(lang='en_US').browse(identifiers.mapped('res_id')).exists()
    legacy_cities = legacy_cities.filtered(lambda city: not city.qing_cn_code and city.country_id.code == 'CN')
    converted = 0
    unresolved = 0
    domain = [('country_id', '=', env.ref('base.cn').id), ('city_id', 'in', legacy_cities.ids)]
    for city, partners in Partner.search(domain).grouped('city_id').items():
        ids = candidates.get((city.state_id.id, city.name), set())
        if len(ids) != 1:
            unresolved += len(partners)
            continue
        matching = partners.filtered(lambda partner: not partner.state_id or partner.state_id == city.state_id)
        unresolved += len(partners - matching)
        district = District.browse(next(iter(ids)))
        matching.write({'city_id': district.city_id.id, 'qing_cn_district_id': district.id})
        converted += len(matching)
    _logger.info('China address conversion: %s contacts converted, %s legacy contacts retained for manual review.', converted, unresolved)
    return {'converted': converted, 'unresolved': unresolved}


def post_init_hook(env):
    country = env.ref('base.cn')
    view = env.ref('qing_address_cn.view_china_address_form')
    parameters = env['ir.config_parameter'].sudo()
    if country.address_view_id != view:
        parameters.set_str(PREVIOUS_VIEW_PARAMETER, str(country.address_view_id.id or 0))
        country.address_view_id = view
    convert_legacy_addresses(env)


def uninstall_hook(env):
    country = env.ref('base.cn')
    view = env.ref('qing_address_cn.view_china_address_form', raise_if_not_found=False)
    parameters = env['ir.config_parameter'].sudo()
    if view and country.address_view_id == view:
        previous_id = parameters.get_str(PREVIOUS_VIEW_PARAMETER, default='0')
        previous = env['ir.ui.view'].browse(int(previous_id)) if previous_id.isdigit() else env['ir.ui.view']
        country.address_view_id = previous.exists()
    parameter = parameters.search([('key', '=', PREVIOUS_VIEW_PARAMETER)])
    parameter.unlink()
