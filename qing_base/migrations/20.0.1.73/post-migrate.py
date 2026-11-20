from hashlib import sha256

from odoo import SUPERUSER_ID, api

from odoo.addons.qing_base.hooks import initialize_assistant_brand


PREVIOUS_DEFAULT_AVATAR_SHA256 = 'a880541842e36770cd76ae4b39eb19ffa3dfc994f701225a5296f9c0cf51ba78'


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    assistant = env.ref('base.partner_root')
    if assistant.name != 'Qing Assistant' or not assistant.image_1920:
        return
    if sha256(assistant.image_1920.content).hexdigest() == PREVIOUS_DEFAULT_AVATAR_SHA256:
        initialize_assistant_brand(env)
