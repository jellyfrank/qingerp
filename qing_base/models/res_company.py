"""Company login IP whitelist."""
import ipaddress

from odoo import fields, models

from ..tools import mommy_base_loaded


def _parse_whitelist(text):
    """Return rules, one per line; ``#`` starts a comment."""
    rules = (line.split('#', 1)[0].strip() for line in (text or '').splitlines())
    return [rule for rule in rules if rule]


def _matches_rule(client_ip, rule):
    if '/' in rule:
        return client_ip in ipaddress.ip_network(rule, strict=False)
    if '-' in rule:
        start, end = (ipaddress.ip_address(part.strip()) for part in rule.split('-', 1))
        if not client_ip.version == start.version == end.version:
            return False
        low, high = sorted((int(start), int(end)))
        return low <= int(client_ip) <= high
    return client_ip == ipaddress.ip_address(rule)


class ResCompany(models.Model):
    _inherit = 'res.company'

    def _qing_login_ip_matches(self, ip_address):
        self.ensure_one()
        try:
            client_ip = ipaddress.ip_address(ip_address or '')
        except ValueError:
            return False
        for rule in _parse_whitelist(self.qing_login_ip_whitelist):
            try:
                if _matches_rule(client_ip, rule):
                    return True
            except ValueError:
                continue
        return False

    def _qing_is_login_ip_allowed(self, user, ip_address):
        """Apply this company's whitelist to ``user``; an enabled empty list blocks everyone."""
        self.ensure_one()
        if mommy_base_loaded(self.env) or not self.qing_login_ip_whitelist_enabled:
            return True
        if self.qing_login_ip_whitelist_exclude_portal and user._is_portal():
            return True
        groups = self.qing_login_ip_whitelist_group_ids
        if groups and not (user.all_group_ids & groups):
            return True
        return self._qing_login_ip_matches(ip_address)

    qing_login_ip_whitelist = fields.Text(
        string='Login IP Whitelist',
        help='One IP address, CIDR subnet or range (start-end) per line. Text after # is a comment.',
    )
    qing_login_ip_whitelist_enabled = fields.Boolean(string='Restrict Sign-in by IP')
    qing_login_ip_whitelist_exclude_portal = fields.Boolean(
        string='Exclude Portal Users', default=True,
    )
    qing_login_ip_whitelist_group_ids = fields.Many2many(
        'res.groups', 'res_company_qing_login_ip_group_rel', 'company_id', 'group_id',
        string='Restricted Groups',
        help='Only members of these groups are restricted. Leave empty to restrict every user.',
    )
