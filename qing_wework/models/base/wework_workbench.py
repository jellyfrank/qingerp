#!/usr/bin/python3
# @Time    : 2023-08-08
# @Author  : Kevin Kong (kfx2007@163.com)

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.safe_eval import safe_eval
from ...tools.date_helpers import FormulaDateUtil
import logging

_logger = logging.getLogger(__name__)


ITEM_HEIGHS = [
    ('single_row', 'Single Row'),
    ('double_row', 'Double Row')
]

TYPES = [
    ('keydata', 'Key Data'),
    ('image', 'Image'),
    ('list', 'List'),
    ('webview', 'WebView')
]


class wework_workbench(models.Model):
    _name = "wework.workbench"
    _description = "WorkBench"
    _inherit =  ['mail.thread','mail.activity.mixin']

    @api.constrains("items")
    def _check_items_count(self):
        """
            check the count of items.

            keydata: 4
            image:1
            list:3
            webview:1
        """
        count = len(self.items)
        if self.type == "keydata" and count > 4:
            raise UserError(
                _("The max count of items is 4 when type is keydata."))
        if self.type == "list" and count > 3:
            raise UserError(
                _("The max count of items is 3 when type is list."))
        if self.type in ["image", "webview"] and count > 1:
            raise UserError(
                _("The max count of items is 1 when type is image or webview."))

    def _compute_data_result(self, codes):
        """
        Compute data codes result.
        """
        ctx = {
            "self": self,
            "dateutil": FormulaDateUtil
        }
        safe_eval(codes, ctx, mode="exec")
        result = ctx.get("result")
        return result

    def _build_workbench_data(self):
        """
            build workbench data.
        """
        self.ensure_one()
        key, data = self.type, {}
        if self.type == "keydata":
            data['items'] = [{
                "key": item.name,
                "data": self._compute_data_result(item.data),
                "jump_url":  item.redirect_url,
                "pagepath": item.pagepath if item.pagepath else ""
            } for item in self.items]

        if self.type == "image":
            item = self.items[0]
            data.update({
                "url": item.url,
                "jump_url": item.redirect_url,
                "pagepath": item.pagepath if item.pagepath else ""
            })

        if self.type == "list":
            key = "template_list"
            data['items'] = [{
                "title": item.title,
                "jump_url": item.redirect_url,
                "pagepath": item.pagepath if item.pagepath else ""
            } for item in self.items]

        if self.type == "webview":
            item = self.items[0]
            data.update({
                "url": item.url,
                "jump_url": item.redirect_url,
                "pagepath": item.pagepath if item.pagepath else "",
                "height": item.height,
                "hide_title": item.hide_title,
                "enable_webview_click": item.enable_webview_click
            })
        return {key: data}

    items = fields.One2many("wework.workbench.item",
                            "bench_id", string="WorkBench Items")
    name = fields.Char("Name")
    type = fields.Selection(TYPES, string="Type")

    def publish(self):
        """
        Publish the workbench
        """
        data = self._build_workbench_data()
        agent_id = self.env.company.wework_id.agent_id
        res = self.env.company.weworkclient.workbench.set_template(
            self.type, agent_id, **data)
        _logger.info(f"[Wework]Set Workbench Template Result:{res}")


class wework_workbench_item(models.Model):
    _name = "wework.workbench.item"
    _description = "WorkBench Item"

    @api.depends("need_authorized","jump_url")
    def _compute_redirect_url(self):
        """
        compute real redirect url.
        """
        for item in self:
            url = item.bench_id._compute_data_result(item.jump_url) if item.jump_url else ""
            if item.need_authorized:
                item.redirect_url = self.env.company.wework_id.get_authorized_url(url)
            else:
                item.redirect_url = url


    bench_id = fields.Many2one("wework.workbench", string="Bench Id")
    data = fields.Text("Data")
    enable_webview_click = fields.Boolean(
        "Enable Webview Redirect", default=False)
    name = fields.Char("Name")
    need_authorized = fields.Boolean("Need Authorized", default=False)
    hide_title = fields.Boolean("Hide title", default=False)
    height = fields.Selection(ITEM_HEIGHS, string="Height")
    jump_url = fields.Char("Redirect URL")
    pagepath = fields.Char("Miniprogram Page Path")
    redirect_url = fields.Char("Real Redirect URL",compute="_compute_redirect_url")
    title = fields.Char("Title")
    type = fields.Selection(string="Type", related="bench_id.type")
    url = fields.Char("URL")
