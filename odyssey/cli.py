"""`odyssey` command line: design, compile, validate, account, analyze."""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from odyssey import design as designs
from odyssey.compile import (
    AGENT_CLI_NAMES, CODE_STATES, EGRESS_ALLOWED_HOSTS, EGRESS_MODES, FEEDBACKS, FRAMINGS, ArmOptions, compile_many,
    harbor_commands,
)
from odyssey.spec import Sequence, SpecError, load_sequence

# Subcommands implemented in modules owned elsewhere; imported only when used,
# so the rest of the CLI works before they exist.
_DELEGATED = {
    "account": ("odyssey.accounting", "per-segment cost rows from Harbor job dirs (SPEC section 7)"),
    "analyze": ("odyssey.analyze", "warm/cold ratios and curves from segments.csv (SPEC section 8)"),
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in _DELEGATED:
        return _delegate(argv[0], argv[1:])
    args = _parser().parse_args(argv)
    if not hasattr(args, "func"):
        _parser().print_help()
        return 2
    try:
        return args.func(args)
    except (SpecError, ValueError, KeyError, FileExistsError, RuntimeError) as exc:
        print(f"odyssey {args.command}: {exc}", file=sys.stderr)
        return 2


def _delegate(command: str, argv: list[str]) -> int:
    module_name, _ = _DELEGATED[command]
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise
        print(f"odyssey {command}: not available yet ({module_name} is not installed)", file=sys.stderr)
        return 2
    result = module.main(argv)
    return int(result or 0)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="odyssey", description="SWE-Odyssey v0 tooling (docs/SPEC.md).")
    sub = parser.add_subparsers(dest="command", metavar="command")

    p = sub.add_parser("design", help="print the orderings a design produces")
    p.add_argument("sequence", type=Path, help="sequence directory")
    _ordering_args(p, allow_explicit=False)
    p.set_defaults(func=_cmd_design)

    p = sub.add_parser("compile", help="compile orderings x one arm into Harbor task dirs")
    p.add_argument("sequence", type=Path, help="sequence directory")
    p.add_argument("--out", type=Path, required=True, help="directory for the generated task dirs")
    _ordering_args(p, allow_explicit=True)
    p.add_argument("--code-state", choices=CODE_STATES, default="forward-merge")
    p.add_argument("--framing", choices=FRAMINGS, default="informed")
    p.add_argument("--feedback", choices=FEEDBACKS, default="none")
    p.add_argument("--seed-notes", type=Path, metavar="FILE",
                   help="expert notes copied to <memory_dir>/NOTES.md before segment 1")
    p.add_argument("--egress", choices=EGRESS_MODES, default="open",
                   help="allowlist: the agent container reaches only "
                        f"{' and '.join(EGRESS_ALLOWED_HOSTS)} through Harbor's egress sidecar, "
                        "every connection is logged, and the agent CLIs are baked into the image "
                        "(docs/SPEC.md section 11). Default open: unrestricted, as before")
    p.add_argument("--egress-allow-host", action="append", default=[], metavar="HOST",
                   help="with --egress allowlist: also admit this host (repeatable), e.g. the "
                        "Bedrock endpoint bedrock-runtime.us-east-1.amazonaws.com")
    p.add_argument("--agent-cli", action="append", default=[], choices=AGENT_CLI_NAMES,
                   help="with --egress allowlist: also bake this harness into the image (repeatable); "
                        "run it with the preinstalled wrappers in scripts/open_models/harbor_agents.py")
    p.add_argument("--allow-leak", action="store_true",
                   help="compile single-defect orderings longer than one ticket even though segment 1 "
                        "exposes every later ticket's fix (docs/SPEC.md section 4)")
    p.set_defaults(func=_cmd_compile)

    p = sub.add_parser("validate", help="check the ticket contract (SPEC section 3)")
    p.add_argument("sequence", type=Path, help="sequence directory")
    p.add_argument("--repeat", type=int, default=1, help="run each grading condition N times (flakiness)")
    p.add_argument("--no-docker", action="store_true", help="run checks on the host in copies of --tree")
    p.add_argument("--tree", type=Path,
                   help="base tree for --no-docker: a directory or a tarball (default: the sequence's base_tree)")
    p.add_argument("--subsets", action="store_true",
                   help="also check every subset of the other tickets' gold (2^(T-1) runs per ticket)")
    p.add_argument("--subset-sample", type=int, metavar="K",
                   help="with --subsets: check K subsets per ticket (every single other gold, every "
                        "'all others but one', then a seeded random fill) instead of all 2^(T-1)-2")
    p.add_argument("--seed", type=int, default=0, help="seed for --subset-sample")
    p.add_argument("--jobs", type=int, default=1, help="grader runs to execute at once")
    p.add_argument("--only", metavar="IDS",
                   help="comma-separated tickets whose grader checks to run (iteration aid; a full "
                        "validation runs them all)")
    p.add_argument("--no-network", action="store_true",
                   help="docker mode: run every check with --network none, whatever allow_internet says")
    p.add_argument("--image", help="docker mode: use this image instead of building the sequence's context")
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("base-manifest",
                       help="hash the base snapshot of the sequence image (or a tree) into a base manifest")
    p.add_argument("sequence", type=Path, help="sequence directory")
    p.add_argument("--image", help="use this image instead of building the sequence's context")
    p.add_argument("--tree", type=Path, help="hash this directory or tarball instead of an image (no Docker)")
    p.add_argument("--out", type=Path, help="write here (e.g. <sequence>/base.manifest) instead of stdout")
    p.set_defaults(func=_cmd_base_manifest)

    for name, (_, help_text) in _DELEGATED.items():
        sub.add_parser(name, help=help_text, add_help=False)
    return parser


def _ordering_args(p: argparse.ArgumentParser, *, allow_explicit: bool) -> None:
    group = p.add_mutually_exclusive_group(required=True)
    if allow_explicit:
        group.add_argument("--ordering", help="comma-separated ticket ids, e.g. LED-1,LED-3,LED-2")
    group.add_argument("--design", choices=designs.DESIGNS)
    p.add_argument("--n", type=int, help="random design: number of orderings (default: number of tickets)")
    p.add_argument("--seed", type=int, default=0, help="random design: seed")
    p.add_argument("--subset", help="comma-separated ticket ids to draw the design from")


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _orderings(seq: Sequence, args) -> list[designs.Ordering]:
    pool = seq.ticket_ids
    if args.subset:
        pool = _split(args.subset)
        for ticket_id in pool:
            seq.ticket(ticket_id)
    if getattr(args, "ordering", None):
        tickets = _split(args.ordering)
        for ticket_id in tickets:
            seq.ticket(ticket_id)
        return [designs.explicit(tickets)]
    return designs.generate(args.design, pool, n=args.n, seed=args.seed)


def _cmd_design(args) -> int:
    seq = load_sequence(args.sequence)
    orderings = _orderings(seq, args)
    for o in orderings:
        print(f"{o.id}  {' '.join(o.tickets)}")
    if args.design in ("williams", "latin") and len(orderings[0].tickets) > 1:
        tickets = list(orderings[0].tickets)
        print(f"\nposition balanced: {designs.is_position_balanced(orderings, tickets)}; "
              f"carryover balanced: {designs.is_carryover_balanced(orderings, tickets)}")
    return 0


def _cmd_compile(args) -> int:
    seq = load_sequence(args.sequence)
    arm = ArmOptions(args.code_state, args.framing, args.feedback, args.seed_notes, args.egress,
                     egress_allow_hosts=tuple(args.egress_allow_host), agent_clis=tuple(args.agent_cli))
    orderings = _orderings(seq, args)
    task_dirs = compile_many(seq, orderings, arm, args.out, allow_leak=args.allow_leak)
    print(f"compiled {len(task_dirs)} task dir(s) for arm {arm.arm_id}:")
    for task_dir, ordering in zip(task_dirs, orderings, strict=True):
        oracle, agent = harbor_commands(task_dir)
        print(f"\n{task_dir}  [{ordering.id}: {' '.join(ordering.tickets)}]")
        print(f"  {oracle}")
        print(f"  {agent}")
    return 0


def _cmd_validate(args) -> int:
    from odyssey import validate as v

    seq = load_sequence(args.sequence)
    if args.repeat < 1:
        raise ValueError("--repeat must be >= 1")
    if args.jobs < 1:
        raise ValueError("--jobs must be >= 1")
    if args.subset_sample is not None and (args.subset_sample < 1 or not args.subsets):
        raise ValueError("--subset-sample needs --subsets and a positive count")
    if args.no_docker:
        tree = args.tree or seq.environment.base_tree
        if tree is None:
            raise ValueError("--no-docker needs --tree <directory or tarball of the base tree> "
                             "(or [environment].base_tree in sequence.toml)")
        runner = v.LocalRunner(seq, tree)
    else:
        runner = v.DockerRunner(seq, args.image, network=False if args.no_network else None)
    try:
        only = None
        if args.only:
            only = _split(args.only)
            for ticket_id in only:
                seq.ticket(ticket_id)
        checks = v.validate(seq, runner, repeat=args.repeat, subsets=args.subsets, jobs=args.jobs,
                            subset_sample=args.subset_sample, seed=args.seed, only=only)
    finally:
        runner.close()
    print(v.format_report(checks))
    return 0 if all(c.ok for c in checks) else 1


def _cmd_base_manifest(args) -> int:
    """Write the manifest a sequence without a host base_tree checks in as base_manifest."""
    from odyssey import validate as v
    from odyssey.compile import tree_manifest

    seq = load_sequence(args.sequence, require_base_manifest=False)
    if args.tree is not None:
        runner = v.LocalRunner(seq, args.tree)
        try:
            text = tree_manifest(runner.pristine)
        finally:
            runner.close()
    else:
        text = v.DockerRunner(seq, args.image).manifest()
    if args.out is not None:
        args.out.write_text(text)
        print(f"wrote {args.out} ({len(text.splitlines())} entries)")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
