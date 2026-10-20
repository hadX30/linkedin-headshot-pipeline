"""Background execution: uploads return immediately, runs execute on worker threads."""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor

from .pipeline import execute_run
from .settings import Settings
from .store import TraceStore

log = logging.getLogger("headshot.jobs")


class JobRunner:
    def __init__(self, settings: Settings, store: TraceStore):
        self.settings = settings
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=max(1, settings.workers), thread_name_prefix="headshot-run")

    def recover(self) -> None:
        """Runs left queued/running by a previous process will never finish; close them."""
        count = self.store.fail_unfinished("Interrupted: the server restarted before this run finished.")
        if count:
            log.warning("Marked %d unfinished run(s) as error after restart", count)

    def submit(self, run_id: str) -> Future:
        return self.pool.submit(execute_run, self.settings, self.store, run_id)

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
