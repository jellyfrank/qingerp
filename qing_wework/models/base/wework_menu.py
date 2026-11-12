#!/usr/bin/python3
# @Time    : 2023-08-07
# @Author  : Kevin Kong (kfx2007@163.com)

from odoo import api, fields, models, _
from odoo.exceptions import AccessError
import logging

_logger = logging.getLogger(__name__)


TYPES = [
    ('click', 'Click'),
    ('view', "View"),
    ('scancode_push', 'Scancode Push'),
    ('scancode_waitmsg', 'Scancode Waitmsg'),
    ('pic_sysphoto', 'Pic Sysphoto'),
    ('pic_photo_or_album', 'Pic Photo or Album'),
    ('pic_weixin', 'Pic WeiXin'),
    ('location_select', 'Location Select'),
    ('view_miniprogram', 'View Miniprogram')
]


class wework_menu(models.Model):
    _name = "wework.menu"
    _description = "Wework Menu"
    _parent_store = True
    _inherit = ['mail.thread', 'mail.activity.mixin']

    def _build_menu_data(self):
        """
        Build menu data
        """
        data = []
        for menu in self:
            item = {
                "type": menu.type,
                "name": menu.name,
            }
            if menu.child_ids:
                sub_items = menu.child_ids._build_menu_data()
                item['sub_button'] = sub_items
            else:
                if menu.key:
                    item['key'] = menu.key
                if menu.url:
                    item['url'] = self.env.company.wework_id.get_authorized_url(
                        menu.url) if menu.need_authorized else menu.url
                if menu.pagepath:
                    item['pagepath'] = menu.pagepath
            data.append(item)
        return data

    @api.constrains("child_ids")
    def _check_child_ids(self):
        """
        Sub menus count max is 5.
        """
        for menu in self:
            if len(menu.child_ids) > 5:
                raise AccessError(_("The sub menus max count is 5."))

    @api.model
    def _create_menus(self):
        """
        Create menus to wework.
        """
        menus = self.sudo().search([])
        # creating menus to wework.
        data = {'button': menus._build_menu_data()}
        if len(data['button']) > 3:
            raise AccessError(_("The max menu count is 3."))
        agent_id = self.env.company.wework_id.agent_id
        # if self.child_ids:
        _logger.debug(f"[Wework]Sync menus：{data}")
        res = self.env.company.weworkclient.menu.create(agent_id, data)
        if res['errcode'] == 0:
            _logger.info(_("Menus sync success."))

    appid = fields.Char("Miniprogram AppID")
    child_ids = fields.One2many(
        "wework.menu", 'parent_id', string="Child Menus")
    key = fields.Char("Key", size=128, required=True)
    need_authorized = fields.Boolean("Need Authorized", default=False)
    name = fields.Char("Name", size=16)
    parent_id = fields.Many2one("wework.menu", string="Parent Menu")
    parent_path = fields.Char(index=True)
    pagepath = fields.Char("Miniprogram Path")
    type = fields.Selection(TYPES, string="Type", required=True)
    url = fields.Char("URL", size=1024)

    @api.model_create_multi
    def create(self, vals):
        """
        Create menus
        """
        menus = super().create(vals)
        menus._create_menus()
        return menus

    def write(self, vals):
        """
        Update menus
        """
        agent_id = self.env.company.wework_id.agent_id
        res = super().write(vals)
        self.env.company.weworkclient.menu.delete(agent_id)
        self._create_menus()
        return res
