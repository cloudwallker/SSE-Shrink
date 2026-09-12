from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_cli import PROJECT_ROOT, run_cli

from sse_shrink.demo import build_synthetic_stream, fails
from sse_shrink.errors import InputError
from sse_shrink.framing import frame_stream
from sse_shrink.minimizer import minimize
from sse_shrink.runner import SubprocessPredicate


def test_synthetic_target_requires_the_two_deliberately_faulty_events() -> None:
    stream = frame_stream(build_synthetic_stream())
    start = next(frame.raw for frame in stream.frames if b'"message_start"' in frame.raw)
    delta = next(frame.raw for frame in stream.frames if b'"message_delta"' in frame.raw)

    assert len(stream.frames) >= 30
    assert fails(start + delta) is True
    assert fails(start) is False
    assert fails(delta) is False
    assert fails(b"data: {not-json}\n\n") is False


def test_shipped_example_predicate_reproduces_only_the_intentional_target() -> None:
    stream = frame_stream(build_synthetic_stream())
    start = next(frame.raw for frame in stream.frames if b'"message_start"' in frame.raw)
    delta = next(frame.raw for frame in stream.frames if b'"message_delta"' in frame.raw)
    predicate = SubprocessPredicate(
        f"{PROJECT_ROOT / 'examples' / 'deliberately_faulty_predicate.py'}:fails"
    )

    assert predicate(start + delta) is True
    assert predicate(delta) is False


def test_demo_exports_two_event_relocatable_standalone_bundle(tmp_path: Path) -> None:
    output_dir = tmp_path / "demo-output"

    result = run_cli("demo", "--output", output_dir, "--json")

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["synthetic_demo"] is True
    assert report["original_events"] >= 30
    assert report["reduced_events"] == 2
    assert report["kept_event_indices"] == sorted(report["kept_event_indices"])
    assert report["status"] == "complete"
    assert report["minimality_verified"] is True
    assert report["baseline_verified"] is True
    assert set(path.name for path in output_dir.iterdir()) == {
        "README.md",
        "minimal.sse",
        "predicate.py",
        "report.json",
        "test_repro.py",
    }

    relocated = tmp_path / "moved" / "reproduction"
    relocated.parent.mkdir()
    shutil.move(str(output_dir), relocated)
    elsewhere = tmp_path / "unrelated-working-directory"
    elsewhere.mkdir()
    environment = os.environ.copy()
    source_path = str(PROJECT_ROOT / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_path if not existing else os.pathsep.join((source_path, existing))
    )
    verification = subprocess.run(
        [sys.executable, "-m", "pytest", str(relocated / "test_repro.py"), "-q"],
        cwd=elsewhere,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert verification.returncode == 0, verification.stdout + verification.stderr
    assert "1 passed" in verification.stdout


def test_custom_bundle_uses_environment_predicate_without_copying_private_code(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.sse"
    input_path.write_bytes(b"data: required\n\ndata: noise\n\n")
    predicate_path = write_private_predicate(tmp_path / "private.py")
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
    )

    assert result.returncode == 0, result.stderr
    assert not output_dir.joinpath("predicate.py").exists()
    test_text = output_dir.joinpath("test_repro.py").read_text("utf-8")
    readme_text = output_dir.joinpath("README.md").read_text("utf-8")
    assert "SSE_SHRINK_PREDICATE" in test_text
    assert "SSE_SHRINK_PREDICATE" in readme_text
    assert str(predicate_path.resolve()) not in test_text + readme_text
    assert "assert predicate(data) is True" in test_text
    assert "does not assert that the application bug is fixed" in readme_text

    environment = os.environ.copy()
    source_path = str(PROJECT_ROOT / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_path if not existing else os.pathsep.join((source_path, existing))
    )
    environment["SSE_SHRINK_PREDICATE"] = f"{predicate_path}:fails"
    verification = subprocess.run(
        [sys.executable, "-m", "pytest", str(output_dir / "test_repro.py"), "-q"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert verification.returncode == 0, verification.stdout + verification.stderr


def write_private_predicate(path: Path) -> Path:
    path.write_text("def fails(data):\n    return b'data: required' in data\n", encoding="utf-8")
    return path


def test_report_schema_contains_only_portable_structural_metadata(tmp_path: Path) -> None:
    input_path = tmp_path / "private-input.sse"
    input_path.write_bytes(b"\xef\xbb\xbf\n\ndata: required\n\ndata: noise\n\n")
    predicate_path = write_private_predicate(tmp_path / "private-predicate.py")
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
    )

    assert result.returncode == 0, result.stderr
    report_text = output_dir.joinpath("report.json").read_text("utf-8")
    report = json.loads(report_text)
    assert report == {
        "schema_version": 1,
        "status": "complete",
        "original_bytes": 34,
        "reduced_bytes": 21,
        "original_events": 2,
        "reduced_events": 1,
        "kept_event_indices": [1],
        "calls": report["calls"],
        "cache_hits": report["cache_hits"],
        "minimality_verified": True,
        "baseline_verified": True,
        "fixed_prefix_bytes": 5,
        "incomplete_tail": False,
        "synthetic_demo": False,
    }
    assert str(input_path.resolve()) not in report_text
    assert str(predicate_path.resolve()) not in report_text


def test_human_summary_uses_relative_bundle_names_and_no_stream_body(tmp_path: Path) -> None:
    input_path = tmp_path / "input.sse"
    input_path.write_bytes(b"data: PRIVATE_REQUIRED\n\ndata: PRIVATE_NOISE")
    predicate_path = tmp_path / "predicate.py"
    predicate_path.write_text(
        "def fails(data):\n    return b'PRIVATE_REQUIRED' in data\n", encoding="utf-8"
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
    )

    assert result.returncode == 0, result.stderr
    assert "2 -> 1 events" in result.stdout
    assert "calls" in result.stdout
    assert "1-minimal: yes" in result.stdout
    assert "incomplete tail: yes" in result.stdout
    assert "minimal.sse" in result.stdout
    assert "report.json" in result.stdout
    assert "python -m pytest" in result.stdout
    assert str(output_dir.resolve()) not in result.stdout
    assert "PRIVATE_REQUIRED" not in result.stdout
    assert "PRIVATE_NOISE" not in result.stdout


def test_export_writes_to_the_same_expanduser_directory_that_was_validated(
    tmp_path: Path, monkeypatch
) -> None:
    from sse_shrink.bundle import export_bundle

    home = tmp_path / "fake-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(tmp_path)
    data = b"data: required\n\n"
    stream = frame_stream(data)
    result = minimize(stream, lambda candidate: b"required" in candidate, repeat=1)

    export_bundle(
        Path("~/bundle"),
        original_data=data,
        stream=stream,
        result=result,
        force=False,
        protected_paths=(),
        synthetic_demo=False,
    )

    assert home.joinpath("bundle", "minimal.sse").read_bytes() == data
    assert not tmp_path.joinpath("~").exists()


def test_force_rejects_hardlinked_reserved_targets_before_writing(tmp_path: Path) -> None:
    from sse_shrink.bundle import export_bundle

    data = b"data: required\n\n"
    stream = frame_stream(data)
    result = minimize(stream, lambda candidate: b"required" in candidate, repeat=1)
    output_dir = tmp_path / "bundle"
    output_dir.mkdir()
    minimal_path = output_dir / "minimal.sse"
    report_path = output_dir / "report.json"
    original = b"ORIGINAL_RESERVED_CONTENT"
    minimal_path.write_bytes(original)
    os.link(minimal_path, report_path)

    with pytest.raises(InputError):
        export_bundle(
            output_dir,
            original_data=data,
            stream=stream,
            result=result,
            force=True,
            protected_paths=(),
            synthetic_demo=False,
        )

    assert minimal_path.read_bytes() == original
    assert report_path.read_bytes() == original


def test_exported_test_preserves_the_configured_predicate_timeout(tmp_path: Path) -> None:
    from sse_shrink.bundle import export_bundle

    data = b"data: required\n\n"
    stream = frame_stream(data)
    result = minimize(stream, lambda candidate: b"required" in candidate, repeat=1)
    output_dir = tmp_path / "bundle"
    export_bundle(
        output_dir,
        original_data=data,
        stream=stream,
        result=result,
        force=False,
        protected_paths=(),
        synthetic_demo=False,
        predicate_timeout=0.02,
    )
    predicate_path = tmp_path / "slow.py"
    predicate_path.write_text(
        "import time\ndef fails(data):\n    time.sleep(0.2)\n    return True\n", encoding="utf-8"
    )
    environment = os.environ.copy()
    source_path = str(PROJECT_ROOT / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_path if not existing else os.pathsep.join((source_path, existing))
    )
    environment["SSE_SHRINK_PREDICATE"] = f"{predicate_path}:fails"

    verification = subprocess.run(
        [sys.executable, "-m", "pytest", str(output_dir / "test_repro.py"), "-q"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert verification.returncode == 1
    assert "predicate timed out" in verification.stdout + verification.stderr
