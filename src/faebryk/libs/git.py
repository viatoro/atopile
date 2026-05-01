# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_COMMIT_HASH_RE = re.compile(r"[0-9a-fA-F]{4,40}")


class GitNotAvailableError(Exception):
    """Raised when the git executable is not found on the system."""

    def __init__(self) -> None:
        super().__init__(
            "Git is not installed. Install git on your system to use this feature: "
            "https://git-scm.com/downloads"
        )


@contextlib.contextmanager
def _require_git():
    """Wrap git operations to produce a clear error when git is missing."""
    try:
        yield
    except ImportError as e:
        if "executable" in str(e):
            raise GitNotAvailableError() from e
        raise


@dataclass
class GitCommitInfo:
    long_hash: str
    short_hash: str
    date: str
    message: str
    author_name: str


def git_root(start: os.PathLike | None = None) -> Path:
    with _require_git():
        from git import Repo

        repo = Repo(
            start if start is not None else Path.cwd(), search_parent_directories=True
        )
        assert repo.working_tree_dir is not None
        return Path(repo.working_tree_dir)


def in_git_repo(path: Path) -> bool:
    """Check if a path is in a git repository."""
    with _require_git():
        import git

        try:
            git.Repo(path)
        except git.InvalidGitRepositoryError:
            return False
        return True


def test_for_git_executable() -> bool:
    try:
        import git  # noqa: F401
    except ImportError as e:
        # catch no git executable
        if "executable" not in e.msg:
            raise
        return False
    return True


def get_short_head_hash(cwd: Path) -> str:
    """Return the short hash of HEAD for the repo at *cwd*."""
    with _require_git():
        from git import Repo

        repo = Repo(cwd, search_parent_directories=True)
        return repo.head.commit.hexsha[:7]


def git_log_for_file(file_path: Path, max_count: int = 100) -> list[GitCommitInfo]:
    """Return commits that touched *file_path*, newest first."""
    with _require_git():
        from git import Repo

        repo = Repo(file_path.parent, search_parent_directories=True)
        commits: list[GitCommitInfo] = []
        for commit in repo.iter_commits(paths=str(file_path), max_count=max_count):
            commits.append(
                GitCommitInfo(
                    long_hash=commit.hexsha,
                    short_hash=commit.hexsha[:7],
                    date=commit.committed_datetime.isoformat(),
                    message=str(commit.summary),
                    author_name=commit.author.name or "",
                )
            )
        return commits


def git_show_file_at_commit(file_path: Path, commit_hash: str) -> Path:
    """Extract *file_path* at *commit_hash* into a temp file and return its path."""
    if not _COMMIT_HASH_RE.fullmatch(commit_hash):
        raise ValueError(f"Invalid commit hash: {commit_hash}")

    with _require_git():
        from git import Repo

        repo = Repo(file_path.parent, search_parent_directories=True)
        assert repo.working_tree_dir is not None
        repo_root = Path(repo.working_tree_dir)
        rel_path = file_path.resolve().relative_to(repo_root)

        commit = repo.commit(commit_hash)
        blob = commit.tree / str(rel_path)
        data = blob.data_stream.read()

        tmp = tempfile.NamedTemporaryFile(
            suffix=file_path.suffix,
            delete=False,
            prefix=f"pcbdiff_{commit_hash[:8]}_",
        )
        tmp.write(data)
        tmp.close()
        return Path(tmp.name)


async def async_git_log_for_file(
    file_path: Path, max_count: int = 100
) -> list[GitCommitInfo]:
    return await asyncio.to_thread(git_log_for_file, file_path, max_count)


async def async_git_show_file_at_commit(file_path: Path, commit_hash: str) -> Path:
    return await asyncio.to_thread(git_show_file_at_commit, file_path, commit_hash)


def clone_repo(
    repo_url: str,
    clone_target: Path,
    depth: int | None = None,
    ref: str | None = None,
) -> Path:
    """Clones a git repository and optionally checks out a specific ref.

    Args:
        repo_url: The URL of the repository to clone.
        clone_target: The directory path where the repository should be cloned.
        depth: If specified, creates a shallow clone with a history truncated
               to the specified number of commits.
        ref: The branch, tag, or commit hash to checkout after cloning.

    Returns:
        The path to the cloned repository (clone_target).

    Raises:
        git.GitCommandError: If any git command fails.
    """
    with _require_git():
        from git import GitCommandError, Repo

        if depth is not None and ref is not None:
            raise NotImplementedError("Cannot specify both depth and ref")

        depth_str = f" with depth {depth or 'full'}" if depth is not None else ""
        logger.debug(f"Cloning {repo_url} into {clone_target}{depth_str}...")
        try:
            repo = Repo.clone_from(repo_url, clone_target, depth=depth)
            logger.debug(f"Successfully cloned {repo_url}")
        except GitCommandError as e:
            logger.error(f"Failed to clone {repo_url}: {e}")
            raise

        if ref:
            logger.debug(f"Checking out ref {ref} in {clone_target}...")
            try:
                repo.git.checkout(ref)
                logger.debug(f"Successfully checked out ref {ref}")
            except GitCommandError as e:
                logger.error(f"Failed to checkout ref {ref}: {e}")
                raise

        return clone_target


def has_uncommitted_changes(files: Iterable[str | Path]) -> bool | None:
    """Check if any of the given files have uncommitted changes."""
    try:
        from git import Repo

        files = [Path(f).resolve() for f in files]
        if not files:
            return False

        repo = Repo(files[0], search_parent_directories=True)
        diff_index = repo.index.diff(None)  # Get uncommitted changes

        repo_root = Path(repo.working_dir)

        # Check if any of the files have changes
        for diff in diff_index:
            touched_file = diff.a_path or diff.b_path
            # m, c or d
            assert touched_file is not None
            touched_path = repo_root / touched_file
            if touched_path in files:
                return True

        return False
    # TODO bad
    except Exception:
        # If we can't check git status (not a git repo, etc), assume we don't
        # have changes
        return None
