"""MongoDB store for the BTST strategy (btst/, run_btst.py).

Connection string: DATABASE_URL in .angel.env (or the environment). The URI
need not name a database; `db_name` (config/btst.json "mongo_db") does.

Collections
    btst_jobs      one document per BTST trade, keyed by job_id: the Telegram
                   message, the parsed signal, the contract, every order (buy,
                   target, average-down, cancel, exit), the position, the
                   next-day decision, the closing metrics and an event log
    btst_messages  every channel message read inside the scan window, with
                   whether it was a signal and why - so a restart never asks
                   the AI about the same message twice
    btst_days      a trading day that ended its scan window with no signal:
                   no trade, and how many messages were read
"""
import os
import sys
import uuid
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OPEN_STATUSES = ("entering", "open")


def database_url(env_path=None):
    """DATABASE_URL from the environment, else from .angel.env."""
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        return url
    from arb.config import _load_env_file
    return str(_load_env_file(env_path or os.path.join(ROOT, ".angel.env")).get("DATABASE_URL", "")).strip()


class MongoStore:
    def __init__(self, url=None, db_name="trading_btst", client=None, timeout_ms=8000):
        if client is None:
            from pymongo import MongoClient
            url = url or database_url()
            if not url:
                raise ValueError("DATABASE_URL is not set in .angel.env")
            client = MongoClient(url, serverSelectionTimeoutMS=timeout_ms, tz_aware=False)
        self.client = client
        self.db = client[db_name]
        self.jobs = self.db["btst_jobs"]
        self.messages = self.db["btst_messages"]
        self.days = self.db["btst_days"]
        self.ticks = self.db["btst_ticks"]      # a trade's recorded prices, by job id (btst/recorder.py)

    def ensure_indexes(self):
        from pymongo import ASCENDING, DESCENDING
        self.jobs.create_index([("job_id", ASCENDING)], unique=True)
        self.jobs.create_index([("trade_date", DESCENDING)])
        self.jobs.create_index([("status", ASCENDING)])
        self.messages.create_index([("channel", ASCENDING), ("msg_id", ASCENDING)], unique=True)
        self.days.create_index([("date", ASCENDING)], unique=True)
        self.ticks.create_index([("job_id", ASCENDING)], unique=True)
        self.jobs.create_index([("recording.status", ASCENDING)])

    def ping(self):
        return self.client.admin.command("ping")

    # ── jobs ────────────────────────────────────────────────────────────────
    @staticmethod
    def new_job_id():
        return datetime.now().strftime("BTST-%Y%m%d-") + uuid.uuid4().hex[:10]

    def insert_job(self, doc):
        doc = dict(doc)
        doc.setdefault("job_id", self.new_job_id())
        self.jobs.insert_one(dict(doc))
        return doc["job_id"]

    def update_job(self, job_id, set_=None, push=None):
        upd = {}
        if set_:
            upd["$set"] = set_
        if push:
            upd["$push"] = push
        if upd:
            self.jobs.update_one({"job_id": job_id}, upd)

    def add_event(self, job_id, at, text):
        self.update_job(job_id, push={"events": {"at": at, "text": text}})

    def add_order(self, job_id, order):
        self.update_job(job_id, push={"orders": order})

    def get_job(self, job_id):
        return self.jobs.find_one({"job_id": job_id}, {"_id": 0})

    def open_job(self):
        """The trade still being managed, if any (one at a time)."""
        return self.jobs.find_one({"status": {"$in": list(OPEN_STATUSES)}}, {"_id": 0},
                                  sort=[("created_at", -1)])

    def job_for_trade_date(self, trade_date):
        """A BTST entered (or being entered) on trade_date, if any - one a day.
        A paper trade parked by a switch to live does not count."""
        return self.jobs.find_one({"trade_date": trade_date,
                                   "status": {"$nin": ["rejected", "parked"]}}, {"_id": 0})

    def recording_jobs(self):
        """Trades whose prices are still being recorded (btst/recorder.py)."""
        return list(self.jobs.find({"recording.status": "recording"}, {"_id": 0}))

    def save_ticks(self, doc):
        """A trade's recorded prices (1-minute bars, the gzipped ticks, its events), by job id."""
        self.ticks.update_one({"job_id": doc["job_id"]}, {"$set": doc}, upsert=True)

    def get_ticks(self, job_id, raw=False):
        proj = {"_id": 0} if raw else {"_id": 0, "gz": 0}
        return self.ticks.find_one({"job_id": job_id}, proj)

    def parked_jobs(self, mode=None):
        """Trades parked by a mode switch (saved, not managed), newest first."""
        flt = {"status": "parked"}
        if mode:
            flt["mode"] = mode
        return list(self.jobs.find(flt, {"_id": 0}).sort("created_at", -1))

    def any_job_on(self, trade_date):
        """Any job on trade_date, refused ones included."""
        return self.jobs.find_one({"trade_date": trade_date}, {"_id": 0}) is not None

    def recent_jobs(self, limit=30):
        return list(self.jobs.find({}, {"_id": 0}).sort("created_at", -1).limit(int(limit)))

    # ── messages ────────────────────────────────────────────────────────────
    def message_seen(self, channel, msg_id):
        return self.messages.find_one({"channel": channel, "msg_id": int(msg_id)}, {"_id": 1}) is not None

    def save_message(self, doc):
        """Upsert by (channel, msg_id)."""
        self.messages.update_one({"channel": doc["channel"], "msg_id": int(doc["msg_id"])},
                                 {"$set": doc}, upsert=True)

    def recent_messages(self, limit=30):
        return list(self.messages.find({}, {"_id": 0}).sort("date", -1).limit(int(limit)))

    def messages_on(self, day):
        """The messages read on `day` (YYYY-MM-DD)."""
        return [m for m in self.messages.find({}, {"_id": 0}) if str(m.get("date", "")).startswith(day)]

    # ── days with no signal ─────────────────────────────────────────────────
    def get_day(self, day):
        return self.days.find_one({"date": day}, {"_id": 0})

    def day_recorded(self, day):
        return self.days.find_one({"date": day}, {"_id": 1}) is not None

    def save_day(self, doc):
        self.days.update_one({"date": doc["date"]}, {"$set": doc}, upsert=True)

    def recent_days(self, limit=400):
        return list(self.days.find({}, {"_id": 0}).sort("date", -1).limit(int(limit)))
