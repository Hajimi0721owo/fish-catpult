"""SQLite 持有基线、队列和结果；事务内同时登记已见与待发送。"""
import json
import sqlite3
import time


class Store:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS seen(id TEXT PRIMARY KEY, title TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS jobs(
            id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL,
            created REAL NOT NULL, updated REAL NOT NULL, error TEXT NOT NULL DEFAULT '',
            receipt TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0);
        """)
        self.db.commit()
        self.set_default("schema_version", 1)
        if self.get("schema_version") != 1:
            raise ValueError("数据库版本不兼容，请使用对应版本程序")

    def close(self):
        self.db.close()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_default(self, key, value):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES (?,?)", (key, json.dumps(value)))

    def set(self, key, value):
        with self.db:
            self.db.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, json.dumps(value)))

    def mutate_meta(self, key, update, default=None):
        """多个控制入口在同一事务内读改写，不能覆盖刚提交的取消或模式设置。"""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            value = update(self.get(key, default))
            self.db.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, json.dumps(value)))
        return value

    def update_sync(self, request_id, **updates):
        def update(current):
            if not current or current["id"] != request_id:
                raise ValueError("同步任务已变化，请重新查看状态")
            return {**current, **updates}
        return self.mutate_meta("sync_request", update)

    def queued_ids(self):
        return [row[0] for row in self.db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created,rowid")]

    def job_counts(self, ids):
        # 收藏夹最多 1000 条；逐条查询避免不同 SQLite 版本的变量数量上限。
        counts = {}
        for item_id in ids:
            row = self.db.execute("SELECT status FROM jobs WHERE id=?", (item_id,)).fetchone()
            status = row[0] if row else "missing"
            counts[status] = counts.get(status, 0) + 1
        return counts

    def assert_folder(self, folder):
        def update(existing):
            if existing and existing != folder:
                raise ValueError("此状态目录属于其他收藏夹；新订阅请指定独立 data_dir")
            return folder
        self.mutate_meta("folder", update)

    def observe(self, items):
        baseline = not self.get("initialized", False)
        queued, now = 0, time.time()
        with self.db:
            for item in items:
                new = self.db.execute("INSERT OR IGNORE INTO seen VALUES (?,?)", (item["id"], item["title"]))
                if new.rowcount and not baseline:
                    self.db.execute("INSERT INTO jobs(id,title,status,created,updated) VALUES (?,?,'queued',?,?)",
                                    (item["id"], item["title"], now, now))
                    queued += 1
            self.db.execute("INSERT INTO meta VALUES ('initialized','true') ON CONFLICT(key) DO UPDATE SET value='true'")
        return {"baseline_created": baseline, "observed": len(items), "queued": queued}

    def validate_setup_folder(self, folder):
        """只读候选归属；存在历史或任务时禁止用设置向导换源。"""
        protected = self.get("initialized", False) or self.db.execute("SELECT 1 FROM seen LIMIT 1").fetchone() or self.db.execute(
            "SELECT 1 FROM jobs LIMIT 1").fetchone()
        if protected and self.get("folder") != folder:
            raise ValueError("此状态目录属于其他收藏夹；新订阅请指定独立 data_dir")
        return bool(protected)

    def commit_setup_folder(self, folder, persist):
        """预检成功才提交；配置写入失败时回滚归属和暂停状态。"""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.validate_setup_folder(folder)
            values = {"folder": folder, "source_paused": False, "poll_error": "", "poll_failures": 0}
            self.db.executemany("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                [(key, json.dumps(value)) for key, value in values.items()])
            persist()

    def transition(self, item_id, status, error="", receipt=""):
        with self.db:
            self.db.execute("UPDATE jobs SET status=?,updated=?,error=?,receipt=? WHERE id=?",
                            (status, time.time(), error, receipt, item_id))

    def next_job(self):
        with self.db:
            row = self.db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created,rowid LIMIT 1").fetchone()
            if row:
                self.db.execute("UPDATE jobs SET status='preparing',attempts=attempts+1,updated=? WHERE id=?",
                                (time.time(), row["id"]))
        return dict(row) if row else None

    def recover(self):
        # 发送调用前就落盘。崩溃后只恢复下载，不猜测外部消息是否已送达。
        with self.db:
            self.db.execute("UPDATE jobs SET status='queued' WHERE status='preparing'")
            self.db.execute("UPDATE jobs SET status='uncertain',error='进程在发送阶段退出，请核对目标群' WHERE status='sending'")
        if self.db.execute("SELECT 1 FROM jobs WHERE status='uncertain' LIMIT 1").fetchone():
            self.set("delivery_paused", True)

    def retry(self, item_id):
        with self.db:
            changed = self.db.execute("UPDATE jobs SET status='queued',error='',updated=? WHERE id=? AND status='failed'",
                                      (time.time(), item_id)).rowcount
        if not changed:
            raise ValueError("只能重试 failed 任务；不确定是否送达的任务需先人工核对")

    def resolve(self, item_id, delivered):
        with self.db:
            changed = self.db.execute("UPDATE jobs SET status=?,error='',updated=? WHERE id=? AND status='uncertain'",
                                      ('sent' if delivered else 'queued', time.time(), item_id)).rowcount
        if not changed:
            raise ValueError("该任务不是 uncertain 状态")

    def status(self):
        return {"initialized": self.get("initialized", False),
                "seen": self.db.execute("SELECT count(*) FROM seen").fetchone()[0],
                "counts": dict(self.db.execute("SELECT status,count(*) FROM jobs GROUP BY status")),
                "poll_error": self.get("poll_error", ""), "source_paused": self.get("source_paused", False),
                "delivery_paused": self.get("delivery_paused", False),
                "next_poll_at": self.get("next_poll_at", 0),
                "jobs": [dict(r) for r in self.db.execute("SELECT * FROM jobs ORDER BY updated DESC LIMIT 50")]}

    def backup(self, destination):
        target = sqlite3.connect(destination)
        try:
            self.db.backup(target)
        finally:
            target.close()
