#!/usr/bin/python3
# @Time    : 2021-06-17
# @Author  : Kevin Kong (kfx2007@163.com)

from odoo import api, fields, models, _
from wechatpy.work.client import WeChatClient as WeworkClient
from odoo.exceptions import UserError

class res_company(models.Model):

    _inherit = "res.company"

    wework_id = fields.Many2one("qing.wework", string="WeWork App")

    @property
    def weworkclient(self):
        """return wework client"""
        self.ensure_one()
        if not self.wework_id:
            raise UserError(_("Current company has't config the WeWork settings, please go to company settings."))
        return WeworkClient(self.wework_id.company_id, self.wework_id.secret)
