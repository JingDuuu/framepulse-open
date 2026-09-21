"""Frozen desktop backend: the same public CLI, with no separate business logic."""
from multiprocessing import freeze_support


if __name__ == "__main__":
    freeze_support()
    from framepulse.cli import main

    raise SystemExit(main())
