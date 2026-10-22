"""OS-owned lock shared by desktop and command-line workers."""
import os
import json
from pathlib import Path


class WorkerBusy(ValueError):
    """持锁进程存在；控制命令仍可提交给它。"""


def write_owner(path, owner):
    with path.open("r+b") as handle:
        handle.seek(1)
        handle.write(json.dumps(owner).encode())
        handle.truncate()
        handle.flush()


def read_owner(path):
    try:
        # Windows byte-range locks also reject reads that include byte zero.
        with path.open("rb") as handle:
            handle.seek(1)
            return json.loads(handle.read() or b"{}")
    except (FileNotFoundError, ValueError):
        return {}


class WorkerLock:
    def __init__(self, path: Path):
        self.path = path
        self.file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file = self.path.open('a+b')
        if file.tell() == 0:
            file.write(b'0')
            file.flush()
        file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            file.close()
            raise WorkerBusy('另一个收藏转发进程正在处理视频；控制请求可交给运行中的 worker') from error
        self.file = file
        file.seek(1)
        file.write(json.dumps({"pid": os.getpid(), "kind": "operation"}).encode())
        file.truncate()
        file.flush()
        return self

    def release(self):
        if self.file:
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close()
            self.file = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *args):
        self.release()
