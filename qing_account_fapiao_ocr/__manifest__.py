{
    'name': 'Qing 税票本地识别',
    'summary': '服务器本地识别税票并经人工核对生成草稿',
    'version': '20.0.1.9',
    'category': 'Accounting/Accounting',
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'support': 'kevin@odoomommy.com',
    'license': 'LGPL-3',
    'depends': ['qing_account_fapiao', 'qing_ocr'],
    'data': ['security/ir.access.csv', 'views/view_fapiao_ocr_form.xml'],
    'installable': True,
    'description': 'Recognize tax-invoice files locally and create draft records after manual review.',
}
