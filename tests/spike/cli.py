"""``python -m spike`` command line.

Two commands:

``check``
    Validate everything the host must provide -- Docker and its daemon, the
    checkout, and the mounted SDK layout -- and print the expected paths.
    Nothing is built and no container is started.

``run``
    Do the same checks, build the runner image, run the in-container probe,
    compute the #370 verdict and write ``verdict.json`` and ``verdict.md``.
    ``--dry-run`` prints the exact Docker commands instead of running them.

Exit codes: ``0`` success (including a ``partial`` or ``fail`` verdict, which is
still a completed experiment), ``1`` the probe crashed or timed out, ``2`` a
missing or invalid prerequisite. Every non-zero path prints one message naming
what is missing or broken.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Mapping, Optional, Sequence, Tuple

from .criteria import compute_verdict, exit_code, format_table
from .procedure import (
    DEFAULT_BASE_IMAGE,
    DEFAULT_IMAGE,
    EXIT_INCOMPLETE,
    EXIT_OK,
    EXIT_PREREQ,
    SpikePlan,
    plan_lines,
    run_spike,
)
from .prereqs import (
    ENV_VARS,
    HostStatus,
    PrereqError,
    check_host,
    describe_environment,
)
from .report import build_document, write_verdict

# tests/spike/cli.py -> tests/spike -> tests -> repository root
TESTS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = TESTS_ROOT.parent
DEFAULT_ARTIFACTS = TESTS_ROOT / "artifacts" / "spike"


def _repo_root(value: Optional[str]) -> Path:
    return Path(value).resolve() if value else DEFAULT_REPO


def _environment_with_sdk(sdk: Optional[str], environ: Optional[Mapping[str, str]]) -> dict:
    """Return a copy of the environment with ``--sdk`` taking precedence."""
    merged = dict(os.environ if environ is None else environ)
    if sdk:
        merged[ENV_VARS[0]] = sdk
    return merged


def _parse_intervals(text: str) -> Tuple[int, ...]:
    values: List[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{part!r} is not an integer") from None
        if not 0 <= value <= 60000:
            raise argparse.ArgumentTypeError(
                f"poll interval must be 0..60000 ms, got {value}"
            )
        values.append(value)
    if not values:
        raise argparse.ArgumentTypeError("at least one poll interval is required")
    return tuple(values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spike",
        description="Connect IQ simulator under Wine -- feasibility spike for issue #370",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The SDK is never committed or baked into an image. Point "
            "CIQ_SDK_HOST_PATH (or --sdk) at a local SDK 9.3.0 with linux/bin/monkeyc, "
            "linux/bin/monkeydo and windows/bin/simulator.exe.\n\n"
            "Examples:\n"
            "  cd tests && python -m spike check\n"
            "  cd tests && python -m spike run --sdk /opt/garmin/connectiq-sdk-9.3.0\n"
            "  cd tests && python -m spike run --dry-run\n"
        ),
    )
    subparsers = parser.add_subparsers(dest="command")

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--sdk", help="SDK root on the host (overrides CIQ_SDK_HOST_PATH)")
        sub.add_argument("--repo", help=f"repository checkout (default: {DEFAULT_REPO})")
        sub.add_argument("--docker", default="docker", help="Docker CLI (default: docker)")
        sub.add_argument(
            "--artifacts",
            help=f"artifact directory (default: {DEFAULT_ARTIFACTS})",
        )
        sub.add_argument(
            "--no-docker",
            action="store_true",
            help="do not require a reachable Docker daemon (configuration check only)",
        )

    check = subparsers.add_parser(
        "check", help="validate Docker, the checkout and the SDK without starting anything"
    )
    add_common(check)
    check.set_defaults(handler=_cmd_check)

    run = subparsers.add_parser(
        "run", help="build the runner image, run the procedures and write the verdict"
    )
    add_common(run)
    run.add_argument("--button-device", default="fenix6pro", help="button device (default: fenix6pro)")
    run.add_argument("--touch-device", default="venu3", help="touch device (default: venu3)")
    run.add_argument("--language", default="deu", help="language code to switch to (default: deu)")
    run.add_argument(
        "--poll-intervals",
        type=_parse_intervals,
        default=(0, 1000),
        help="comma-separated pollingInterval values in ms (default: 0,1000)",
    )
    run.add_argument("--image", default=DEFAULT_IMAGE, help=f"runner image tag (default: {DEFAULT_IMAGE})")
    run.add_argument(
        "--base-image",
        default=DEFAULT_BASE_IMAGE,
        help=f"runner base image build arg (default: {DEFAULT_BASE_IMAGE})",
    )
    run.add_argument("--timeout", type=float, default=1800.0, help="probe timeout in seconds")
    run.add_argument("--build-timeout", type=float, default=1800.0, help="image build timeout in seconds")
    run.add_argument("--skip-build", action="store_true", help="reuse an existing image")
    run.add_argument("--keep-container", action="store_true", help="do not remove a timed-out container")
    run.add_argument("--dry-run", action="store_true", help="print the Docker commands and exit")
    run.add_argument("--json", action="store_true", help="print the verdict JSON to stdout")
    run.set_defaults(handler=_cmd_run)

    return parser


def _status_or_exit(status: HostStatus, repo: Path) -> int:
    for line in describe_environment(status, repo).splitlines():
        print(line)
    if status.problems:
        print(f"error: {status.message()}", file=sys.stderr)
        return EXIT_PREREQ
    return EXIT_OK


def _cmd_check(args: argparse.Namespace) -> int:
    repo = _repo_root(args.repo)
    environ = _environment_with_sdk(args.sdk, None)
    status = check_host(
        environ,
        repo,
        docker_binary=args.docker,
        require_docker=not args.no_docker,
    )
    return _status_or_exit(status, repo)


def _resolve_plan(args: argparse.Namespace) -> Tuple[Optional[SpikePlan], int]:
    repo = _repo_root(args.repo)
    environ = _environment_with_sdk(args.sdk, None)
    status = check_host(
        environ,
        repo,
        docker_binary=args.docker,
        require_docker=not args.dry_run,
    )
    if status.problems:
        print(f"error: {status.message()}", file=sys.stderr)
        return None, EXIT_PREREQ
    assert status.sdk is not None
    artifacts = Path(args.artifacts) if args.artifacts else DEFAULT_ARTIFACTS
    try:
        artifacts.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"error: artifacts directory ({artifacts} is not writable: {error})", file=sys.stderr)
        return None, EXIT_PREREQ
    plan = SpikePlan(
        repo_root=repo,
        sdk=status.sdk,
        artifacts=artifacts,
        image=args.image,
        base_image=args.base_image,
        docker=status.docker or args.docker,
        button_device=args.button_device,
        touch_device=args.touch_device,
        language=args.language,
        poll_intervals=tuple(args.poll_intervals),
        timeout_s=args.timeout,
        build_timeout_s=args.build_timeout,
        skip_build=args.skip_build,
        keep_container=args.keep_container,
    )
    return plan, EXIT_OK


def _cmd_run(args: argparse.Namespace) -> int:
    plan, code = _resolve_plan(args)
    if plan is None:
        return code
    if args.dry_run:
        for line in plan_lines(plan):
            print(line)
        return EXIT_OK

    result = run_spike(plan)
    if result.exit_code != EXIT_OK:
        print(f"error: {result.message}", file=sys.stderr)
        return result.exit_code
    if result.probe is None:
        print(f"error: the probe produced no result; see {result.artifacts}", file=sys.stderr)
        return EXIT_INCOMPLETE

    verdict = compute_verdict(result.probe)
    paths = write_verdict(
        verdict,
        result.probe,
        artifacts_root=result.artifacts,
        generated_at=time.time(),
    )
    if args.json:
        document = build_document(
            verdict,
            result.probe,
            generated_at=time.time(),
            artifacts_root=result.artifacts,
        )
        print(json.dumps(document, indent=2))
    else:
        print()
        print(format_table(verdict))
        print()
        print(f"json:     {paths['json']}")
        print(f"markdown: {paths['markdown']}")
        if verdict.unknown_checks:
            print(
                "note:     checks not mapped to a #370 criterion were kept in the "
                "JSON: " + ", ".join(verdict.unknown_checks)
            )
    return exit_code(verdict)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return EXIT_PREREQ
    try:
        return handler(args)
    except PrereqError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_PREREQ
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_INCOMPLETE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
