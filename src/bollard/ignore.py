"""
Handles .dockerignore parsing and matching rule generation.
"""

import os
from typing import List


class DockerIgnore:
    """
    Parses a .dockerignore file and matches paths against it.
    Parses a .dockerignore file and matches paths against it.
    Follows Docker's Go implementation rules as closely as possible using
    Python's fnmatch.
    """

    def __init__(self, root_path: str = ".") -> None:
        self.root_path = root_path
        self.patterns: List[str] = []
        self._load_ignore_file()

    def _load_ignore_file(self) -> None:
        ignore_path = os.path.join(self.root_path, ".dockerignore")
        if not os.path.exists(ignore_path):
            return

        with open(ignore_path, "r", encoding="utf-8") as file_obj:
            for line in file_obj:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                self.patterns.append(line)

    def is_ignored(self, path: str) -> bool:
        """
        Check if a path should be ignored based on the loaded patterns.
        path should be relative to root_path.
        """
        # Clean path to be relative and use forward slashes for consistency
        rel_path = os.path.normpath(path).replace(os.path.sep, "/")

        # In Docker, "!" negates a previous match.
        # We iterate patterns in order.
        ignored = False

        import fnmatch

        for pattern in self.patterns:
            # Handle negation
            is_negative = pattern.startswith("!")
            clean_pattern = pattern[1:] if is_negative else pattern
            clean_pattern = clean_pattern.replace(os.path.sep, "/")

            # Simple fnmatch doesn't handle ** perfectly like Go's filepath.Match,
            # but it is a reasonable approximation for a zero-dependency client.
            # Ideally we would use 'glob' or 'pathspec' but we want zero dependencies.

            # Docker ignore rules are slightly different from gitignore,
            # but fnmatch is the closest standard lib equivalent.

            # Check if it matches
            if fnmatch.fnmatch(rel_path, clean_pattern):
                ignored = not is_negative

            # Check if it matches a directory (implicit /**) for patterns allowing it
            # e.g. pattern "node_modules" should match "node_modules/foo.js"
            if not is_negative and clean_pattern.endswith("/") is False:
                if fnmatch.fnmatch(rel_path, clean_pattern + "/*"):
                    ignored = not is_negative

        return ignored
