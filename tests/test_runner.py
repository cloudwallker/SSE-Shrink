from __future__ import annotations

import traceback
from pathlib import Path

import pytest

from sse_shrink.errors import InputError, PredicateError
from sse_shrink.runner import SubprocessPredicate


def write_predicate(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_runs_unicode_path_with_shell_metacharacters_and_binary_stdin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    predicate_path = write_predicate(
        tmp_path / "目录 with spaces & symbols" / "判定 worker.py",
        "import sys\n"
        "def fails(data):\n"
        "    print('CANARY_SECRET')\n"
        "    print('CANARY_SECRET', file=sys.stderr)\n"
        "    return data == b'target\\x00\\xff'\n",
    )
    predicate = SubprocessPredicate(f"{predicate_path}:fails")

    assert predicate(b"target\x00\xff") is True
    assert predicate(b"other") is False
    captured = capsys.readouterr()
    assert "CANARY_SECRET" not in captured.out
    assert "CANARY_SECRET" not in captured.err


def test_resolves_relative_predicate_path_before_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    predicate_dir = tmp_path / "predicate"
    predicate_dir.mkdir()
    write_predicate(predicate_dir / "check.py", "def fails(data):\n    return data == b'ok'\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    monkeypatch.chdir(predicate_dir)
    predicate = SubprocessPredicate("check.py:fails")
    monkeypatch.chdir(elsewhere)

    assert predicate(b"ok") is True


def test_each_call_uses_fresh_module_state(tmp_path: Path) -> None:
    predicate_path = write_predicate(
        tmp_path / "stateful.py",
        "calls = 0\ndef fails(data):\n    global calls\n    calls += 1\n    return calls == 1\n",
    )
    predicate = SubprocessPredicate(f"{predicate_path}:fails")

    assert predicate(b"first") is True
    assert predicate(b"second") is True


def test_predicate_can_import_sibling_and_read_relative_fixture(tmp_path: Path) -> None:
    (tmp_path / "helper.py").write_text(
        "def matches(candidate, expected):\n    return candidate == expected\n", encoding="utf-8"
    )
    (tmp_path / "fixture.bin").write_bytes(b"from fixture")
    predicate_path = write_predicate(
        tmp_path / "predicate.py",
        "from pathlib import Path\n"
        "from helper import matches\n"
        "def fails(data):\n"
        "    return matches(data, Path('fixture.bin').read_bytes())\n",
    )

    predicate = SubprocessPredicate(f"{predicate_path}:fails")

    assert predicate(b"from fixture") is True


def test_predicate_module_is_registered_while_dataclass_decorators_run(tmp_path: Path) -> None:
    predicate_path = write_predicate(
        tmp_path / "dataclass_predicate.py",
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "@dataclass\n"
        "class Config:\n"
        "    value: int\n"
        "def fails(data):\n"
        "    return Config(value=1).value == 1 and data == b'target'\n",
    )

    predicate = SubprocessPredicate(f"{predicate_path}:fails")

    assert predicate(b"target") is True
    assert predicate(b"other") is False


@pytest.mark.parametrize(
    "source",
    [
        "value = True\n",
        "fails = 42\n",
    ],
)
def test_invalid_target_is_a_content_free_predicate_error(tmp_path: Path, source: str) -> None:
    predicate_path = write_predicate(tmp_path / "invalid.py", source)

    with pytest.raises(PredicateError) as caught:
        SubprocessPredicate(f"{predicate_path}:fails")(b"PRIVATE_CANDIDATE")

    message = str(caught.value)
    assert "PRIVATE_CANDIDATE" not in message
    assert str(predicate_path) not in message


def test_integer_return_is_rejected_even_when_truthy(tmp_path: Path) -> None:
    predicate_path = write_predicate(tmp_path / "integer.py", "def fails(data):\n    return 1\n")

    with pytest.raises(PredicateError, match="boolean"):
        SubprocessPredicate(f"{predicate_path}:fails")(b"candidate")


def test_nonzero_exit_rejects_a_written_boolean_without_exposing_content(tmp_path: Path) -> None:
    predicate_path = write_predicate(
        tmp_path / "exits_during_shutdown.py",
        "import atexit\n"
        "import os\n"
        "atexit.register(lambda: os._exit(23))\n"
        "def fails(data):\n"
        "    return bool(data)\n",
    )

    with pytest.raises(PredicateError) as caught:
        SubprocessPredicate(f"{predicate_path}:fails")(b"PRIVATE_CANDIDATE")

    assert "PRIVATE_CANDIDATE" not in str(caught.value)
    assert str(predicate_path) not in str(caught.value)


def test_missing_import_is_a_content_free_predicate_error(tmp_path: Path) -> None:
    predicate_path = write_predicate(
        tmp_path / "missing_import.py",
        "import package_that_does_not_exist_for_sse_shrink_test\n"
        "def fails(data):\n"
        "    return True\n",
    )

    with pytest.raises(PredicateError) as caught:
        SubprocessPredicate(f"{predicate_path}:fails")(b"PRIVATE_CANDIDATE")

    assert "package_that_does_not_exist" not in str(caught.value)
    assert "PRIVATE_CANDIDATE" not in str(caught.value)


def test_exception_details_and_output_are_not_exposed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    predicate_path = write_predicate(
        tmp_path / "raises.py",
        "import sys\n"
        "def fails(data):\n"
        "    print('CANARY_SECRET', flush=True)\n"
        "    print('CANARY_SECRET', file=sys.stderr, flush=True)\n"
        "    raise RuntimeError('CANARY_SECRET ' + data.decode())\n",
    )

    with pytest.raises(PredicateError) as caught:
        SubprocessPredicate(f"{predicate_path}:fails")(b"PRIVATE_CANDIDATE")

    captured = capsys.readouterr()
    assert "CANARY_SECRET" not in str(caught.value)
    assert "PRIVATE_CANDIDATE" not in str(caught.value)
    assert "CANARY_SECRET" not in captured.out
    assert "CANARY_SECRET" not in captured.err


def test_timeout_terminates_process_without_exposing_path_in_traceback(tmp_path: Path) -> None:
    predicate_path = write_predicate(
        tmp_path / "CANARY_PRIVATE_PATH" / "slow.py",
        "import time\ndef fails(data):\n    time.sleep(10)\n    return True\n",
    )

    with pytest.raises(PredicateError, match="timed out") as caught:
        SubprocessPredicate(f"{predicate_path}:fails", timeout=0.05)(b"candidate")

    rendered = "".join(traceback.format_exception(caught.value))
    assert str(predicate_path) not in rendered
    assert "CANARY_PRIVATE_PATH" not in rendered


@pytest.mark.parametrize("spec", ["predicate.py", ":fails", "predicate.py:"])
def test_malformed_predicate_spec_is_rejected(spec: str) -> None:
    with pytest.raises(InputError):
        SubprocessPredicate(spec)


@pytest.mark.parametrize("timeout", [0, -1, True, "5"])
def test_invalid_timeout_is_rejected(timeout: object) -> None:
    with pytest.raises(InputError):
        SubprocessPredicate(f"{Path(__file__)}:fails", timeout=timeout)  # type: ignore[arg-type]


def test_missing_predicate_file_is_an_input_error_without_exposing_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing.py"

    with pytest.raises(InputError) as caught:
        SubprocessPredicate(f"{missing}:fails")

    assert str(missing) not in str(caught.value)
