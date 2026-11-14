#!/usr/bin/python3
# @Time    : 2025-08-14
# @Author  : Kevin Kong (kfx2007@163.com)

from odoo import models, fields, api, _

class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    is_mobile = fields.Boolean(string='Is Mobile', default=False)
