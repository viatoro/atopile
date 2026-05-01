import configparser
import contextlib
import dataclasses
import hashlib
import importlib.metadata
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from faebryk.libs.util import cast_assert, once

if TYPE_CHECKING:
    import git

log = logging.getLogger(__name__)


def _normalize_git_remote_url(git_url: str) -> str:
    """
    Commonize the remote which could be in either of these forms:
        - https://github.com/atopile/atopile.git
        - git@github.com:atopile/atopile.git
    ... to "github.com/atopile/atopile"
    """

    if git_url.startswith("git@"):
        git_url = git_url.removeprefix("git@")
        git_url = "/".join(git_url.split(":", 1))
    else:
        git_url = git_url.removeprefix("https://")

    git_url = git_url.removesuffix(".git")

    return git_url


class PropertyLoaders:
    @once
    @staticmethod
    def _repo() -> "git.Repo":
        try:
            import git
        except ImportError:
            return None

        with contextlib.suppress(
            git.InvalidGitRepositoryError,
            git.NoSuchPathError,
            configparser.Error,
            ValueError,
            AttributeError,
            configparser.Error,
        ):
            return git.Repo(search_parent_directories=True)

    @once
    @staticmethod
    def _repo_config_reader() -> "git.ConfigParser | None":
        with contextlib.suppress(
            configparser.Error,
            ValueError,
            AttributeError,
        ):
            return PropertyLoaders._repo().config_reader()
        return None

    @once
    @staticmethod
    def _repo_get_value[T: int | float | str | bool](
        t: type[T], section: str, option: str, default: T | None = None
    ) -> str | None:
        with contextlib.suppress(
            configparser.Error,
            ValueError,
            AttributeError,
            TypeError,
        ):
            return cast_assert(
                t,
                PropertyLoaders._repo_config_reader().get_value(
                    section, option, default
                ),
            )
        return default

    @once
    @staticmethod
    def email() -> str | None:
        """Get the git user email."""
        return PropertyLoaders._repo_get_value(str, "user", "email", None)

    @once
    @staticmethod
    def current_git_hash() -> str | None:
        """Get the current git commit hash."""
        repo = PropertyLoaders._repo()
        if repo is None:
            return None
        import git

        with contextlib.suppress(
            git.InvalidGitRepositoryError,
            git.NoSuchPathError,
            configparser.Error,
            ValueError,
            AttributeError,
        ):
            return repo.head.commit.hexsha

        return None

    @once
    @staticmethod
    def project() -> str | None:
        """Get the project from the git URL."""
        repo = PropertyLoaders._repo()
        if repo is None:
            return None

        import git

        with contextlib.suppress(
            git.InvalidGitRepositoryError,
            git.NoSuchPathError,
            configparser.Error,
            ValueError,
            AttributeError,
        ):
            if not repo.remotes:
                return None

            if (git_url := repo.remotes.origin.url) is None:
                return None

            return _normalize_git_remote_url(git_url)

        return None

    @once
    @staticmethod
    def project_id() -> str | None:
        """Anonymous project ID for telemetry."""

        project = PropertyLoaders.project()
        if project is None:
            return None
        # Hash the project ID to de-identify it
        return hashlib.sha256(project.encode()).hexdigest()

    @once
    @staticmethod
    def ci_provider() -> str | None:
        if os.getenv("GITHUB_ACTIONS"):
            return "GitHub Actions"
        elif os.getenv("TF_BUILD"):
            return "Azure Pipelines"
        elif os.getenv("CIRCLECI"):
            return "Circle CI"
        elif os.getenv("TRAVIS"):
            return "Travis CI"
        elif os.getenv("BUILDKITE"):
            return "Buildkite"
        elif os.getenv("CIRRUS_CI"):
            return "Cirrus CI"
        elif os.getenv("GITLAB_CI"):
            return "GitLab CI"
        elif os.getenv("TEAMCITY_VERSION"):
            return "TeamCity"
        elif os.getenv("CODEBUILD_BUILD_ID"):
            return "CodeBuild"
        elif os.getenv("HEROKU_TEST_RUN_ID"):
            return "Heroku CI"
        elif os.getenv("bamboo.buildKey"):
            return "Bamboo"
        elif os.getenv("BUILD_ID"):
            return "Jenkins"  # could also be Hudson
        elif os.getenv("CI"):
            return "Other"

        return None

    @once
    @staticmethod
    def platform() -> str:
        import sys

        return sys.platform

    @once
    @staticmethod
    def via_docker() -> bool:
        return bool(os.getenv("ATO_VIA_DOCKER"))

    @once
    @staticmethod
    def via_vsce() -> bool:
        return bool(os.getenv("ATO_VSCE_PID"))

    @once
    @staticmethod
    def github_action_repository() -> str | None:
        return os.getenv("GITHUB_ACTION_REPOSITORY")

    @once
    @staticmethod
    def github_repository_owner() -> str | None:
        return os.getenv("GITHUB_REPOSITORY_OWNER")

    @once
    @staticmethod
    def github_repository() -> str | None:
        return os.getenv("GITHUB_REPOSITORY")

    @once
    @staticmethod
    def install_method() -> str:
        """Detect how atopile was installed from the dist-info path."""
        import importlib.metadata
        from pathlib import PurePath

        if os.getenv("ATO_PLAYGROUND"):
            return "playground"
        if os.getenv("ATO_VIA_DOCKER"):
            return "docker"
        try:
            dist = importlib.metadata.distribution("atopile")
        except importlib.metadata.PackageNotFoundError:
            return "unknown"
        direct_url = dist.read_text("direct_url.json")
        if direct_url and '"editable":true' in direct_url.replace(" ", ""):
            return "dev"
        path = getattr(dist, "_path", None)
        if path is None:
            return "unknown"
        parts = PurePath(str(path)).parts
        for i, part in enumerate(parts[:-1]):
            if part == "atopile.atopile" and parts[i + 1] == "uv":
                return "vsce-managed"
        if "uv" in parts and "tools" in parts:
            return "uv-tool"
        if "homebrew" in parts or "Cellar" in parts:
            return "brew"
        if "pipx" in parts:
            return "pipx"
        return "pip"

    @once
    @staticmethod
    def run_id() -> str:
        return str(uuid.uuid4())


@dataclass
class ThinProperties:
    """Cheap properties captured on the calling thread."""

    run_id: str = field(default_factory=PropertyLoaders.run_id)
    start_time: float = field(default_factory=time.perf_counter)
    extra: dict[str, Any] | None = None


@dataclass
class TelemetryProperties:
    duration: float | None = None
    run_id: str | None = None

    # property loaders
    email: str | None = field(default_factory=PropertyLoaders.email)
    current_git_hash: str | None = field(
        default_factory=PropertyLoaders.current_git_hash
    )
    project: str | None = field(default_factory=PropertyLoaders.project)
    project_id: str | None = field(default_factory=PropertyLoaders.project_id)
    ci_provider: str | None = field(default_factory=PropertyLoaders.ci_provider)
    platform: str = field(default_factory=PropertyLoaders.platform)
    via_docker: bool = field(default_factory=PropertyLoaders.via_docker)
    via_vsce: bool = field(default_factory=PropertyLoaders.via_vsce)
    install_method: str = field(default_factory=PropertyLoaders.install_method)
    github_action_repository: str | None = field(
        default_factory=PropertyLoaders.github_action_repository
    )
    github_repository_owner: str | None = field(
        default_factory=PropertyLoaders.github_repository_owner
    )
    github_repository: str | None = field(
        default_factory=PropertyLoaders.github_repository
    )
    atopile_version: str = field(
        default_factory=lambda: importlib.metadata.version("atopile")
    )
    _extra: dict[str, Any] | None = field(default_factory=dict)

    def __init__(self, thin: ThinProperties | None = None, **kwargs: Any) -> None:  # type: ignore[override]
        self.__dict__.update(
            {
                f.name: f.default_factory()
                if f.default_factory is not dataclasses.MISSING
                else f.default
                for f in dataclasses.fields(self)
            }
        )
        self._extra.update(kwargs)

        if thin:
            self.run_id = thin.run_id
            self.duration = time.perf_counter() - thin.start_time
            self._extra.update(thin.extra or {})

    def dump(self) -> dict:
        return {
            f.name: getattr(self, f.name)
            for f in dataclasses.fields(self)
            if not f.name.startswith("_")
        } | (self._extra or {})
