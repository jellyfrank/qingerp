# Part of Odoo. See LICENSE file for full copyright and licensing details.

# Existing Antom checkout currency allowlist. Availability still depends on the
# merchant contract and selected payment method. Mainland Alipay uses CNY only.

SUPPORTED_CURRENCIES = (
    'AUD',
    'BRL',
    'CAD',
    'CNY',
    'CZK',
    'DKK',
    'EUR',
    'HKD',
    'HUF',
    'ILS',
    'JPY',
    'MYR',
    'MXN',
    'TWD',
    'NZD',
    'NOK',
    'PHP',
    'PLN',
    'GBP',
    'RUB',
    'SGD',
    'SEK',
    'CHF',
    'THB',
    'USD',
)

# The codes of the payment methods to activate when Alipay is activated.
DEFAULT_PAYMENT_METHODS_CODES = [
    # Primary payment methods.
    'alipay',
]

PAYMENT_STATUS_MAPPING = {
    'pending': ('WAIT_BUYER_PAY',),
    'done': ('TRADE_SUCCESS','TRADE_FINISHED'),
    'cancel': ('TRADE_CLOSED'),
}
