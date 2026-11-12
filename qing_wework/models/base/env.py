#!/usr/bin/python3
# @Time    : 2021-06-17
# @Author  : Kevin Kong (kfx2007@163.com)

from odoo.orm.environments import Environment


def _get_weworkclient(self):
    """Return the client for the environment's current company."""
    return self.company.sudo().weworkclient

Environment.weworkclient = property(_get_weworkclient)
