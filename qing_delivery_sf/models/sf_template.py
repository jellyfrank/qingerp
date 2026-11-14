from odoo import fields, models


class SfTemplate(models.Model):
    _name = 'qing.delivery.sf.template'
    _description = 'SF Express Label Template'
    _order = 'name, id'

    _code_unique = models.Constraint('UNIQUE(code)', 'An SF Express template code must be unique.')

    code = fields.Char(string='Template Code', required=True)
    custom = fields.Boolean(string='Custom Template', help='Synchronized from the SF account.')
    name = fields.Char(string='Name', required=True, translate=True)
