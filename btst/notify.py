"""The BTST desk's alerts to a Telegram channel (owner, 27 Oct 2026).

Sent as the Telegram USER (TELEGRAM_API_ID / TELEGRAM_API_HASH, the login of
arb/utils.py) to TELEGRAM_CHANNEL_NAME in .angel.env - not through the bot
(TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID). Messages queue to a thread of their
own, so a slow Telegram never holds up a trade; a send that fails is tried
once more, then logged and dropped.
"""
import queue
import threading
import time

from btst.logbook import fmt


class ChannelNotifier:
    def __init__(self, channel=None, send_fn=None, log=print, retry_sec=10.0):
        if send_fn is None:
            from arb.utils import send_channel_message
            send_fn = send_channel_message
        self.channel, self.send_fn, self.log, self.retry_sec = channel, send_fn, log, float(retry_sec)
        self.q = queue.Queue()
        self.stop_ev = threading.Event()
        self.thread = None
        self.sent = self.failed = 0

    def start(self):
        self.thread = threading.Thread(target=self._run, name="btst-telegram", daemon=True)
        self.thread.start()
        return self

    def send(self, text):
        """Queue one message (HTML)."""
        self.q.put(str(text))

    def _send_once(self, text):
        try:
            return bool(self.send_fn(text, channel=self.channel, log=self._quiet))
        except Exception as e:
            self.log(fmt("WARN", "telegram", f"channel send raised {e!r}"))
            return False

    def _quiet(self, text):
        self.log(fmt("WARN" if "fail" in str(text).lower() or "not" in str(text).lower() else "INFO",
                     "telegram", str(text)))

    def _run(self):
        while not (self.stop_ev.is_set() and self.q.empty()):
            try:
                text = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            ok = self._send_once(text)
            if not ok:
                time.sleep(self.retry_sec)
                ok = self._send_once(text)
            if ok:
                self.sent += 1
            else:
                self.failed += 1
                self.log(fmt("WARN", "telegram", f"alert not sent to the channel after 2 tries: {text[:120]}"))

    def stop(self, wait=5.0):
        """Send what is queued (up to `wait` seconds), then stop."""
        self.stop_ev.set()
        if self.thread is not None:
            self.thread.join(timeout=wait)
