# Changelog

All notable changes to this project will be documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
intends to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Lossless LF, CRLF, and CR framing for bounded SSE files.
- Predicate-driven event reduction with stability checks, call budgets, and
  event-level 1-minimal verification.
- Fresh, time-bounded subprocess execution for Python predicates.
- Synchronous in-memory HTTPX replay transport.
- Offline synthetic demo and exported pytest reproduction bundles.
- English and Simplified Chinese documentation.

No public package or repository release is claimed by this changelog entry.

### Changed

- Scan SSE line endings with a compiled streaming matcher instead of a Python
  loop over every byte; retain exact CR, LF, CRLF and incomplete-tail behavior.
- Assemble nonempty prefixes and selected event bodies without an intermediate
  full-body copy. Preserve the empty-prefix fast path.
- Add reproducible component benchmarks and document dense-line performance tradeoffs.
