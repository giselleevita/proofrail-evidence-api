from __future__ import annotations

import threading
from collections.abc import Callable


class RefreshScheduler:
    """Very small in-process scheduler for periodic refreshes.

    This avoids adding an external queue; in production you'd likely move to
    a real job system.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._stops: dict[str, threading.Event] = {}

    def start(self, customer_id: str, interval_s: int, func: Callable[[], None]) -> None:
        with self._lock:
            if customer_id in self._threads:
                return
            stop = threading.Event()
            self._stops[customer_id] = stop

            def loop() -> None:
                while not stop.is_set():
                    func()
                    stop.wait(interval_s)

            t = threading.Thread(target=loop, name=f"refresh-{customer_id}", daemon=True)
            self._threads[customer_id] = t
            t.start()

    def stop(self, customer_id: str) -> None:
        with self._lock:
            ev = self._stops.pop(customer_id, None)
            if ev is not None:
                ev.set()
            self._threads.pop(customer_id, None)

