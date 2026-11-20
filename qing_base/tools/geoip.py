"""Read IP location from the local Odoo GeoIP database."""
from ipaddress import ip_address
from pathlib import Path

from geoip2.errors import GeoIP2Error
from maxminddb import InvalidDatabaseError

from odoo.http.geoip import _geoip_city_db
from odoo.tools import config


_DEFAULT_CITY_DATABASE = '/usr/share/GeoIP/GeoLite2-City.mmdb'
_DBIP_CITY_DATABASE = 'dbip-city.mmdb'


def configure_local_city_database():
    """Use an operator-provisioned city database on a dedicated instance."""
    city_database = config['geoip_city_db']
    if city_database != _DEFAULT_CITY_DATABASE or Path(city_database).is_file():
        return
    databases = config['db_name']
    if len(databases) != 1 or Path(databases[0]).name != databases[0]:
        return
    path = Path(config['data_dir']) / 'geoip' / databases[0] / _DBIP_CITY_DATABASE
    if path.is_file():
        config['geoip_city_db'] = str(path)
        _geoip_city_db.cache_clear()


def uses_dbip_city_database():
    return Path(config['geoip_city_db']).name == _DBIP_CITY_DATABASE


def request_ip_location(http_request):
    """Return the request's IP and available country/city names."""
    remote_addr = http_request.httprequest.remote_addr
    if not remote_addr:
        return None, None, None
    try:
        address = ip_address(remote_addr)
    except ValueError:
        return None, None, None
    if not address.is_global or address.is_multicast:
        return remote_addr, None, None

    try:
        geoip = http_request.geoip
        return remote_addr, geoip.country.name, geoip.city.name
    except (GeoIP2Error, InvalidDatabaseError, OSError, ValueError):
        # Location data is optional; lookup failure must not interrupt logging.
        return remote_addr, None, None
