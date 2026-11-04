"""Run one selected demo slot, finalizing a native recording on worker SIGTERM.

Launch in a separate process session with a file log. This wrapper does not
choose actions or alter the Pilot. SIGKILL or killing the native process itself
cannot be made safe by a Python signal handler.
"""

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


def main(argv=None):
    from scripts.run_m35 import main as run_one

    with finalize_on_sigterm():
        return run_one(argv)


if __name__ == "__main__":
    raise SystemExit(main())
