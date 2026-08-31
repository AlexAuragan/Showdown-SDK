"""Build generation-specific Pokémon Showdown dex JSON files.

The Python entrypoint manages a local Node dependency cache under dex/.node and
runs export.cjs, which uses @pkmn/dex + @pkmn/data to resolve historical data.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE_DIR = ROOT / ".node"
NODE_MODULES = NODE_DIR / "node_modules"
EXPORTER = ROOT / "scripts" / "export.cjs"
PACKAGES = ("@pkmn/dex", "@pkmn/data")


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command))
    subprocess.run(command, check=True, env=env)


def require_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise SystemExit(
            f"{name!r} is required. Install Node.js (which includes npm), then rerun this script."
        )
    return executable


def dependencies_installed() -> bool:
    return all((NODE_MODULES / package).exists() for package in PACKAGES)


def ensure_dependencies(npm: str, *, update: bool) -> None:
    NODE_DIR.mkdir(parents=True, exist_ok=True)

    package_json = NODE_DIR / "package.json"
    if not package_json.exists():
        package_json.write_text(
            json.dumps(
                {
                    "private": True,
                    "description": "Local build dependencies for Pokémon Showdown dex export",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    if dependencies_installed() and not update:
        return

    package_specs = [f"{package}@latest" for package in PACKAGES]
    run(
        [
            npm,
            "install",
            "--prefix",
            str(NODE_DIR),
            "--no-audit",
            "--no-fund",
            *package_specs,
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export generation-specific Pokémon Showdown dex JSON into dex/genN/."
    )
    parser.add_argument(
        "--gens",
        type=int,
        nargs="+",
        default=list(range(1, 10)),
        metavar="GEN",
        help="Generations to export (default: 1 2 3 4 5 6 7 8 9)",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Update @pkmn/dex and @pkmn/data before exporting.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Write compact JSON instead of pretty-printed JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    gens = sorted(set(args.gens))
    invalid = [gen for gen in gens if gen < 1 or gen > 9]
    if invalid:
        raise SystemExit(f"Unsupported generation(s): {invalid}. Expected values from 1 to 9.")

    node = require_executable("node")
    npm = require_executable("npm")
    ensure_dependencies(npm, update=args.update)

    env = os.environ.copy()
    existing_node_path = env.get("NODE_PATH")
    env["NODE_PATH"] = (
        str(NODE_MODULES)
        if not existing_node_path
        else os.pathsep.join((str(NODE_MODULES), existing_node_path))
    )

    command = [
        node,
        str(EXPORTER),
        "--output",
        str(ROOT / "dex"),
        "--gens",
        ",".join(str(gen) for gen in gens),
    ]
    if args.compact:
        command.append("--compact")

    run(command, env=env)
    print(f"\nDex export complete: {ROOT}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        print(f"Command failed with exit code {error.returncode}.", file=sys.stderr)
        raise SystemExit(error.returncode) from error
