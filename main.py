"""
main.py
=======
Lightweight CLI entry-point for the Multi-Source Candidate Data Transformer.

Usage
-----
  python main.py \\
      --source ats:data/ats_source.json \\
      --source github:data/github_source.json \\
      --config data/runtime_config.json \\
      [--pretty] \\
      [--log-level DEBUG|INFO|WARNING|ERROR]

Each ``--source`` flag takes the form ``<type>:<path>`` where ``<type>`` is one
of the registered adapter labels (``ats``, ``github``).

Output
------
  A JSON array written to stdout — one object per resolved candidate entity.
  Errors and diagnostics go to stderr so stdout can be piped cleanly.

Exit codes
----------
  0 — Success (≥0 candidates emitted).
  1 — CLI argument error.
  2 — Missing required field (on_missing=error policy triggered).
  3 — Unexpected runtime error.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List, Tuple

# Ensure stdout is UTF-8 on Windows (handles accented names like Léa, Müller).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


from models import RuntimeConfig, SourceAuthorityMatrix
from pipeline import MissingRequiredFieldError, run_pipeline


# ---------------------------------------------------------------------------
# Logging bootstrap
# ---------------------------------------------------------------------------


def _configure_logging(level: str) -> None:
    """Set up structured logging to stderr at the requested level."""
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        stream=sys.stderr,
        level=numeric,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="candidate-transformer",
        description=(
            "Multi-Source Candidate Data Transformer — "
            "ingests disparate candidate sources and emits a unified canonical profile."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic run with ATS + GitHub sources
  python main.py \\
      --source ats:data/ats_source.json \\
      --source github:data/github_source.json \\
      --config data/runtime_config.json

  # Pretty-print output with debug logging
  python main.py \\
      --source ats:data/ats_source.json \\
      --source github:data/github_source.json \\
      --config data/runtime_config.json \\
      --pretty --log-level DEBUG
        """,
    )
    parser.add_argument(
        "--source",
        action="append",
        metavar="TYPE:PATH",
        dest="sources",
        required=True,
        help=(
            "Source file in the format <type>:<path>. "
            "Supported types: ats, github. "
            "May be repeated for multiple sources."
        ),
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        required=True,
        help="Path to the runtime_config.json file.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        default=False,
        help="Pretty-print JSON output with 2-space indentation.",
    )
    parser.add_argument(
        "--log-level",
        metavar="LEVEL",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    return parser


def _parse_sources(raw_sources: List[str]) -> List[Tuple[str, Path]]:
    """
    Parse ``--source type:path`` arguments into ``(source_type, Path)`` pairs.

    Raises
    ------
    SystemExit(1)
        If any argument is malformed or the referenced file does not exist.
    """
    result: List[Tuple[str, Path]] = []
    for raw in raw_sources:
        if ":" not in raw:
            print(
                f"ERROR: --source argument '{raw}' must be in the format TYPE:PATH "
                "(e.g. ats:data/ats_source.json).",
                file=sys.stderr,
            )
            sys.exit(1)
        source_type, _, path_str = raw.partition(":")
        source_type = source_type.strip().lower()
        path = Path(path_str.strip())
        if not path.exists():
            print(
                f"ERROR: Source file does not exist: '{path}'",
                file=sys.stderr,
            )
            sys.exit(1)
        result.append((source_type, path))
    return result


# ---------------------------------------------------------------------------
# Main entry-point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse CLI args, run the pipeline, and emit JSON to stdout."""
    parser = _build_arg_parser()
    args = parser.parse_args()

    _configure_logging(args.log_level)
    logger = logging.getLogger("main")

    # --- Validate config path ---
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"ERROR: Config file does not exist: '{config_path}'", file=sys.stderr)
        sys.exit(1)

    # --- Load runtime config ---
    try:
        config = RuntimeConfig.from_file(str(config_path))
        logger.info("Loaded runtime config from '%s'.", config_path)
    except Exception as exc:
        print(f"ERROR: Failed to load runtime config: {exc}", file=sys.stderr)
        sys.exit(3)

    # --- Parse source paths ---
    source_paths = _parse_sources(args.sources)
    logger.info(
        "Ingesting %d source file(s): %s",
        len(source_paths),
        ", ".join(f"{t}:{p}" for t, p in source_paths),
    )

    # --- Run pipeline ---
    try:
        sam = SourceAuthorityMatrix()
        results = list(run_pipeline(source_paths, config, sam))
        logger.info("Pipeline complete. %d candidate(s) resolved.", len(results))
    except MissingRequiredFieldError as exc:
        print(f"PIPELINE ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        logger.exception("Unexpected pipeline failure.")
        print(f"PIPELINE ERROR: {exc}", file=sys.stderr)
        sys.exit(3)

    # --- Emit JSON to stdout ---
    indent = 2 if args.pretty else None
    print(json.dumps(results, indent=indent, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
