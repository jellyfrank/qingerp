from collections import defaultdict
from datetime import timedelta

from odoo import api, models
from odoo.exceptions import UserError

from .cash_flow_template import (
    BALANCE_HINTS, INFLOW_ROWS, PROFIT_HINTS, ROW_NAMES, TOTALS,
)


class CashFlowCalculator(models.AbstractModel):
    _name = 'qing.cash.flow.calculator'
    _description = 'Small Business Cash Flow Calculator'

    def _periods(self, report_date, mode):
        current_start = report_date.replace(month=1, day=1)
        if mode == 'annual':
            if report_date.month != 12 or report_date.day != 31:
                raise UserError(self.env._('Annual cash flow requires 31 December.'))
            prior_end = current_start - timedelta(days=1)
            return [(current_start, report_date), (prior_end.replace(month=1, day=1), prior_end)]
        if mode != 'monthly':
            raise UserError(self.env._('Select a supported reporting period.'))
        return [(current_start, report_date), (report_date.replace(day=1), report_date)]

    def _cash_balance(self, company, accounts, cutoff):
        if not accounts:
            return 0.0
        result = self.env['account.move.line']._read_group([
            ('company_id', '=', company.id), ('parent_state', '=', 'posted'),
            ('account_id', 'in', accounts.ids), ('date', '<=', cutoff),
        ], [], ['balance:sum'])
        return result[0][0] if result else 0.0

    def _classification_hints(self, scheme):
        balance = {line.account_id.id: int(line.line_code)
                   for line in scheme.balance_mapping_id.line_ids}
        profit = {line.account_id.id: int(line.line_code)
                  for line in scheme.profit_mapping_id.line_ids}
        return balance, profit

    def _hint(self, counterpart, cash_positive, balance, profit):
        pair = PROFIT_HINTS.get(profit.get(counterpart.account_id.id))
        if not pair:
            pair = BALANCE_HINTS.get(balance.get(counterpart.account_id.id))
        return pair[0 if cash_positive else 1] if pair else False

    def _reconciled_hints(self, counterpart, cash_positive, balance, profit,
                          cash_account_ids, currency):
        """Follow a matched clearing item once to its payment or invoice side."""
        allocations = []
        covered = 0.0
        matched = counterpart.matched_debit_ids | counterpart.matched_credit_ids
        for partial in matched:
            peer = (partial.debit_move_id if partial.credit_move_id == counterpart
                    else partial.credit_move_id)
            if peer.move_id == counterpart.move_id:
                continue
            fraction = min(partial.amount, abs(counterpart.balance) - covered)
            if currency.is_zero(fraction):
                continue
            covered += fraction
            other_lines = peer.move_id.line_ids.filtered(
                lambda line: line != peer and line.account_id.id not in cash_account_ids
            )
            candidates = [(self._hint(line, cash_positive, balance, profit), abs(line.balance))
                          for line in other_lines if not currency.is_zero(line.balance)]
            if candidates and all(code for code, _amount in candidates):
                total = sum(amount for _code, amount in candidates)
                allocations += [(code, fraction * amount / total)
                                for code, amount in candidates]
            elif not candidates and any(line.account_id.id in cash_account_ids
                                        for line in peer.move_id.line_ids):
                allocations.append(('transfer', fraction))
            else:
                allocations.append((False, fraction))
        return allocations, max(0.0, abs(counterpart.balance) - covered)

    def _automatic(self, cash_line, external_amount, balance, profit, cash_account_ids):
        opposite = cash_line.move_id.line_ids.filtered(
            lambda line: line.account_id.id not in cash_account_ids
            and line.balance * cash_line.balance < 0
        )
        total = sum(abs(line.balance) for line in opposite)
        if not total:
            return [], external_amount
        result = []
        unresolved = 0.0
        known_without_tax = {
            code for counterpart in opposite
            if balance.get(counterpart.account_id.id) != 36
            for code in [self._hint(counterpart, cash_line.balance > 0, balance, profit)]
            if code
        }
        for counterpart in opposite:
            portion = external_amount * abs(counterpart.balance) / total
            code = self._hint(counterpart, cash_line.balance > 0, balance, profit)
            if balance.get(counterpart.account_id.id) == 36 and len(known_without_tax) == 1:
                # Tax on the same sale/purchase belongs to that cash activity;
                # a standalone cash tax payment remains row 5.
                code = next(iter(known_without_tax))
            if code:
                signed = portion if (cash_line.balance > 0) == (code in INFLOW_ROWS) else -portion
                result.append((code, signed))
            else:
                indirect, remainder = self._reconciled_hints(
                    counterpart, cash_line.balance > 0, balance, profit,
                    cash_account_ids, cash_line.company_currency_id,
                )
                scale = portion / abs(counterpart.balance)
                unresolved += remainder * scale
                for indirect_code, amount in indirect:
                    if indirect_code == 'transfer':
                        continue
                    if not indirect_code:
                        unresolved += amount * scale
                        continue
                    signed = (amount * scale if
                              (cash_line.balance > 0) == (indirect_code in INFLOW_ROWS)
                              else -amount * scale)
                    result.append((indirect_code, signed))
        return result, unresolved

    def _classify(self, cash_line, external_amount, overrides, balance, profit,
                  currency, cash_account_ids):
        allocations = overrides.get(cash_line.id)
        if not allocations:
            return self._automatic(cash_line, external_amount, balance, profit, cash_account_ids)
        assigned = sum(allocations.mapped('amount'))
        if currency.compare_amounts(assigned, external_amount) > 0:
            return [], external_amount
        result = []
        for allocation in allocations:
            code = allocation.line_code if allocation.line_code == 'fx' else int(allocation.line_code)
            signed = (allocation.amount if cash_line.balance > 0 else -allocation.amount) if code == 'fx' else (
                allocation.amount if (cash_line.balance > 0) == (code in INFLOW_ROWS)
                else -allocation.amount
            )
            result.append((code, signed))
        return result, external_amount - assigned

    def _external_lines(self, cash_lines, currency):
        by_move = defaultdict(list)
        for line in cash_lines:
            by_move[line.move_id.id].append(line)
        for move_lines in by_move.values():
            net = sum(line.balance for line in move_lines)
            if currency.is_zero(net):
                continue
            direction = 1 if net > 0 else -1
            candidates = [line for line in move_lines if line.balance * direction > 0]
            denominator = sum(abs(line.balance) for line in candidates)
            for line in candidates:
                yield line, abs(net) * abs(line.balance) / denominator

    def _calculate_period(self, scheme, period, cash_lines, overrides, hints):
        start, end = period
        currency = scheme.company_id.currency_id
        amounts = defaultdict(float)
        details = defaultdict(list)
        unresolved_lines = []
        scoped = cash_lines.filtered(lambda line: start <= line.date <= end)
        for cash_line, external_amount in self._external_lines(scoped, currency):
            allocations, unresolved = self._classify(
                cash_line, external_amount, overrides, *hints, currency,
                set(scheme.cash_account_ids.ids),
            )
            for code, signed in allocations:
                if code != 'fx':
                    amounts[code] += signed
                    details[code].append((cash_line, signed))
                else:
                    amounts['fx'] += signed
            if not currency.is_zero(unresolved):
                unresolved_lines.append((cash_line, unresolved))
        amounts[21] = self._cash_balance(scheme.company_id, scheme.cash_account_ids,
                                         start - timedelta(days=1))
        actual_closing = self._cash_balance(scheme.company_id, scheme.cash_account_ids, end)
        for code, parts in TOTALS.items():
            amounts[code] = sum(amounts[abs(part)] * (1 if part > 0 else -1) for part in parts)
        difference = currency.round(actual_closing - amounts[22])
        return amounts, details, unresolved_lines, actual_closing, difference

    @api.model
    def report_data(self, scheme, report_date, mode):
        periods = self._periods(report_date, mode)
        headers = ['本年金额', '上年金额'] if mode == 'annual' else ['本年累计金额', '本月金额']
        if not scheme.cash_account_ids:
            return {'issues': [self.env._('Select the cash and cash-equivalent accounts in the report scheme.')],
                    'rows': [], 'details': {}, 'unclassified_lines': [],
                    'headers': headers, 'empty': True,
                    'empty_message': self.env._('No cash accounts have been configured for this report.'),
                    'actual_cash': [0.0, 0.0], 'fx_effect': [0.0, 0.0],
                    'differences': [0.0, 0.0]}
        from_date = min(period[0] for period in periods)
        CashLine = self.env['account.move.line'].with_company(scheme.company_id)
        cash_lines = CashLine.search([
            ('company_id', '=', scheme.company_id.id), ('parent_state', '=', 'posted'),
            ('account_id', 'in', scheme.cash_account_ids.ids),
            ('date', '>=', from_date), ('date', '<=', report_date),
        ])
        allocations = self.env['qing.cash.flow.allocation'].search([
            ('company_id', '=', scheme.company_id.id), ('cash_line_id', 'in', cash_lines.ids),
        ])
        overrides = defaultdict(lambda: self.env['qing.cash.flow.allocation'])
        for item in allocations:
            overrides[item.cash_line_id.id] |= item
        hints = self._classification_hints(scheme)
        results = [self._calculate_period(scheme, period, cash_lines, overrides, hints)
                   for period in periods]
        currency = scheme.company_id.currency_id
        issues = []
        pending = {}
        for index, (_amounts, _details, unclassified, _closing, difference) in enumerate(results):
            for line, amount in unclassified:
                pending[line.id] = {'id': line.id, 'date': str(line.date),
                                    'move': line.move_id.name, 'amount': currency.round(amount)}
            if not currency.is_zero(difference):
                issues.append(self.env._('Cash flow column %s does not reconcile to cash account balances: %s.',
                                         index + 1, currency.round(difference)))
            if not currency.is_zero(_amounts['fx']):
                issues.append(self.env._(
                    'Cash flow column %s contains a foreign exchange effect of %s; review its statutory presentation.',
                    index + 1, currency.round(_amounts['fx']),
                ))
        if pending:
            issues.append(self.env._('%s cash journal items need classification.', len(pending)))
        rows = [
            {'code': code, 'name': name,
             'amounts': [f'{currency.round(result[0][code]):.{currency.decimal_places}f}'
                         for result in results]}
            for code, name in ROW_NAMES.items()
        ]
        direct_details = defaultdict(list)
        for index, result in enumerate(results):
            for code, entries in result[1].items():
                direct_details[code] += [(line, index, value) for line, value in entries]

        def expand(code):
            entries = list(direct_details[code])
            if code in (21, 22):
                return entries
            for part in TOTALS.get(code, ()):
                entries += [(line, index, value * (1 if part > 0 else -1))
                            for line, index, value in expand(abs(part))]
            return entries

        details = {
            str(code): [
                {'move_line_id': line.id, 'account_id': line.account_id.id,
                 'account_code': line.account_id.code, 'account_name': line.account_id.name,
                 'partner_id': line.partner_id.id, 'partner': line.partner_id.display_name or '',
                 'amounts': [f'{value:.{currency.decimal_places}f}' if i == index else ''
                             for i in range(2)]}
                for line, index, value in expand(code)
            ]
            for code in ROW_NAMES
        }
        return {
            'rows': rows, 'details': details, 'issues': issues,
            'empty': not bool(cash_lines),
            'empty_message': self.env._('No posted cash movements in the selected period.'),
            'unclassified_lines': list(pending.values()),
            'headers': headers,
            'actual_cash': [currency.round(result[3]) for result in results],
            'fx_effect': [currency.round(result[0]['fx']) for result in results],
            'differences': [result[4] for result in results],
        }
