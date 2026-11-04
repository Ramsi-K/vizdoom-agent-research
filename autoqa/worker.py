"""Finalize native recordings when a long validation worker receives SIGTERM."""

import signal
from contextlib import contextmanager


@contextmanager
def finalize_on_sigterm():
    previous = signal.getsignal(signal.SIGTERM)

    def terminate(signum, frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        print("Worker received SIGTERM; finalizing the existing episode", flush=True)
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)
