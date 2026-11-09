"""Chinese money words adapted from Solutions mommy_qweb_chinese_num (16.0).

Original module: LGPL-3, author lmch, commit 5000cf01.
Decimal normalization, rounding, validation and unit handling repaired for Qing.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

MAX_AMOUNT = Decimal('10000000000000000')
CAPITAL_DIGITS = ('零', '壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖')
ORDINARY_DIGITS = ('〇', '一', '二', '三', '四', '五', '六', '七', '八', '九')
CAPITAL_UNITS = ('元', '拾', '佰', '仟', '万', '拾', '佰', '仟', '亿', '拾', '佰', '仟', '万', '拾', '佰', '仟')
ORDINARY_UNITS = ('元', '十', '百', '千', '万', '十', '百', '千', '亿', '十', '百', '千', '万', '十', '百', '千')


def cncurrency(value, capital=True, prefix=False, classical=None):
    """Convert a monetary amount to Chinese words with yuan, jiao, and fen.

    ``capital`` selects financial or ordinary Chinese digits, ``classical``
    selects the standard or alternate yuan character, and ``prefix`` adds the
    renminbi currency prefix.
    """
    if not isinstance(capital, bool) or not isinstance(prefix, bool):
        raise ValueError('capital and prefix must be booleans')
    if classical is not None and not isinstance(classical, bool):
        raise ValueError('classical must be a boolean or None')
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int, float)):
        raise ValueError('金额必须是数字、数字字符串或Decimal')
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount.copy_abs() >= MAX_AMOUNT:
            raise ValueError('金额必须为有限数且绝对值小于一亿亿元')
        with localcontext() as context:
            context.prec = 32
            value = amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except InvalidOperation as error:
        raise ValueError('金额格式无效') from error
    if value.copy_abs() >= MAX_AMOUNT:
        raise ValueError('舍入后金额超出支持范围')
    # Default to the standard yuan character in both output modes.
    if classical is None:
        classical = True

    # Build the optional currency prefix.
    if prefix is True:
        prefix = '人民币'
    else:
        prefix = ''

    # Select the digit and unit character sets.
    dunit = ('角', '分')
    num = CAPITAL_DIGITS if capital else ORDINARY_DIGITS
    iunit = list(CAPITAL_UNITS if capital else ORDINARY_UNITS)
    iunit[0] = '元' if classical else '圆'

    # Normalize negative amounts before processing individual digits.
    if value < 0:
        prefix += '负'  # Keep the negative sign next to the currency prefix.
        value = value.copy_abs()
    # Use a fixed two-decimal representation for deterministic processing.
    s = format(value, '.2f')
    istr, dstr = s.split('.')  # Process the integer and decimal parts separately.
    istr = istr[::-1]  # Reverse the integer part to align indexes with units.
    so = []  # Accumulate output tokens in reverse order.

    # Handle zero as a dedicated case.
    if value == 0:
        return prefix + num[0] + iunit[0] + '整'
    haszero = False  # Track whether a zero token was already emitted.
    if dstr == '00':
        haszero = True  # Avoid an extra zero before the whole-amount suffix.

    # Process the decimal part, starting with fen.
    if dstr[1] != '0':
        so.append(dunit[1])
        so.append(num[int(dstr[1])])
    else:
        so.append('整')  # Mark an exact jiao or yuan amount.
    # Process jiao.
    if dstr[0] != '0':
        so.append(dunit[0])
        so.append(num[int(dstr[0])])
    elif dstr[1] != '0':
        so.append(num[0])  # Insert zero when fen exists without jiao.
        haszero = True

    # Return early when there is no integer part.
    if istr == '0':
        if haszero:  # Remove the jiao-position zero for sub-yuan values.
            so.pop()
        so.append(prefix)
        so.reverse()
        return ''.join(so)

    # Process the integer part.
    for i, n in enumerate(istr):
        n = int(n)
        if i % 4 == 0:  # Major units are retained even for zero digits.
            if i == 8 and so[-1] == iunit[4]:  # Collapse an all-zero group between major units.
                so.pop()
            so.append(iunit[i])
            if n == 0:
                if not haszero:
                    so.insert(-1, num[0])
                    haszero = True
            else:
                so.append(num[n])
                haszero = False
        else:
            if n != 0:
                so.append(iunit[i])
                so.append(num[n])
                haszero = False
            else:
                if not haszero:
                    so.append(num[0])
                    haszero = True

    # Restore forward order and return the final text.
    so.append(prefix)
    so.reverse()
    return ''.join(so)
