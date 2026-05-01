# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

import logging
import os
import re
import shutil
from collections.abc import Generator
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from httpx import HTTPStatusError, RequestError, TimeoutException

import faebryk.core.faebrykpy as fbrk
import faebryk.core.graph as graph
import faebryk.core.node as fabll
import faebryk.library._F as F
from faebryk.libs.http import http_client
from faebryk.libs.util import Advancable

logger = logging.getLogger(__name__)


@dataclass
class _DownloadState:
    """Internal state for background datasheet downloads."""

    cache_dir: Path
    futures: dict[str, Future]
    executor: ThreadPoolExecutor


_pending_downloads: _DownloadState | None = None


# Maximum characters for the joined module names in the filename
# (excluding .pdf extension)
MAX_FILE_NAME_CHARACTERS = 100


class DatasheetDownloadException(Exception):
    pass


def _extract_filename_from_url(url: str) -> str:
    """Extract filename from LCSC datasheet URL"""
    url_filename = Path(url).name
    # If URL doesn't end in .pdf or has no valid filename, create one from URL
    if not url_filename or not url_filename.endswith(".pdf"):
        # Fallback: use a hash of the URL
        import hashlib

        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        url_filename = f"datasheet_{url_hash}.pdf"

    # Clean up LCSC filenames: remove "lcsc_datasheet_NNNNNNNNNN_" prefix
    if url_filename.startswith("lcsc_datasheet_"):
        parts = url_filename.split("_", 3)  # Split into max 4 parts
        if len(parts) >= 4:
            # ["lcsc", "datasheet", "date", "MFR-PART_CXXXXX.pdf"]
            url_filename = parts[3]

    return url_filename


def export_datasheets(
    app: fabll.Node,
    path: Path = Path("build/documentation/datasheets"),
    overwrite: bool = False,
    progress: Advancable | None = None,
):
    """
    Export all datasheets of all modules (that have a datasheet defined)
    of the given application.

    Downloads each unique datasheet URL once, naming the file with all
    module names that share that URL joined by underscores.
    """
    # Create directories if they don't exist
    path.mkdir(parents=True, exist_ok=True)

    # Collect unique datasheet URLs
    unique_urls: set[str] = set()
    logger.info(f"Exporting datasheets to: {path}")

    for m in fabll.Traits.get_implementor_objects(
        F.has_datasheet.bind_typegraph(tg=app.tg)
    ):
        datasheet_trait = m.try_get_trait(F.has_datasheet)
        if datasheet_trait is None:
            logger.warning(f"Missing datasheet trait for {m.get_name()}")
            continue
        url = datasheet_trait.get_datasheet()
        if not url:
            logger.warning(f"Missing datasheet URL for {m.get_name()}")
            continue
        unique_urls.add(url)

    # Build download tasks, deduplicating by output file path to avoid
    # concurrent writes when different URLs produce the same filename.
    seen_paths: dict[Path, str] = {}
    for url in unique_urls:
        filename = _extract_filename_from_url(url)
        file_path = path / filename
        if file_path.exists() and not overwrite:
            logger.debug(f"Datasheet {filename} already exists, skipping download")
            continue
        if file_path in seen_paths:
            logger.debug(
                f"Filename collision: {url} and {seen_paths[file_path]} "
                f"both map to {filename}, skipping duplicate"
            )
            continue
        seen_paths[file_path] = url

    tasks = list(seen_paths.items())  # [(file_path, url), ...]

    if progress:
        progress.set_total(len(tasks))

    # Download datasheets in parallel
    MAX_WORKERS = 8
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_download_datasheet, url, fp): (url, fp) for fp, url in tasks
        }
        for future in as_completed(futures):
            url, fp = futures[future]
            if progress:
                progress.advance()
            try:
                future.result()
                logger.debug(f"Downloaded datasheet {fp.name}")
            except DatasheetDownloadException as e:
                logger.error(f"Failed to download datasheet {fp.name}: {e}")


def _download_datasheet(url: str, path: Path):
    """
    Download the datasheet of the given module and save it to the given path.
    """
    TIMEOUT_S = 15  # datasheet download timeout
    if not url.endswith(".pdf"):
        raise DatasheetDownloadException(f"Datasheet URL {url} is probably not a PDF")
    if not url.startswith(("http://", "https://")):
        raise DatasheetDownloadException(
            f"Datasheet URL {url} is probably not a valid URL"
        )

    try:
        user_agent_headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36"
        }
        with http_client(headers=user_agent_headers) as client:
            response = client.get(url, timeout=TIMEOUT_S, follow_redirects=False)

            # Handle redirects explicitly (httpx doesn't treat 3xx as errors).
            if response.status_code == 301:
                # Some LCSC datasheets are moved; map to the stable wmsc URL.
                if "lcsc.com" in url:
                    lcsc_id_regex = r"_(C\d{4,8})"
                    match = re.search(lcsc_id_regex, url)
                    if match:
                        lcsc_id = match.group(1)
                        redirected_url = f"https://wmsc.lcsc.com/wmsc/upload/file/pdf/v2/{lcsc_id}.pdf"
                        logger.info(f"LCSC 301 redirect: {url} -> {redirected_url}")
                        _download_datasheet(redirected_url, path)
                        return  # Exit after successful recursive download

                # Otherwise, follow the Location header if present.
                location = response.headers.get("location")
                if location:
                    _download_datasheet(location, path)
                    return

            response.raise_for_status()
    except HTTPStatusError as e:
        raise DatasheetDownloadException(
            f"HTTP error downloading datasheet from {url}: {e}"
        ) from e
    except TimeoutException as e:
        raise DatasheetDownloadException(
            f"Timed out (>{TIMEOUT_S}s) downloading datasheet from {url}: {e}"
        ) from e
    except RequestError as e:
        raise DatasheetDownloadException(
            f"Failed to download datasheet from {url}: {e}"
        ) from e

    # check if content is pdf
    if not response.content.startswith(b"%PDF"):
        raise DatasheetDownloadException(
            f"Downloaded content is not a PDF: {response.content[:100]}"
        )

    try:
        path.write_bytes(response.content)
    except Exception as e:
        raise DatasheetDownloadException(
            f"Failed to save datasheet to {path}: {e}"
        ) from e


def _iter_part_datasheets() -> Generator[tuple[Path, str], None, None]:
    """Iterate part directories and yield (part_dir, datasheet_url) pairs.

    Uses AtoCodeParse to extract has_datasheet trait from .ato files,
    following the same directory iteration pattern as PartLifecycle.Library.
    """
    from atopile.config import config
    from faebryk.libs.codegen.atocodeparse import AtoCodeParse

    parts_dir = config.project.paths.parts
    if not parts_dir.is_dir():
        return
    for part_dir in sorted(parts_dir.iterdir(), key=lambda x: x.name):
        if not part_dir.is_dir():
            continue
        ato_path = part_dir / (part_dir.name + ".ato")
        if not ato_path.is_file():
            continue
        try:
            ato = AtoCodeParse.ComponentFile(ato_path)
            _, args = ato.parse_trait("has_datasheet")
            url = args.get("datasheet")
            if url:
                yield part_dir, url
        except AtoCodeParse.TraitNotFound:
            continue


def start_datasheet_downloads(app: fabll.Node) -> None:
    """Kick off background datasheet downloads into build/cache.

    Stores state internally; call finalize_datasheet_downloads() later
    to wait for completion and copy files into part directories.
    """
    global _pending_downloads

    from atopile.config import config

    if os.environ.get("CI"):
        logger.info("Skipping datasheet downloads in CI")
        return

    # Collect unique datasheet URLs from the live graph
    urls: set[str] = set()
    for m in fabll.Traits.get_implementor_objects(
        F.has_datasheet.bind_typegraph(tg=app.tg)
    ):
        datasheet_trait = m.try_get_trait(F.has_datasheet)
        if datasheet_trait is None:
            continue
        url = datasheet_trait.get_datasheet()
        if url:
            urls.add(url)

    if not urls:
        logger.info("No datasheets to download")
        return

    cache_dir = config.project.paths.build / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Seed cache from PDFs already present in part directories
    for part_dir, url in _iter_part_datasheets():
        filename = _extract_filename_from_url(url)
        src = part_dir / filename
        dest = cache_dir / filename
        if src.exists() and not dest.exists():
            shutil.copy2(src, dest)

    executor = ThreadPoolExecutor(max_workers=8)
    futures: dict[str, Future] = {}
    for url in urls:
        filename = _extract_filename_from_url(url)
        cache_path = cache_dir / filename
        if cache_path.exists():
            continue
        futures[url] = executor.submit(_download_datasheet, url, cache_path)

    if futures:
        logger.info(
            f"Starting background download of {len(futures)} datasheets "
            f"({len(urls) - len(futures)} already cached)"
        )
    else:
        logger.info(f"All {len(urls)} datasheets already cached")

    _pending_downloads = _DownloadState(
        cache_dir=cache_dir,
        futures=futures,
        executor=executor,
    )


def finalize_datasheet_downloads() -> None:
    """Wait for background datasheet downloads and copy into part directories."""
    global _pending_downloads

    state = _pending_downloads
    _pending_downloads = None

    if state is None:
        return

    try:
        # Wait for in-flight downloads
        downloaded = 0
        failed = 0
        for url, future in state.futures.items():
            try:
                future.result()
                downloaded += 1
            except DatasheetDownloadException as e:
                logger.error(f"Failed to download datasheet from {url}: {e}")
                failed += 1
            except Exception as e:
                logger.error(f"Unexpected error downloading {url}: {e}")
                failed += 1

        # Copy cached datasheets into part directories
        copied = 0
        for part_dir, url in _iter_part_datasheets():
            filename = _extract_filename_from_url(url)
            cache_path = state.cache_dir / filename
            if not cache_path.exists():
                continue
            dest = part_dir / filename
            if dest.exists():
                continue
            shutil.copy2(cache_path, dest)
            logger.info(f"Copied datasheet to {dest}")
            copied += 1

        logger.info(
            f"Datasheets: {downloaded} downloaded, {copied} copied to parts, "
            f"{failed} failed"
        )
    finally:
        state.executor.shutdown(wait=False)


def _create_app_with_datasheet(url: str):
    g = graph.GraphView.create()
    tg = fbrk.TypeGraph.create(g=graph.GraphView.create())

    class _ModuleWithDatasheet(fabll.Node):
        _is_module = fabll.Traits.MakeEdge(fabll.is_module.MakeChild())
        datasheet = fabll.Traits.MakeEdge(F.has_datasheet.MakeChild(datasheet=url))

    class _App(fabll.Node):
        modules_with_datasheet = [_ModuleWithDatasheet.MakeChild() for _ in range(2)]

    return _App.bind_typegraph(tg=tg).create_instance(g=g)


def test_download_datasheet(caplog, tmp_path):
    URL = "https://www.ti.com/lit/ds/symlink/lm555.pdf"
    DEFAULT_PATH = tmp_path / "datasheets"

    app = _create_app_with_datasheet(URL)

    datasheet_a = app.modules_with_datasheet[0].get().try_get_trait(F.has_datasheet)
    datasheet_b = app.modules_with_datasheet[1].get().try_get_trait(F.has_datasheet)
    assert datasheet_a is not None
    assert datasheet_b is not None

    assert datasheet_a.get_datasheet() == URL
    assert datasheet_b.get_datasheet() == URL

    export_datasheets(app, path=DEFAULT_PATH)

    # check that exactly one datasheet file was downloaded
    # (both modules share the same URL, so deduplication should result in one file)
    # filename should be the original filename from the URL
    pdf_files = list(DEFAULT_PATH.glob("*.pdf"))
    assert len(pdf_files) == 1, f"Expected 1 PDF, got: {pdf_files}"
    expected_name = "lm555.pdf"  # filename from URL
    assert (DEFAULT_PATH / expected_name).exists(), (
        f"Expected {expected_name}, got: {[f.name for f in pdf_files]}"
    )


def test_download_datasheet_failure(caplog, tmp_path):
    URL = "fake_url.pdf"
    DEFAULT_PATH = tmp_path / "datasheets"

    app = _create_app_with_datasheet(URL)

    with caplog.at_level(logging.ERROR):
        export_datasheets(app, path=DEFAULT_PATH)

    # check that no datasheet files were downloaded due to the invalid URL
    pdf_files = list(DEFAULT_PATH.glob("*.pdf"))
    assert len(pdf_files) == 0, f"Expected no PDFs, got: {pdf_files}"
    # verify that the failure was logged with DatasheetDownloadException
    # "fake_url.pdf" fails the URL protocol check
    assert any(
        "is probably not a valid URL" in record.message for record in caplog.records
    ), (
        f"Expected DatasheetDownloadException to be logged, "
        f"got: {[r.message for r in caplog.records]}"
    )
