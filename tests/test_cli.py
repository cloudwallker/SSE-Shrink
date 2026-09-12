from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[1]


def run_cli(*args: object, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    source_path = str(PROJECT_ROOT / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_path if not existing else os.pathsep.join((source_path, existing))
    )
    return subprocess.run(
        [sys.executable, "-m", "sse_shrink", *(str(arg) for arg in args)],
        cwd=cwd or PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def write_predicate(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_help_and_version_are_available_from_module_entrypoint() -> None:
    help_result = run_cli("--help")
    version_result = run_cli("--version")

    assert help_result.returncode == 0
    assert "minimize" in help_result.stdout
    assert "demo" in help_result.stdout
    assert version_result.returncode == 0
    assert version_result.stdout.strip() == "sse-shrink 0.1.0"


def test_minimize_exports_two_required_events_and_one_json_object(tmp_path: Path) -> None:
    input_path = tmp_path / "private-input.sse"
    input_path.write_bytes(b"data: noise\n\ndata: A\n\ndata: noise2\n\ndata: B\n\n")
    predicate_path = write_predicate(
        tmp_path / "private predicate.py",
        "def fails(data):\n    return b'data: A' in data and b'data: B' in data\n",
    )
    output_dir = tmp_path / "bundle"

    result = run_cli(
        "minimize",
        input_path,
        "--predicate",
        f"{predicate_path}:fails",
        "--output",
        output_dir,
        "--repeat",
        1,
        "--json",
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert result.stdout.count("\n") == 1
    assert report["kept_event_indices"] == [2, 4]
    assert report["original_events"] == 4
    assert report["reduced_events"] == 2
    assert report["baseline_verified"] is True
    assert report["minimality_verified"] is True
    assert output_dir.joinpath("minimal.sse").read_bytes() == b"data: A\n\ndata: B\n\n"
    assert not output_dir.joinpath("predicate.py").exists()
    combined = result.stdout + result.stderr + output_dir.joinpath("report.json").read_text("utf-8")
    assert "data: A" not in combined
    assert str(input_path.resolve()) not in combined
    assert str(predicate_path.resolve()) not in combined


@pytest.mark.parametrize(
    ("args", "expected_fragment"),
    [
        (("--max-calls", "0"), "positive integer"),
        (("--repeat", "-1"), "positive integer"),
        (("--timeout", "nan"), "positive number"),
        (("--max-bytes", "0"), "positive integer"),
        (("--max-events", "0"), "positive integer"),
    ],
)
def test_invalid_numeric_options_exit_2(
    tmp_path: Path, args: tuple[str, str], expected_fragment: str
) -> None:
    input_path = tmp_path / "input.sse"
    input_path.write_bytes(b"data: A\n\n")
    predicate_path = write_predicate(
        tmp_path / "predicate.py", "def fails(data):\n    return True\n"
    )

    result = run_cli(
        "minimize",
        input_path,
        "--predicate",
        f"{predicate_path}:fails",
        "--output",
        tmp_path / "output",
        *args,
    )

    assert result.returncode == 2
    assert expected_fragment in result.stderr


def test_input_is_read_with_a_strict_byte_limit(tmp_path: Path) -> None:
    input_path = tmp_path / "input.sse"
    input_path.write_bytes(b"123456")
    predicate_path = write_predicate(
        tmp_path / "predicate.py", "def fails(data):\n    return False\n"
    )

    result = run_cli(
        "minimize",
        input_path,
        "--predicate",
        f"{predicate_path}:fails",
        "--output",
        tmp_path / "output",
        "--max-bytes",
        5,
    )

    assert result.returncode == 2
    assert "max_bytes" in result.stderr
    assert not (tmp_path / "output").exists()


def test_absent_target_exits_3_without_writing_bundle(tmp_path: Path) -> None:
    input_path = tmp_path / "input.sse"
    input_path.write_bytes(b"data: harmless\n\n")
    predicate_path = write_predicate(
        tmp_path / "predicate.py", "def fails(data):\n    return False\n"
    )
    output_dir = tmp_path / "output"

    result = run_cli(
        "minimize",
        input_path,
        "--predicate",
        f"{predicate_path}:fails",
        "--output",
        output_dir,
        "--repeat",
        1,
    )

    assert result.returncode == 3
    assert not output_dir.exists()


def test_predicate_error_exits_4_without_exposing_output_or_exception(tmp_path: Path) -> None:
    input_path = tmp_path / "PRIVATE_INPUT_NAME.sse"
    input_path.write_bytes(b"PRIVATE_STREAM_CANARY\n\n")
    predicate_path = write_predicate(
        tmp_path / "PRIVATE_PREDICATE_NAME.py",
        "def fails(data):\n"
        "    print('PRIVATE_OUTPUT_CANARY')\n"
        "    raise RuntimeError('PRIVATE_EXCEPTION_CANARY')\n",
    )

    result = run_cli(
        "minimize",
        input_path,
        "--predicate",
        f"{predicate_path}:fails",
        "--output",
        tmp_path / "output",
    )

    assert result.returncode == 4
    combined = result.stdout + result.stderr
    for private_value in (
        "PRIVATE_STREAM_CANARY",
        "PRIVATE_OUTPUT_CANARY",
        "PRIVATE_EXCEPTION_CANARY",
        str(input_path.resolve()),
        str(predicate_path.resolve()),
    ):
        assert private_value not in combined


def test_unverified_baseline_budget_exhaustion_exits_5_without_bundle(tmp_path: Path) -> None:
    input_path = tmp_path / "input.sse"
    input_path.write_bytes(b"data: target\n\n")
    predicate_path = write_predicate(
        tmp_path / "predicate.py", "def fails(data):\n    return True\n"
    )
    output_dir = tmp_path / "output"

    result = run_cli(
        "minimize",
        input_path,
        "--predicate",
        f"{predicate_path}:fails",
        "--output",
        output_dir,
        "--max-calls",
        1,
        "--repeat",
        2,
        "--json",
    )

    assert result.returncode == 5
    assert json.loads(result.stdout)["status"] == "budget_exhausted"
    assert not output_dir.exists()


def test_unverified_baseline_human_output_does_not_claim_bundle_exists(tmp_path: Path) -> None:
    input_path = tmp_path / "input.sse"
    input_path.write_bytes(b"data: target\n\n")
    predicate_path = write_predicate(
        tmp_path / "predicate.py", "def fails(data):\n    return True\n"
    )

    result = run_cli(
        "minimize",
        input_path,
        "--predicate",
        f"{predicate_path}:fails",
        "--output",
        tmp_path / "output",
        "--max-calls",
        1,
        "--repeat",
        2,
    )

    assert result.returncode == 5
    assert "bundle:" not in result.stdout
    assert "reproduce:" not in result.stdout


def test_verified_budget_result_is_exported_and_exits_5(tmp_path: Path) -> None:
    input_path = tmp_path / "input.sse"
    original = b"data: required\n\ndata: noise\n\n"
    input_path.write_bytes(original)
    predicate_path = write_predicate(
        tmp_path / "predicate.py",
        "def fails(data):\n    return b'data: required' in data\n",
    )
    output_dir = tmp_path / "output"

    result = run_cli(
        "minimize",
        input_path,
        "--predicate",
        f"{predicate_path}:fails",
        "--output",
        output_dir,
        "--max-calls",
        3,
        "--repeat",
        1,
        "--json",
    )

    assert result.returncode == 5
    report = json.loads(result.stdout)
    assert report["status"] == "budget_exhausted"
    assert report["baseline_verified"] is True
    assert report["minimality_verified"] is False
    assert output_dir.joinpath("minimal.sse").read_bytes() == original


def test_all_output_collisions_are_checked_before_force_writes(tmp_path: Path) -> None:
    input_path = tmp_path / "input.sse"
    input_path.write_bytes(b"data: required\n\n")
    predicate_path = write_predicate(
        tmp_path / "predicate.py", "def fails(data):\n    return b'required' in data\n"
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    report = output_dir / "report.json"
    report.write_bytes(b"OLD_REPORT")

    result = run_cli(
        "minimize",
        input_path,
        "--predicate",
        f"{predicate_path}:fails",
        "--output",
        output_dir,
        "--repeat",
        1,
    )

    assert result.returncode == 2
    assert not output_dir.joinpath("minimal.sse").exists()
    assert report.read_bytes() == b"OLD_REPORT"


def test_force_never_overwrites_hardlink_alias_of_input(tmp_path: Path) -> None:
    input_path = tmp_path / "input.sse"
    original = b"data: required\n\n"
    input_path.write_bytes(original)
    predicate_path = write_predicate(
        tmp_path / "predicate.py", "def fails(data):\n    return b'required' in data\n"
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    os.link(input_path, output_dir / "minimal.sse")

    result = run_cli(
        "minimize",
        input_path,
        "--predicate",
        f"{predicate_path}:fails",
        "--output",
        output_dir,
        "--repeat",
        1,
        "--force",
    )

    assert result.returncode == 2
    assert input_path.read_bytes() == original


def test_force_never_overwrites_hardlink_alias_of_predicate(tmp_path: Path) -> None:
    input_path = tmp_path / "input.sse"
    input_path.write_bytes(b"data: required\n\n")
    predicate_path = write_predicate(
        tmp_path / "predicate.py", "def fails(data):\n    return b'required' in data\n"
    )
    original = predicate_path.read_bytes()
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    os.link(predicate_path, output_dir / "report.json")

    result = run_cli(
        "minimize",
        input_path,
        "--predicate",
        f"{predicate_path}:fails",
        "--output",
        output_dir,
        "--repeat",
        1,
        "--force",
    )

    assert result.returncode == 2
    assert predicate_path.read_bytes() == original


def test_force_never_follows_symlink_alias_of_input(tmp_path: Path) -> None:
    input_path = tmp_path / "input.sse"
    original = b"data: required\n\n"
    input_path.write_bytes(original)
    predicate_path = write_predicate(
        tmp_path / "predicate.py", "def fails(data):\n    return b'required' in data\n"
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    try:
        (output_dir / "minimal.sse").symlink_to(input_path)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")

    result = run_cli(
        "minimize",
        input_path,
        "--predicate",
        f"{predicate_path}:fails",
        "--output",
        output_dir,
        "--repeat",
        1,
        "--force",
    )

    assert result.returncode == 2
    assert input_path.read_bytes() == original


def test_usage_error_does_not_echo_unknown_private_argument_value(tmp_path: Path) -> None:
    input_path = tmp_path / "input.sse"
    input_path.write_bytes(b"data: target\n\n")
    predicate_path = write_predicate(
        tmp_path / "predicate.py", "def fails(data):\n    return True\n"
    )
    result = run_cli(
        "minimize",
        input_path,
        "--predicate",
        f"{predicate_path}:fails",
        "--output",
        tmp_path / "output",
        "--unknown",
        "PRIVATE_PATH_CANARY",
        "--json",
    )

    assert result.returncode == 2
    assert result.stdout.count("\n") == 1
    assert json.loads(result.stdout)["category"] == "input"
    assert "PRIVATE_PATH_CANARY" not in result.stdout + result.stderr
