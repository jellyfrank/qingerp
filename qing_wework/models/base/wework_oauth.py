#!/usr/bin/python3
# @Time    : 2022-09-07
# @Author  : Kevin Kong (kfx2007@163.com)

from odoo import api, fields, models, _
import secrets
import string

_CODE_ALPHABET = string.ascii_letters + string.digits


def _random_code(length=24):
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))


STATES = [
    ('draft','Draft'),
    ('done','Used')
]


class wework_temp_state(models.TransientModel):
    _name = "wework.temp.state"
    _description = "wework temporary state"

    name = fields.Char("Code", default=lambda self: _random_code())
    state = fields.Selection(STATES,string="state", default='draft')

    @api.model
    def get_state(self):
        """get provider state"""
        state = self.sudo().create({})
        return state.name

    @api.model
    def validate(self, state):
        """
        validate the state

        Return: boolean. False if state was used. True if state is new.
        """
        state = self.sudo().search([('state','=','draft'),('name','=', state)],limit=1)
        return True if state else False


class auth_oauth_provider(models.Model):
    _inherit="auth.oauth.provider"

    corp_id =fields.Char("Corp ID")