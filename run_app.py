"""Entry script for the packaged app (PyInstaller)."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()  # worker processes stop here
    from pipfucci.updater import maybe_launch_newer

    maybe_launch_newer(sys.argv)  # hands over to a newer downloaded version, if any
    from pipfucci.app import main

    sys.exit(main())
