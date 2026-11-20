from . import controllers
from . import models


def post_init_hook(env):
    env['website']._qing_replace_stock_favicons()
