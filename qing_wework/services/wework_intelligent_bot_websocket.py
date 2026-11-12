# -*- coding: utf-8 -*-
"""Process-local WebSocket workers for WeCom intelligent bots."""

import logging
import threading
import time
from contextlib import contextmanager

from odoo import SUPERUSER_ID, fields
from odoo.api import Environment
from odoo.modules.registry import Registry
from wechatpy.work.ai_bot import WeChatAIBotWebSocketClient


_logger = logging.getLogger(__name__)
CONFIGURATION_CHECK_SECONDS = 5
DELIVERY_CHECK_SECONDS = 2


class WeworkIntelligentBotWebSocketManager(object):
    """Own at most one local worker per database and intelligent bot."""

    _lock = threading.Lock()
    _workers = {}

    @classmethod
    def ensure_running(cls, database, bot_id):
        key = (database, bot_id)
        with cls._lock:
            worker = cls._workers.get(key)
            if worker and worker.is_alive():
                return False
            worker = _WeworkIntelligentBotWebSocketWorker(database, bot_id, key)
            cls._workers[key] = worker
            worker.start()
        return True

    @classmethod
    def stop(cls, database, bot_id):
        with cls._lock:
            worker = cls._workers.get((database, bot_id))
            if worker:
                worker.stop()

    @classmethod
    def _remove_worker(cls, key, worker):
        with cls._lock:
            if cls._workers.get(key) is worker:
                cls._workers.pop(key, None)


class _WeworkIntelligentBotWebSocketWorker(threading.Thread):
    """Keep a bot connection outside Odoo HTTP and cron request threads."""

    def __init__(self, database, bot_id, key):
        super().__init__(
            name="qing-wework-ws-%s-%s" % (database, bot_id),
            daemon=True,
        )
        self.database = database
        self.bot_id = bot_id
        self.key = key
        self.stop_event = threading.Event()
        self._enabled = True
        self._next_configuration_check = 0
        self._next_delivery_check = 0

    @contextmanager
    def _environment(self):
        registry = Registry(self.database)
        with registry.cursor() as cr:
            env = Environment(cr, SUPERUSER_ID, {})
            yield env
            cr.commit()

    def _get_bot(self, env):
        Bot = env["wework.intelligent.bot"].sudo()
        return Bot.browse(self.bot_id).exists()

    def _get_configuration(self):
        with self._environment() as env:
            bot = self._get_bot(env)
            if not bot or not bot._is_websocket_connection_enabled():
                return False
            return {
                "bot_id": bot.websocket_bot_id,
                "secret": bot.websocket_secret,
                "ws_url": bot.websocket_url,
            }

    def _set_status(self, status, error=False):
        try:
            with self._environment() as env:
                bot = self._get_bot(env)
                if not bot:
                    return
                values = {"websocket_status": status}
                if status == "connected":
                    values["websocket_last_connected_at"] = fields.Datetime.now()
                    values["websocket_last_error"] = False
                elif error:
                    values["websocket_last_error"] = str(error)
                bot.write(values)
        except Exception:
            _logger.exception("[QingERP WeCom] Could not update intelligent bot WebSocket status.")

    def _record_frame(self, frame):
        command = frame.get("cmd") or "unknown"
        try:
            with self._environment() as env:
                bot = self._get_bot(env)
                if bot:
                    bot.write({
                        "websocket_last_event": command,
                        "websocket_last_frame_at": fields.Datetime.now(),
                    })
        except Exception:
            _logger.exception("[QingERP WeCom] Could not record intelligent bot WebSocket frame.")

    def _should_continue(self):
        if self.stop_event.is_set():
            return False
        now = time.monotonic()
        if now < self._next_configuration_check:
            return self._enabled
        self._next_configuration_check = now + CONFIGURATION_CHECK_SECONDS
        self._enabled = bool(self._get_configuration())
        return self._enabled

    def _handle_message(self, client, frame):
        self._record_frame(frame)
        headers = frame.get("headers") or {}
        request_id = headers.get("req_id")
        payload = frame.get("body") or {}
        if not request_id or not isinstance(payload, dict):
            _logger.warning("[QingERP WeCom] Ignored invalid intelligent bot WebSocket callback.")
            return
        with self._environment() as env:
            bot = self._get_bot(env)
            if not bot or not bot._is_websocket_connection_enabled():
                return
            incoming_message = bot._record_incoming_message(payload)
            incoming_message.write({"websocket_request_id": request_id})
            reply_payload = bot._prepare_websocket_reply(incoming_message)
        if reply_payload:
            client.reply(request_id, reply_payload)
            with self._environment() as env:
                bot = self._get_bot(env)
                if bot:
                    Message = env["wework.intelligent.bot.message"].sudo()
                    bot._mark_websocket_reply_delivered(
                        Message.browse(incoming_message.id),
                        reply_payload,
                    )

    def _handle_event(self, frame):
        self._record_frame(frame)
        event = (frame.get("body") or {}).get("event") or {}
        if event.get("eventtype") == "disconnected_event":
            error = "WeCom closed this connection because another client connected with the same bot credentials."
            _logger.warning("[QingERP WeCom] %s", error)
            self._set_status("error", error)
            self.stop_event.set()

    def _process_pending_deliveries(self, client):
        now = time.monotonic()
        if now < self._next_delivery_check:
            return
        self._next_delivery_check = now + DELIVERY_CHECK_SECONDS
        with self._environment() as env:
            bot = self._get_bot(env)
            if bot and bot._is_websocket_connection_enabled():
                bot._deliver_websocket_pending_messages(client)

    def _acquire_database_lock(self):
        registry = Registry(self.database)
        cursor = registry.cursor()
        cursor.execute(
            "SELECT pg_try_advisory_lock(hashtext(%s))",
            ["qing_wework_intelligent_bot_websocket_%s" % self.bot_id],
        )
        if cursor.fetchone()[0]:
            return cursor
        cursor.close()
        return False

    def run(self):
        advisory_lock_cursor = self._acquire_database_lock()
        if not advisory_lock_cursor:
            _logger.info(
                "[QingERP WeCom] Another process owns WebSocket bot %s in %s.",
                self.bot_id,
                self.database,
            )
            WeworkIntelligentBotWebSocketManager._remove_worker(self.key, self)
            return
        try:
            configuration = self._get_configuration()
            if not configuration:
                self._set_status("stopped")
                return
            self._set_status("connecting")
            client = WeChatAIBotWebSocketClient(
                configuration["bot_id"],
                configuration["secret"],
                ws_url=configuration["ws_url"],
            )
            client.serve_forever(
                lambda frame: self._handle_message(client, frame),
                self.stop_event,
                on_authenticated=lambda: self._set_status("connected"),
                on_error=lambda error: self._set_status("error", error),
                on_event=self._handle_event,
                on_idle=lambda: self._process_pending_deliveries(client),
                should_continue=self._should_continue,
            )
        except Exception as error:
            _logger.exception("[QingERP WeCom] Intelligent bot WebSocket worker failed.")
            self._set_status("error", error)
        finally:
            advisory_lock_cursor.close()
            if not self._enabled:
                self._set_status("stopped")
            WeworkIntelligentBotWebSocketManager._remove_worker(self.key, self)

    def stop(self):
        self.stop_event.set()
