# Changelog

All notable changes to this project are documented here, following
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-09-21

### Changed

- Scan the leading CR/LF prefix as one range while preserving the original bytes,
  CRLF line counting, frame locations and incomplete-tail behavior. See the
  [component benchmarks](docs/performance.md) for measurements and their limits.
- Keep the Python API, reduction rules and report `schema_version=1` compatible.

### Fixed

- Read candidate bytes before importing a predicate, preventing a hanging import
  from blocking large Windows stdin writes ahead of timeout handling.
- Reap the direct predicate worker and close its pipes after interruption or
  communication failure while preserving the original exception. Library calls
  retain `KeyboardInterrupt`; the CLI reports interruption without a traceback,
  returns 130 and supports an `interrupted` JSON error category. Cleanup does not
  manage arbitrary predicate descendants or bound operating-system startup time.

### Added

- An isolated wheel smoke check in all four Ubuntu/Windows and Python 3.11/3.14
  CI combinations. It checks distribution contents, installed imports, version
  entry points, the offline 42-to-2-event demo and the exported pytest test from
  outside the source checkout.

This version is distributed through GitHub Releases; no PyPI publication is claimed.

## [0.1.0] - 2026-09-12

First public source publication. This version had no Git tag, GitHub Release or
PyPI publication.

### Added

- Lossless LF, CRLF, and CR framing for bounded SSE files.
- Predicate-driven event reduction with stability checks, call budgets, and
  event-level 1-minimal verification.
- Fresh subprocess execution for Python predicates with timeout handling.
- Synchronous in-memory HTTPX replay transport.
- Offline synthetic demo and exported pytest reproduction bundles.
- English and Simplified Chinese documentation.

### Changed

- Scan SSE line endings with a compiled streaming matcher instead of a Python
  loop over every byte; retain exact CR, LF, CRLF and incomplete-tail behavior.
- Assemble nonempty prefixes and selected event bodies without an intermediate
  full-body copy. Preserve the empty-prefix fast path.
- Add reproducible component benchmarks and document dense-line performance tradeoffs.

[0.2.0]: https://github.com/cloudwallker/SSE-Shrink/releases/tag/v0.2.0
[0.1.0]: https://github.com/cloudwallker/SSE-Shrink/tree/bb6b031
