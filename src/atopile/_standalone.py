"""Stdlib-only utilities with no atopile/third-party imports.

Safe to import from CI scripts and other contexts where the full
atopile package (and its native extensions) are not installed.
"""


def pep440_to_semver(version_str: str) -> str:
    """Convert a PEP 440 version string to semver format.

    PEP 440 uses "." between base version and pre-release segments,
    but semver requires "-":
      "0.14.0.post1.dev35+g1bd1cf8c4.d20260210230746"
    → "0.14.0-post1.dev35+g1bd1cf8c4.d20260210230746"
    """
    if version_str.startswith("v"):
        version_str = version_str[1:]
    dot_split = version_str.split(".")
    if len(dot_split) < 3:
        dot_split += ["0"] * (3 - len(dot_split))
    base = ".".join(dot_split[:3])
    rest = ".".join(dot_split[3:])
    return f"{base}-{rest}" if rest else base


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <pep440-version>", file=sys.stderr)
        sys.exit(1)
    print(pep440_to_semver(sys.argv[1]))
