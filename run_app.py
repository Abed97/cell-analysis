"""Entry script for the packaged app (PyInstaller)."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from pipfucci.app import main

    sys.exit(main())
