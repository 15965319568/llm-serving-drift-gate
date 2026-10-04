"""Admission is durable; worker threads perform only already claimed attempts."""
import threading

from .errors import WorkerError
from .transport import generate


class Scheduler:
    def __init__(self, store, config):
        self.store, self.config = store, config
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, name="serving-scheduler", daemon=True)

    def start(self):
        self.store.recover_requests()
        self.thread.start()

    def run(self):
        while not self.stopped.is_set():
            request = self.store.claim()
            if request:
                threading.Thread(target=self.execute, args=(request,), daemon=True).start()
            else:
                with self.store.changed: self.store.changed.wait(timeout=0.1)

    def execute(self, request):
        request_id, attempt = request["request_id"], request["attempts"]
        try:
            usage = generate(self.config, request,
                lambda: self.stopped.is_set() or not self.store.is_running(request_id, attempt),
                lambda delta: self.store.append_delta(request_id, attempt, delta))
            if usage is not None:
                self.store.finish(request_id, "SUCCEEDED", usage=usage, attempt=attempt)
        except WorkerError as exc:
            self.store.worker_failed(request_id, attempt, exc)

    def stop(self):
        self.stopped.set()
        with self.store.changed: self.store.changed.notify_all()
        self.thread.join(timeout=2)
