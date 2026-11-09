from odoo import models
from odoo.exceptions import AccessError


class ResUsers(models.Model):
    _inherit = 'res.users'

    def _qing_check_sale_team(self, team_id):
        if not self._qing_sales_team_domain():
            return
        team = self.env['crm.team'].browse(team_id)
        if not team or (team.user_id != self and self not in team.member_ids):
            raise AccessError(self.env._('Assign sales documents only to a team you belong to.'))

    def _qing_team_member_domain(self, prefix='team_id'):
        self.ensure_one()
        return ['|', (prefix + '.user_id', '=', self.id),
                (prefix + '.member_ids', 'in', [self.id])]

    def _qing_sales_team_domain(self, invoices=False, prefix='team_id'):
        """Restrict business records without granting accounting privileges."""
        self.ensure_one()
        if not self.has_group('qing_sale.group_sale_team_restricted'):
            return []
        if any(self.has_group(group) for group in (
            'base.group_system', 'account.group_account_user', 'account.group_account_manager',
        )):
            return []
        domain = self._qing_team_member_domain(prefix)
        if invoices:
            move_type = prefix.removesuffix('team_id') + 'move_type'
            domain = ['|', (move_type, 'not in', ('out_invoice', 'out_refund')), *domain]
        return domain
