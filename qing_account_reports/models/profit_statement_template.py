"""Fixed ASSE profit statement, MoF 2011 appendix pp. 75-78."""

TEMPLATE_VERSION = 'cn-small-profit-2011-v1'

# Statutory Chinese form labels intentionally remain Chinese in every language.
ROW_NAMES = {
    1: '一、营业收入',
    2: '减：营业成本',
    3: '营业税金及附加',
    4: '其中：消费税',
    5: '营业税',
    6: '城市维护建设税',
    7: '资源税',
    8: '土地增值税',
    9: '城镇土地使用税、房产税、车船税、印花税',
    10: '教育费附加、矿产资源补偿费、排污费',
    11: '销售费用',
    12: '其中：商品维修费',
    13: '广告费和业务宣传费',
    14: '管理费用',
    15: '其中：开办费',
    16: '业务招待费',
    17: '研究费用',
    18: '财务费用',
    19: '其中：利息费用（收入以“-”号填列）',
    20: '加：投资收益（损失以“-”号填列）',
    21: '二、营业利润（亏损以“-”号填列）',
    22: '加：营业外收入',
    23: '其中：政府补助',
    24: '减：营业外支出',
    25: '其中：坏账损失',
    26: '无法收回的长期债券投资损失',
    27: '无法收回的长期股权投资损失',
    28: '自然灾害等不可抗力因素造成的损失',
    29: '税收滞纳金',
    30: '三、利润总额（亏损总额以“-”号填列）',
    31: '减：所得税费用',
    32: '四、净利润（净亏损以“-”号填列）',
}

FORMULA_ROWS = {21, 30, 32}
ROW_SELECTION = [(str(code), f'{code} · {label}') for code, label in ROW_NAMES.items()
                 if code not in FORMULA_ROWS]
INCOME_ROWS = {1, 20, 22, 23}

# Map original native cn template XML IDs, never customer-created accounts by code.
CN_XMLID_ROWS = {
    'l10n_cn_account_5001': 1,
    'l10n_cn_account_5051': 1,
    'l10n_cn_account_5111': 20,
    'l10n_cn_account_5301': 22,
    'l10n_cn_account_530101': 23,
    'l10n_cn_account_530102': 22,
    'l10n_cn_account_530103': 22,
    'l10n_cn_account_530104': 22,
    'l10n_cn_account_5401': 2,
    'l10n_cn_account_5402': 2,
    'l10n_cn_account_5403': 3,
    'l10n_cn_account_540301': 4,
    'l10n_cn_account_540302': 5,
    'l10n_cn_account_540303': 6,
    'l10n_cn_account_540304': 7,
    'l10n_cn_account_540305': 8,
    'l10n_cn_account_540306': 9,
    'l10n_cn_account_540307': 9,
    'l10n_cn_account_540308': 9,
    'l10n_cn_account_540309': 9,
    'l10n_cn_account_540310': 10,
    'l10n_cn_account_540311': 10,
    'l10n_cn_account_540312': 10,
    'l10n_cn_account_5601': 11,
    'l10n_cn_account_560101': 12,
    'l10n_cn_account_560102': 13,
    'l10n_cn_account_5602': 14,
    'l10n_cn_account_560201': 15,
    'l10n_cn_account_560202': 16,
    'l10n_cn_account_560203': 17,
    'l10n_cn_account_5603': 18,
    'l10n_cn_account_560301': 19,
    'l10n_cn_account_560302': 18,
    'l10n_cn_account_560303': 18,
    'l10n_cn_account_560304': 18,
    'l10n_cn_account_5711': 24,
    'l10n_cn_account_571101': 25,
    'l10n_cn_account_571102': 26,
    'l10n_cn_account_571103': 27,
    'l10n_cn_account_571104': 28,
    'l10n_cn_account_571105': 29,
    'l10n_cn_account_5801': 31,
}

CN_CLOSING_XMLIDS = ('l10n_cn_account_3103', 'l10n_cn_account_3104')
