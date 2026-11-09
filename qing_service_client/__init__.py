from . import models


def post_init_hook(env):
    env['qing.service.instance'].sudo()._local_instance()
