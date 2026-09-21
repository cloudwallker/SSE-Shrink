"""Check distributions and exercise the installed wheel in an isolated environment."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

_ROOT = Path(__file__).resolve().parents[1]
_FORBIDDEN_PARTS = {
    ".agents",
    ".codex",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".superpowers",
    ".tooling",
    ".venv",
    "__pycache__",
    "private",
    "venv",
}
_OFFLINE_GUARD = """import sys

def _block_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto", "socket.bind"}:
        raise RuntimeError("network access is disabled during wheel smoke tests")

sys.addaudithook(_block_network)
sys._sse_shrink_smoke_offline = True
"""


def _check_archive_paths(names: list[str]) -> None:
    for name in names:
        path = PurePosixPath(name)
        assert not path.is_absolute() and ".." not in path.parts, name
        assert not _FORBIDDEN_PARTS.intersection(part.lower() for part in path.parts), name
        assert not any(part.lower().startswith(".env") for part in path.parts), name
        assert path.suffix not in {".pyc", ".pyo"}, name
        assert not any(part.startswith(".pytest-tmp") for part in path.parts), name
        assert "/docs/research/" not in f"/{name}", name
        assert "/docs/superpowers/" not in f"/{name}", name
        assert path.name not in {"resume.zh-CN.md", "validation.md", "release.md"}, name


def check_distributions(dist_dir: Path, version: str) -> Path:
    """Check the exact release pair without extracting potentially unexpected members."""
    wheel = dist_dir / f"sse_shrink-{version}-py3-none-any.whl"
    sdist = dist_dir / f"sse_shrink-{version}.tar.gz"
    readme = (_ROOT / "README.md").read_text(encoding="utf-8").strip()
    runtime_names = sorted(
        {path.name for path in (_ROOT / "src/sse_shrink").glob("*.py")} | {"_worker.py"}
    )
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        _check_archive_paths(names)
        for name in runtime_names:
            assert f"sse_shrink/{name}" in names, f"wheel is missing {name}"
        metadata_root = f"sse_shrink-{version}.dist-info"
        assert f"{metadata_root}/licenses/LICENSE" in names, "wheel is missing its license"
        metadata = BytesParser().parsebytes(archive.read(f"{metadata_root}/METADATA"))
        assert metadata["Name"] == "sse-shrink"
        assert metadata["Version"] == version
        assert metadata["Description-Content-Type"] == "text/markdown"
        description = metadata.get_payload(decode=True).decode("utf-8").strip()
        assert description == readme, "wheel metadata must embed the README"
    with tarfile.open(sdist, "r:gz") as archive:
        names = archive.getnames()
        _check_archive_paths(names)
        prefix = f"sse_shrink-{version}"
        for name in runtime_names:
            assert f"{prefix}/src/sse_shrink/{name}" in names, f"sdist is missing {name}"
        for name in ("LICENSE", "README.md", "README.zh-CN.md", "scripts/smoke_wheel.py"):
            assert f"{prefix}/{name}" in names, f"sdist is missing {name}"
    return wheel


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=True,
    )
    return completed.stdout.strip()


def smoke_wheel(wheel: Path, version: str) -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"PYTHONPATH", "PYTHONHOME", "PYTHONOPTIMIZE", "SSE_SHRINK_PREDICATE"}
    }
    env.update(PYTHONNOUSERSITE="1", PYTHONUTF8="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    with tempfile.TemporaryDirectory(prefix="sse-shrink-wheel-") as temporary_dir:
        temporary = Path(temporary_dir).resolve()
        assert not temporary.is_relative_to(_ROOT), "smoke directory must be outside the checkout"
        environment = temporary / "venv"
        working = temporary / "work"
        working.mkdir()
        _run([sys.executable, "-m", "venv", str(environment)], cwd=working, env=env)
        executable_dir = environment / ("Scripts" if os.name == "nt" else "bin")
        python = str(executable_dir / ("python.exe" if os.name == "nt" else "python"))
        console = str(executable_dir / ("sse-shrink.exe" if os.name == "nt" else "sse-shrink"))
        _run(
            [python, "-m", "pip", "install", str(wheel), "pytest>=8,<10"],
            cwd=working,
            env=env,
        )
        site_packages = Path(
            _run(
                [python, "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
                cwd=working,
                env=env,
            )
        ).resolve()
        assert site_packages.is_relative_to(environment), site_packages
        (site_packages / "sitecustomize.py").write_text(_OFFLINE_GUARD, encoding="utf-8")
        env["PIP_NO_INDEX"] = "1"
        probe = """import importlib.metadata, json, pathlib, socket, sys
import sse_shrink
assert getattr(sys, "_sse_shrink_smoke_offline", False), "offline guard is missing"
try:
    socket.getaddrinfo("network.disabled.invalid", 443)
except RuntimeError:
    pass
else:
    raise AssertionError("offline guard did not block network resolution")
assert sse_shrink.__version__ == importlib.metadata.version("sse-shrink")
print(json.dumps({"path": str(pathlib.Path(sse_shrink.__file__).resolve()),
                  "version": sse_shrink.__version__}))
"""
        installed = json.loads(_run([python, "-c", probe], cwd=working, env=env))
        assert Path(installed["path"]).is_relative_to(site_packages), installed
        assert installed["version"] == version, installed
        for command in ([python, "-m", "sse_shrink", "--version"], [console, "--version"]):
            assert _run(command, cwd=working, env=env) == f"sse-shrink {version}"
        report = json.loads(
            _run(
                [python, "-m", "sse_shrink", "demo", "--output", "demo-output", "--json"],
                cwd=working,
                env=env,
            )
        )
        assert report["schema_version"] == 1, report
        assert report["status"] == "complete", report
        assert (report["original_events"], report["reduced_events"]) == (42, 2), report
        assert report["baseline_verified"] and report["minimality_verified"], report
        saved_report = (working / "demo-output/report.json").read_text(encoding="utf-8")
        assert report == json.loads(saved_report), report
        _run(
            [python, "-m", "pytest", "demo-output/test_repro.py", "-q"],
            cwd=working,
            env=env,
        )
    print(f"wheel smoke passed: {version}; installed imports; offline demo 42 -> 2; pytest")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=_ROOT / "dist")
    parser.add_argument("--artifacts-only", action="store_true")
    args = parser.parse_args()
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    wheel = check_distributions(args.dist_dir.resolve(), project["version"])
    if args.artifacts_only:
        print(f"distribution checks passed: {project['version']}")
    else:
        smoke_wheel(wheel, project["version"])


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        print(error.stdout, file=sys.stderr)
        print(error.stderr, file=sys.stderr)
        raise SystemExit(error.returncode) from None
