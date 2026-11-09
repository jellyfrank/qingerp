# qing_account_fapiao_connector

Connects Qing tax-invoice requests to the shared Mommy Fapiao provider-job framework.


明确失败的开票任务可从任务页面重试。重试会重新校验已审核申请和服务商配置，并创建带新请求编号的任务，原失败任务及审计记录会保留；结果未知或服务商处理中时应先查询原任务，不能重复提交。
