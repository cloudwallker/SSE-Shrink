"""Public error categories; messages must not echo stream contents."""


class InputError(ValueError):
    """Invalid input, options, or file paths (CLI exit 2)."""


class NotReproducedError(ValueError):
    """The input does not isolate the target failure (CLI exit 3)."""


class PredicateError(RuntimeError):
    """The predicate errored, timed out, or returned inconsistent results (exit 4)."""
