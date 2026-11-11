#!/usr/bin/env python3
"""Deploy atlas-commerce components from this repository to the platform's AWS account.

    python3 deploy/deploy.py --list                    show the components and their order
    python3 deploy/deploy.py --component orders_core   deploy one component (repeatable)
    python3 deploy/deploy.py --all                     deploy every component, in order

A component is a module in deploy/components/ with a `deploy()` function and an integer
`ORDER` (lower deploys first). Components are idempotent: they create what is missing and bring
what exists in line with the repository. Credentials and the endpoint come from the usual AWS
environment (AWS_ENDPOINT_URL, AWS_SHARED_CREDENTIALS_FILE, AWS_DEFAULT_REGION).
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def load_components() -> dict:
    components = {}
    for path in sorted((HERE / "components").glob("*.py")):
        if path.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f"atlas_components.{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not callable(getattr(module, "deploy", None)):
            raise SystemExit(f"deploy/components/{path.name} has no deploy() function")
        components[path.stem] = module
    return dict(sorted(components.items(), key=lambda item: (getattr(item[1], "ORDER", 100), item[0])))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="list components in deploy order")
    group.add_argument("--all", action="store_true", help="deploy every component")
    group.add_argument("--component", action="append", metavar="NAME", help="deploy this component")
    args = parser.parse_args(argv)

    components = load_components()
    if args.list:
        for name, module in components.items():
            summary = (module.__doc__ or "").strip().splitlines()[0] if module.__doc__ else ""
            print(f"{getattr(module, 'ORDER', 100):4d}  {name:24s} {summary}")
        return 0
    if args.all:
        selected = list(components)
    else:
        unknown = [name for name in args.component if name not in components]
        if unknown:
            parser.error(f"unknown component(s): {', '.join(unknown)}; known: {', '.join(components)}")
        selected = [name for name in components if name in args.component]

    for name in selected:
        start = time.monotonic()
        print(f"[deploy] == {name}", flush=True)
        components[name].deploy()
        print(f"[deploy] == {name} done in {time.monotonic() - start:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
