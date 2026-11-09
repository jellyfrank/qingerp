"""Conservative extraction of labelled tax-invoice fields from recognized text."""
import re
import unicodedata
from datetime import date
from decimal import Decimal


def _party_columns(lines):
    """Resolve the two columns only when buyer/seller markers prove their order."""
    candidates = {}
    for index, line in enumerate(lines):
        buyer = re.search(r'购(?:买方)?名称[:：]', line)
        seller = re.search(r'销(?:售方)?名称[:：]', line)
        if not buyer or not seller or buyer.start() >= seller.start():
            continue
        pair = {'buyer_name': line[buyer.end():seller.start()], 'seller_name': line[seller.end():]}
        if not all(pair.values()):
            continue
        for key, value in pair.items():
            candidates.setdefault(key, set()).add(value)
        for following in lines[index + 1:index + 16]:
            if re.search(r'项目名称|货物或应税|规格型号|价税合计', following):
                break
            ids = re.findall(r'(?:纳税人识别号|统一社会信用代码|税号)[:：]([A-Z0-9]{15,20})(?![A-Z0-9])', following)
            if len(ids) == 2:
                for key, value in zip(('buyer_tax_id', 'seller_tax_id'), ids):
                    candidates.setdefault(key, set()).add(value)
    return {key: next(iter(values)) for key, values in candidates.items() if len(values) == 1}


def parse_fields(text):
    """Only extract unambiguous labelled values; never guess missing totals/IDs."""
    lines = [re.sub(r'[ \t]+', '', line) for line in unicodedata.normalize('NFKC', text).splitlines()]
    compact = '\n'.join(lines)
    values = {}
    patterns = {
        'number': r'(?:发票号码|票号)[:：]?([0-9]{8,30})(?![0-9])',
        'code': r'发票代码[:：]?([0-9]{10,12})(?![0-9])',
        'buyer_name': r'购买方名称[:：]([^\n]+?)(?=销售方名称|$)',
        'seller_name': r'销售方名称[:：]([^\n]+?)(?=购买方名称|$)',
        'buyer_tax_id': r'购买方(?:税号|纳税人识别号)[:：]([A-Z0-9]+)',
        'seller_tax_id': r'销售方(?:税号|纳税人识别号)[:：]([A-Z0-9]+)',
        'amount_untaxed': r'(?:不含税金额|金额合计)[:：]?[¥￥]?([0-9,]+\.[0-9]{2})(?![0-9])',
        'amount_tax': r'(?:税额合计|合计税额|税额)[:：][¥￥]?([0-9,]+\.[0-9]{2})(?![0-9])',
        'invoice_total': r'(?:价税合计(?:\(小写\))?|\(小写\))[:：]?[¥￥]?([0-9,]+\.[0-9]{2})(?![0-9])',
    }
    for key, pattern in patterns.items():
        matches = set(re.findall(pattern, compact, re.M))
        if len(matches) == 1:
            value = matches.pop()
            values[key] = float(value.replace(',', '')) if key.startswith('amount_') or key == 'invoice_total' else value
    for key, value in _party_columns(lines).items():
        if key in values and values[key] != value:
            values.pop(key)
        else:
            values[key] = value
    kinds = set(re.findall(r'(普通|专用)发票', compact))
    if len(kinds) == 1:
        values['kind'] = 'normal' if '普通' in kinds else 'special'
    # Read the explicit subtotal row, never sum item rows or reverse-calculate tax.
    totals = set(re.findall(r'^合计[:：]?[¥￥]([0-9,]+\.[0-9]{2})[¥￥]([0-9,]+\.[0-9]{2})$', compact, re.M))
    if len(totals) == 1 and 'invoice_total' in values:
        net, tax = [Decimal(value.replace(',', '')) for value in totals.pop()]
        if net + tax == Decimal(str(values['invoice_total'])):
            for key, amount in (('amount_untaxed', net), ('amount_tax', tax)):
                if key not in values:
                    values[key] = float(amount)
    dates = set(re.findall(r'开票日期[:：]?(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})日?', compact))
    if len(dates) == 1:
        try:
            values['invoice_date'] = date(*map(int, dates.pop())).isoformat()
        except ValueError:
            pass
    return values
