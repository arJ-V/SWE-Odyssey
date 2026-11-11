#!/usr/bin/env python3
"""Remove the Docker leftovers of every trial in a finished Harbor job directory.

    python3 scripts/atlas/cleanup_job.py <jobs>/<job name> [...]

The same sweep validate_ticket.py runs after its own trial: the compose project's containers,
networks, volumes and images, and LocalStack's Lambda containers (named after the LocalStack
container). Run it after every `harbor run` of an atlas task on a shared box.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validate_ticket import cleanup_docker  # noqa: E402


def main() -> int:
    for job in sys.argv[1:]:
        for trial in sorted(Path(job).iterdir()) if Path(job).is_dir() else []:
            if trial.is_dir() and (trial / "config.json").is_file():
                removed = cleanup_docker(trial.name)
                print(f"{trial.name}: removed { {k: len(v) for k, v in removed.items()} }")
    return 0


if __name__ == "__main__":
    sys.exit(main())
