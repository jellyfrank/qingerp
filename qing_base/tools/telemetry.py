from contextvars import ContextVar


HEARTBEAT_CRON_XMLID = 'qing_base.ir_cron_qing_telemetry_heartbeat'
MANUAL_HEARTBEAT = ContextVar('qing_manual_heartbeat', default=False)
