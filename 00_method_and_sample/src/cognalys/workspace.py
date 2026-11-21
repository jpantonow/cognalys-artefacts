"""Where a run reads its data and writes its outputs.

The package used to find data/ by walking up from its own file, which is
right in a checkout and wrong once it is installed: `__file__` is then in
site-packages. The workspace is the directory that holds data/
(specifications, policies, detector rules, learning) and outputs/, and it is
resolved here, once:

1. the COGNALYS_WORKSPACE environment variable, when set;
2. the checkout the package runs from, when it is one (src/cognalys inside a
   tree that has data/ and pyproject.toml), which keeps every existing
   command and test pointing where it always did;
3. otherwise the current directory.
"""
from __future__ import annotations

import os
from pathlib import Path

ENVIRONMENT_VARIABLE = "COGNALYS_WORKSPACE"


def library_checkout() -> Path | None:
    """The checkout this package runs from, or None when it is installed."""
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / "data").is_dir() and (checkout / "pyproject.toml").is_file():
        return checkout
    return None


def workspace_root() -> Path:
    explicit = os.environ.get(ENVIRONMENT_VARIABLE, "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return library_checkout() or Path.cwd().resolve()


def data_path(*parts: str) -> Path:
    """A path under the workspace's data/ directory."""
    return workspace_root().joinpath("data", *parts)


def library_data(*parts: str) -> Path:
    """Data the library itself provides: specifications, detector rules, policies.

    The workspace's own copy when it has one, so a workspace can extend or
    replace them; otherwise the library checkout's. A research workspace
    kept apart from the library (COGNALYS_WORKSPACE=legado) holds its runs
    and inputs, not a copy of every specification.
    """
    own = data_path(*parts)
    checkout = library_checkout()
    if own.exists() or checkout is None:
        return own
    shipped = checkout.joinpath("data", *parts)
    return shipped if shipped.exists() else own


def credential_directories() -> list[Path]:
    """Where .env.* credential files are read: the workspace, then the library.

    A variable already set wins (cli.load_env_file), so the workspace's
    file takes precedence and the library's only fills what it lacks.
    """
    directories = [workspace_root()]
    checkout = library_checkout()
    if checkout is not None and checkout not in directories:
        directories.append(checkout)
    return directories
