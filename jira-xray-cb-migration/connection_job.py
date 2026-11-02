"""Bounded, cancellable background reads. No Tk or SQLite calls in workers."""
import queue
import threading
import time


class ConnectionJob:
    def __init__(self, action, timeout=30, clients=()):
        self.cancel_event = threading.Event()
        self.deadline = time.monotonic() + timeout
        self.results = queue.Queue()
        self.finished = False
        for client in clients:
            client.read_deadline = self.deadline
            client.cancel_event = self.cancel_event
        def worker():
            try:self.results.put(('success',action()))
            except Exception as exc:self.results.put(('error',exc))
        threading.Thread(target=worker,daemon=True).start()

    def cancel(self):
        self.cancel_event.set()

    def poll(self):
        if self.finished:return 'finished',None
        if self.cancel_event.is_set():result=('cancelled',None)
        elif time.monotonic() >= self.deadline:
            self.cancel_event.set();result=('timeout',None)
        else:
            try:result=self.results.get_nowait()
            except queue.Empty:return 'running',None
        self.finished=True
        return result
