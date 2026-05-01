"""
Minimal Altium 365 API — log in, enumerate components, download
item-revision zips, and list managed-project git repositories.

Four Typer subcommands, built against the reverse-engineered API documented
in `models/server/dumps/README.md`:

    login      — full OIDC PKCE (browser) or email+password (headless)
                 bootstrap, mints the short AFSSessionID and writes it to
                 `.auth-token.json` for the other commands to pick up
                 (see dumps/README.md § 14.1 and § 14.5)
    list       — hit `searchasync` on the regional host and print components
    download   — resolve an HRID, walk link table for CMP-*, call the S3 URL
                 broker, and fetch the zip(s) from S3
    repos      — call `FindProjects` on the regional projects service and
                 print each project as a clone-ready git URL on the GITREST
                 host (see dumps/README.md § 16)

All commands except `login` read the session, workspace host, regional host
and git host from `.auth-token.json` (falling back to env vars, then a
hard-coded default). Run `login` first; the other commands take no auth
flags.
"""

from __future__ import annotations

import base64 as _b64
import datetime as _dt
import hashlib
import json as _json
import logging
import os
import re
import secrets
import time
import uuid
import webbrowser
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from xml.sax.saxutils import escape as _xml_sax_escape

import git as _git
import httpx

from faebryk.libs.http import http_client

# README § 2 Paging: the Designer client sends int32-max when dumping a
# whole category in one shot.
SEARCH_LIMIT_ALL = 2147483647

# README § 11 / § 14: tempuri.org is still the body namespace even though the
# envelope is SOAP 1.1. Only the string constants are used — we walk XML via
# `_local_name` / `_first_child` rather than ElementTree's namespaced XPath,
# so no `{prefix: uri}` dict is needed.
NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
NS_TEMPURI = "http://tempuri.org/"

# README § 2 Field naming: the Altium search index mangles most field names
# with a 32-char hex schema-GUID suffix. Only two suffixes have ever been
# observed; they're stable so we hard-code them.
SCHEMA_SUFFIX_CAT = "DD420E8DDD8B445E911A0601BB2B6D53"  # catalog / content-type fields
SCHEMA_SUFFIX_ITEM = "C623975962814A5FAAD7FA1CD85DA0DB"  # vault-item / revision fields

# Where `login` writes (and `list` / `download` / `repos` read) the minted
# session. Lives in CWD so multiple workspaces can coexist by `cd`-ing.
# The file is 0600 and contains both the short AFSSessionID and the JWT —
# treat it like an SSH private key.
AUTH_TOKEN_FILE = Path(".auth-token.json")

# Vault SOAP link records (§ 11.1) use the link HRID to identify the type.
# ComponentTemplate links point at a schema template revision, not a
# downloadable binary asset — skip it when walking for downloads.
LINK_HRID_SKIP = {"ComponentTemplate"}

# OIDC / OAuth 2.0 — README § 14.1.1. These are the static values the Altium
# Designer desktop client uses against `auth.altium.com`. The client is
# public (PKCE substitutes for a secret), so reusing them from a custom
# CLI is fine.
OIDC_CLIENT_ID = "3CD47A94-0610-4FA9-B3E4-C9C256FD84AE"
OIDC_REDIRECT_URI = "https://auth.altium.com/api/AuthComplete"
OIDC_SCOPE = "a365 a365:requirements openid profile"
OIDC_AUTH_BASE = "https://auth.altium.com"
OIDC_AUTHORIZE_URL = f"{OIDC_AUTH_BASE}/connect/authorize"
OIDC_TOKEN_URL = f"{OIDC_AUTH_BASE}/connect/token"
# Headless-login endpoint (README § 14.1.6) — drives the IdentityServer 4
# sign-in SPA's JSON API directly, no browser needed.
OIDC_SIGNIN_URL = f"{OIDC_AUTH_BASE}/api/account/signIn"
# README § 14.1.4 — the out-of-band bridge that hands the OAuth code from
# the browser back to the desktop client (or, here, our CLI).
ACTIONWAIT_URL = "https://actionwait.altium.com/await"

# README § 14.4 — the only endpoint that maps a logged-in user to the set
# of workspaces they can reach (with each workspace's per-slug
# `hostingurl`). We need this before we can call servicediscovery/Login
# because the JWT alone is workspace-agnostic.
WORKSPACES_LISTER_URL = (
    "https://workspaces.altium.com/workspaceexternalservices/"
    "WorkspaceHelperService.asmx"
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class ConfigError(RuntimeError):
    """Raised when `load_config()` can't assemble a complete `Config`.

    A subclass of `RuntimeError` so the existing CLI error handler in
    `cli.py:main` (which catches `RuntimeError` and prints it via the
    Rich stderr console) handles it without special casing.
    """


@dataclass(frozen=True)
class Config:
    session_id: str
    workspace_host: str
    regional_host: str
    git_host: str
    # Account email — used for HTTP Basic auth on the git host (README § 16.1).
    # Optional because the other operations don't need it and a session
    # minted via `ALTIUM_SESSION_ID` alone wouldn't supply it.
    email: str | None = None

    @property
    def vault_soap_url(self) -> str:
        return f"https://{self.workspace_host}/vault/?cls=soap"

    @property
    def search_url(self) -> str:
        return f"https://{self.regional_host}/search/v1.0/searchasync"

    @property
    def projects_service_url(self) -> str:
        return f"https://{self.regional_host}/projects/ProjectsService.asmx"

    def git_clone_url(self, repository_path: str) -> str:
        """Build a git clone URL from a project's REPOSITORYPATH (§ 16.2)."""
        return f"https://{self.git_host}/git/{repository_path}.git"


@dataclass(frozen=True)
class AuthToken:
    """What `login` writes to `.auth-token.json` and the other commands
    read back. A flat bag of everything the servicediscovery `Login`
    response hands us plus the OIDC tokens — kept together so a future
    `partcatalog` command can pull `access_token` (§ 1, `LiveSessionId`)
    without re-running the whole OIDC flow.
    """

    email: str
    session_id: str
    workspace_host: str
    regional_host: str | None
    git_host: str | None
    access_token: str
    id_token: str
    created_at: str
    endpoints: dict[str, str]

    def to_json(self) -> dict:
        return {
            "email": self.email,
            "session_id": self.session_id,
            "workspace_host": self.workspace_host,
            "regional_host": self.regional_host,
            "git_host": self.git_host,
            "access_token": self.access_token,
            "id_token": self.id_token,
            "created_at": self.created_at,
            "endpoints": self.endpoints,
        }

    def write(self, path: Path) -> None:
        path.write_text(_json.dumps(self.to_json(), indent=2))
        # Contains the JWT + short session — lock it down.
        try:
            path.chmod(0o600)
        except OSError:
            pass

    @classmethod
    def load(cls, path: Path) -> "AuthToken":
        data = _json.loads(path.read_text())
        # `email` is required — `clone` and any future user-identified
        # operation depends on it, and `login` always writes it. Fail
        # loudly instead of silently defaulting to an empty string.
        if not data.get("email"):
            raise ValueError(
                f"{path}: missing `email` field. Re-run `login` to mint a fresh token."
            )
        return cls(
            email=data["email"],
            session_id=data["session_id"],
            workspace_host=data["workspace_host"],
            regional_host=data.get("regional_host"),
            git_host=data.get("git_host"),
            access_token=data.get("access_token", ""),
            id_token=data.get("id_token", ""),
            created_at=data.get("created_at", ""),
            endpoints=data.get("endpoints") or {},
        )

    def to_config(self) -> Config:
        """Build a `Config` from this token. Used by `Altium365Api` to
        populate the `config` property right after a successful `login`
        call, so the subsequent calls don't re-read `.auth-token.json`
        from disk.

        Raises if the token is incomplete (no regional or git host —
        which should never happen in a real workspace, since the
        servicediscovery endpoint directory is guaranteed to list them).
        """
        if not self.regional_host or not self.git_host:
            raise RuntimeError(
                f"AuthToken for {self.email!r} has incomplete endpoint "
                f"discovery (regional_host={self.regional_host!r}, "
                f"git_host={self.git_host!r}). Re-run `login`."
            )
        return Config(
            session_id=self.session_id,
            workspace_host=self.workspace_host,
            regional_host=self.regional_host,
            git_host=self.git_host,
            email=self.email,
        )


def _try_load_auth_token(path: Path = AUTH_TOKEN_FILE) -> AuthToken | None:
    """Best-effort read of the cached auth token. Returns None if the file
    does not exist; warns and returns None on parse errors so the caller
    can still fall back to env vars / CLI flags.
    """
    if not path.exists():
        return None
    try:
        return AuthToken.load(path)
    except Exception as e:  # noqa: BLE001
        logger.warning("could not read %s: %s; falling back to env vars.", path, e)
        return None


def load_config() -> Config:
    """Build a Config for `list` / `download` / `repos` from (in precedence
    order) env vars → `.auth-token.json`.

    `login` writes the auth file with every host populated from the
    servicediscovery endpoint directory, so in the common case every
    field comes from there and the user never has to set anything by
    hand. If neither a token nor the env vars supply a required field,
    raises with a clear list of what's missing — no hard-coded
    workspace-specific fallbacks.
    """
    token = _try_load_auth_token()

    sid = os.environ.get("ALTIUM_SESSION_ID") or (token.session_id if token else None)
    workspace_host = os.environ.get("ALTIUM_WORKSPACE_HOST") or (
        token.workspace_host if token else None
    )
    regional_host = os.environ.get("ALTIUM_REGIONAL_HOST") or (
        token.regional_host if token else None
    )
    git_host = os.environ.get("ALTIUM_GIT_HOST") or (token.git_host if token else None)
    email = os.environ.get("ALTIUM_EMAIL") or (token.email if token else None)

    missing = [
        env_var
        for env_var, val in {
            "ALTIUM_SESSION_ID": sid,
            "ALTIUM_WORKSPACE_HOST": workspace_host,
            "ALTIUM_REGIONAL_HOST": regional_host,
            "ALTIUM_GIT_HOST": git_host,
        }.items()
        if not val
    ]
    if missing:
        raise ConfigError(
            f"no Altium config. Run `login` first to create "
            f"`{AUTH_TOKEN_FILE}`, or set: {', '.join(missing)}."
        )

    # All four are guaranteed non-empty here; narrow for the type checker.
    assert sid and workspace_host and regional_host and git_host
    return Config(
        session_id=sid,
        workspace_host=workspace_host,
        regional_host=regional_host,
        git_host=git_host,
        email=email,
    )


# ---------------------------------------------------------------------------
# Search — README § 2
# ---------------------------------------------------------------------------


def _search_condition(
    field: str, value: str, *, occur: int = 0, query_type: str = "Strict"
) -> dict:
    """One leaf condition inside a SearchRequest boolean query.

    occur: 0=MUST, 1=SHOULD, 2=MUST_NOT (README § 2 Occur).
    """
    return {
        "$type": "DtoSearchConditionBooleanQueryItem",
        "Item": {
            "$type": f"DtoSearchCondition{query_type}Query",
            "Term": {
                "$type": "DtoSearchConditionTerm",
                "Field": field,
                "Value": value,
            },
        },
        "Occur": occur,
    }


def _build_component_search(component_type: str | None, limit: int) -> dict:
    """Build a SearchRequest envelope for 'list all components [of type X]'.

    Field names carry a 32-hex schema suffix (README § 2 Field naming).
    See `SCHEMA_SUFFIX_CAT` / `SCHEMA_SUFFIX_ITEM` at the top of the
    module for the two observed values.
    """
    items: list[dict] = [
        _search_condition(f"ContentType{SCHEMA_SUFFIX_CAT}", "Component", occur=0),
        _search_condition(
            f"Id{SCHEMA_SUFFIX_ITEM}", "r_", occur=0, query_type="Wildcard"
        ),
        # NOTE: `LatestRevision` is a *catalog* field (SCHEMA_SUFFIX_CAT),
        # not a vault-item field. Not obvious from the name — the
        # heuristic "revision metadata → SCHEMA_SUFFIX_ITEM" would guess
        # wrong here. Verified against all three `altium_proxy.har` /
        # `atopile-2.har` / `checkappexec.har` captures, which send it as
        # `LatestRevisionDD420E8DDD8B445E911A0601BB2B6D53`. Changing to
        # SCHEMA_SUFFIX_ITEM breaks the filter (silently — the server
        # returns all revisions instead of only the latest).
        _search_condition(f"LatestRevision{SCHEMA_SUFFIX_CAT}", "1", occur=0),
        _search_condition(f"IsActive{SCHEMA_SUFFIX_ITEM}", "0", occur=2),
    ]
    if component_type:
        items.append(
            _search_condition(
                f"ComponentType{SCHEMA_SUFFIX_CAT}", component_type, occur=0
            )
        )

    return {
        "request": {
            "$type": "SearchRequest",
            "Condition": {
                "$type": "DtoSearchConditionBooleanQuery",
                "Items": items,
            },
            "SortFields": [
                {
                    "$type": "DtoSortSearchField",
                    "Name": "<score>",
                    "Order": 1,
                }
            ],
            "ReturnFields": None,
            "Start": 0,
            "Limit": limit,
            "IncludeFacets": False,
            "UseOnlyBestFacets": False,
            "IncludeDebugInfo": False,
            "IgnoreCaseFieldNames": False,
        }
    }


def _doc_field(doc: dict, unsuffixed_name: str) -> str | None:
    """Look up a field in a `Documents[i].Fields` list tolerating the
    32-hex schema-GUID suffix Altium appends to most names (README § 2).

    Matches the bare name or the name with one of the two known schema
    suffixes. Earlier versions used `startswith`, which silently
    returned the wrong value for pairs like `ComponentType` vs
    `ComponentTypeGUID` — the latter is a sibling field and the first
    one to appear in `Fields[]` would win.
    """
    candidates = {
        unsuffixed_name,
        unsuffixed_name + SCHEMA_SUFFIX_CAT,
        unsuffixed_name + SCHEMA_SUFFIX_ITEM,
    }
    for f in doc.get("Fields", []):
        if f.get("Name") in candidates:
            return f.get("Value")
    return None


def _parse_component_row(doc: dict) -> dict[str, str | None]:
    raw_id = _doc_field(doc, "Id")
    rev_guid = raw_id[2:] if raw_id and raw_id.startswith("R_") else raw_id
    return {
        "HRID": _doc_field(doc, "HRID"),
        "ItemHRID": _doc_field(doc, "ItemHRID"),
        "ComponentType": _doc_field(doc, "ComponentType"),
        "Description": _doc_field(doc, "Description"),
        "ItemGUID": _doc_field(doc, "ItemGUID"),
        "RevisionGUID": rev_guid,
    }


# ---------------------------------------------------------------------------
# Vault SOAP — README § 11
# ---------------------------------------------------------------------------


# Thin wrapper around `xml.sax.saxutils.escape` so every call site uses the
# same entity set (`&`, `<`, `>`, `'`, `"`). The stdlib function only
# escapes `&<>` by default; the two extra entities are necessary because
# we interpolate into single- and double-quoted attribute/filter values
# elsewhere in the module.
_XML_EXTRA_ENTITIES = {"'": "&apos;", '"': "&quot;"}


def _xml_escape(s: str) -> str:
    return _xml_sax_escape(s, _XML_EXTRA_ENTITIES)


# Only user-facing text that is interpolated into an Altium SOAP <Filter>
# expression is the HRID that `download` takes on the CLI. Because the
# server XML-decodes the filter before parsing its SQL-like grammar,
# `&apos;` round-trips back to `'` and would break out of the
# single-quoted filter string. XML escaping alone is NOT enough —
# whitelist the HRID character set instead, matching everything observed
# in the HAR captures (alphanumeric + space + `._-`).
_HRID_SAFE_RE = re.compile(r"^[A-Za-z0-9 ._-]+$")


def _validate_hrid(hrid: str) -> str:
    """Reject HRIDs that contain characters outside the observed safe
    set. Returns the HRID unchanged on success, raises `ValueError` on
    rejection. Used as a defence against filter-expression injection
    via user input to `download` / `_get_item_revisions_by_hrid`.
    """
    if not _HRID_SAFE_RE.match(hrid):
        raise ValueError(
            f"invalid HRID {hrid!r}: only [A-Za-z0-9 ._-] characters are allowed"
        )
    return hrid


def _soap_envelope(op: str, session: str, inner: str) -> str:
    """Build a vault SOAP envelope. README § 11 Common envelope."""
    return (
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        "<s:Header>"
        "<APIVersion>2.0</APIVersion>"
        "<User-Agent>AltiumDesignerDevelop-VaultClient</User-Agent>"
        "<X-Request-ID/>"
        "<X-Request-Depth>2</X-Request-Depth>"
        "</s:Header>"
        f'<s:Body><{op} xmlns="{NS_TEMPURI}">'
        f"{inner}"
        f"<SessionHandle>{_xml_escape(session)}</SessionHandle>"
        f"</{op}></s:Body>"
        "</s:Envelope>"
    )


def _post_soap(
    client: httpx.Client, url: str, op: str, session: str, inner: str
) -> ET.Element:
    body = _soap_envelope(op, session, inner)
    resp = client.post(
        url,
        content=body,
        headers={
            "Authorization": f"AFSSessionID {session}",
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{op}"',
        },
        timeout=60,
    )
    resp.raise_for_status()
    return ET.fromstring(resp.text)


def _iter_records(resp_root: ET.Element, response_op: str) -> list[ET.Element]:
    """The vault SOAP bulk ops wrap records in:
        <s:Envelope><s:Body><{op}Response xmlns="tempuri"><Records><item>...
    Returns the list of <item> elements, or [] if the response is
    empty-but-successful.
    """
    records = _first_child(
        _first_child(_first_child(resp_root, "Body"), response_op), "Records"
    )
    if records is None:
        return []
    return [c for c in records if _local_name(c) == "item"]


def _method_result_success(resp_root: ET.Element, response_op: str) -> None:
    """Raise RuntimeError on <MethodResult><Success>false."""
    mr = _first_child(
        _first_child(_first_child(resp_root, "Body"), response_op), "MethodResult"
    )
    if mr is None:
        return
    success = _first_child(mr, "Success")
    if success is None or (success.text or "").strip().lower() == "true":
        return
    msg = _first_child(mr, "Message")
    text = (msg.text or "").strip() if msg is not None and msg.text else ""
    raise RuntimeError(f"{response_op}: Success=false, Message={text!r}")


def _local_name(e: ET.Element) -> str:
    """Return the element's tag with any XML namespace prefix stripped.

    Altium SOAP responses use `xmlns:soap-env=` / inconsistent prefixes,
    and stdlib ElementTree's XPath can't match by local-name(), so we
    walk manually and compare against this.
    """
    return e.tag.rsplit("}", 1)[-1] if "}" in e.tag else e.tag


def _first_child(elem: ET.Element | None, name: str) -> ET.Element | None:
    """First direct child of `elem` whose local-name equals `name`, or
    None. Tolerates `elem is None` so chains of calls can read cleanly
    without sprinkling `if x is not None` everywhere.
    """
    if elem is None:
        return None
    for c in elem:
        if _local_name(c) == name:
            return c
    return None


def _child_text(item: ET.Element, name: str) -> str | None:
    c = _first_child(item, name)
    return c.text if c is not None else None


# ---- concrete SOAP ops --------------------------------------------------


def _get_item_revisions_by_hrid(
    client: httpx.Client, cfg: Config, hrid: str
) -> ET.Element:
    """Return the first <item> record for a GetALU_ItemRevisions call
    filtered by HRID. Raises if zero or multiple matches, or if the
    HRID contains characters that could break out of the SQL-like
    filter string (see `_validate_hrid`)."""
    hrid = _validate_hrid(hrid)
    inner = (
        f"<Filter>HRID='{_xml_escape(hrid)}'</Filter>"
        "<InputCursor/>"
        '<Options xmlns:i="http://www.w3.org/2001/XMLSchema-instance">'
        "<item>IncludeItemRevisionParameters=false</item>"
        "<item>SupportOwnerAclType=true</item>"
        "</Options>"
    )
    root = _post_soap(
        client, cfg.vault_soap_url, "GetALU_ItemRevisions", cfg.session_id, inner
    )
    _method_result_success(root, "GetALU_ItemRevisionsResponse")
    items = _iter_records(root, "GetALU_ItemRevisionsResponse")
    if not items:
        raise RuntimeError(f"no item-revision found with HRID={hrid!r}")
    if len(items) > 1:
        raise RuntimeError(
            f"multiple ({len(items)}) item-revisions match HRID={hrid!r}; "
            f"expected unique"
        )
    return items[0]


def _get_item_revisions_by_guids(
    client: httpx.Client, cfg: Config, guids: list[str]
) -> dict[str, str]:
    """GUID → HRID map via GetALU_ItemRevisions with a GUID IN filter."""
    guid_list = ",".join(f"'{_xml_escape(g)}'" for g in guids)
    inner = (
        f"<Filter>GUID IN ({guid_list})</Filter>"
        "<InputCursor/>"
        '<Options xmlns:i="http://www.w3.org/2001/XMLSchema-instance">'
        "<item>IncludeItemRevisionParameters=false</item>"
        "<item>SupportOwnerAclType=true</item>"
        "</Options>"
    )
    root = _post_soap(
        client, cfg.vault_soap_url, "GetALU_ItemRevisions", cfg.session_id, inner
    )
    _method_result_success(root, "GetALU_ItemRevisionsResponse")
    out: dict[str, str] = {}
    for item in _iter_records(root, "GetALU_ItemRevisionsResponse"):
        guid = _child_text(item, "GUID")
        hrid = _child_text(item, "HRID")
        if guid and hrid:
            out[guid.upper()] = hrid
    return out


def _get_item_revision_links(
    client: httpx.Client, cfg: Config, parent_rev_guid: str
) -> list[tuple[str, str]]:
    """Return [(link_hrid, child_rev_guid)] for a parent revision.
    README § 11.1. link_hrid is one of PCBLIB, PCBLIB 1, SCHLIB,
    ComponentTemplate, ... in the captures.
    """
    inner = (
        f"<Filter>ParentItemRevisionGUID IN ('{_xml_escape(parent_rev_guid)}')</Filter>"
        "<InputCursor/>"
        '<Options xmlns:i="http://www.w3.org/2001/XMLSchema-instance">'
        "<item>IncludeAllChildObjects=True</item>"
        "<item>NotFilterRbComponentLinks=True</item>"
        "</Options>"
    )
    root = _post_soap(
        client,
        cfg.vault_soap_url,
        "GetALU_ItemRevisionLinks",
        cfg.session_id,
        inner,
    )
    _method_result_success(root, "GetALU_ItemRevisionLinksResponse")
    rows: list[tuple[str, str]] = []
    for item in _iter_records(root, "GetALU_ItemRevisionLinksResponse"):
        link_hrid = _child_text(item, "HRID") or ""
        child_guid = _child_text(item, "ChildItemRevisionGUID") or ""
        if child_guid:
            rows.append((link_hrid, child_guid))
    return rows


def _get_download_urls(
    client: httpx.Client, cfg: Config, rev_guids: list[str]
) -> list[str]:
    """README § 11.3. Returns one pre-signed S3 URL per input GUID, order preserved."""
    items_xml = "".join(f"<item>{_xml_escape(g)}</item>" for g in rev_guids)
    inner = (
        '<ItemRevisionGUIDList xmlns:i="http://www.w3.org/2001/XMLSchema-instance">'
        f"{items_xml}"
        "</ItemRevisionGUIDList>"
        '<Options xmlns:i="http://www.w3.org/2001/XMLSchema-instance">'
        "<item>GetDirectLinks=true</item>"
        "</Options>"
    )
    root = _post_soap(
        client,
        cfg.vault_soap_url,
        "GetALU_ItemRevisionDownloadURLs",
        cfg.session_id,
        inner,
    )

    # The download-URL response has a different shape: a single
    # <MethodResult> wrapping <Results><item><URL>... — no <Records>.
    method_result = _first_child(
        _first_child(
            _first_child(root, "Body"), "GetALU_ItemRevisionDownloadURLsResponse"
        ),
        "MethodResult",
    )
    if method_result is None:
        raise RuntimeError(
            "GetALU_ItemRevisionDownloadURLs: no MethodResult in response"
        )
    success = _first_child(method_result, "Success")
    if success is None or (success.text or "").strip().lower() != "true":
        raise RuntimeError("GetALU_ItemRevisionDownloadURLs: Success!=true")
    results = _first_child(method_result, "Results")
    if results is None:
        return []
    urls: list[str] = []
    for item in results:
        if _local_name(item) != "item":
            continue
        item_success = _first_child(item, "Success")
        if item_success is None or (item_success.text or "").strip().lower() != "true":
            message = _first_child(item, "Message")
            msg_text = message.text if message is not None else None
            raise RuntimeError(
                f"GetALU_ItemRevisionDownloadURLs: per-item failure: {msg_text!r}"
            )
        url_el = _first_child(item, "URL")
        if url_el is None or not url_el.text:
            raise RuntimeError(
                "GetALU_ItemRevisionDownloadURLs: missing <URL> in <item>"
            )
        urls.append(url_el.text)
    return urls


# ---------------------------------------------------------------------------
# Projects service SOAP — README § 6
# ---------------------------------------------------------------------------
#
# Unlike the vault SOAP (§ 11), ProjectsService.asmx has a few quirks:
#   - Lives on the regional host (usw.365), not the workspace host.
#   - No `Authorization` HTTP header; the session id is only in the body,
#     and the body element is `<sessionId>` (lowercase-s) not
#     `<SessionHandle>`.
#   - Custom SOAP header has `<User-Agent>AltiumDesignerDevelop-ProjectsClient`.
#   - SOAPAction is the fully-qualified tempuri URL
#     (e.g. `"http://tempuri.org/FindProjects"`).


def _projects_soap_envelope(op: str, inner: str) -> str:
    """Build a ProjectsService.asmx SOAP envelope.

    Note that (unlike the vault `_soap_envelope`) the session id is NOT
    injected by this function — it has to be embedded inside `inner` as
    `<sessionId>...</sessionId>`, because `FindProjects` expects it to
    appear *before* the other parameters in the body. The caller owns
    that placement.
    """
    return (
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        "<s:Header>"
        "<User-Agent>AltiumDesignerDevelop-ProjectsClient</User-Agent>"
        "<X-Request-ID/>"
        "<X-Request-Depth>2</X-Request-Depth>"
        "</s:Header>"
        f'<s:Body><{op} xmlns="{NS_TEMPURI}" '
        'xmlns:i="http://www.w3.org/2001/XMLSchema-instance">'
        f"{inner}"
        f"</{op}></s:Body>"
        "</s:Envelope>"
    )


def _post_projects_soap(
    client: httpx.Client, cfg: Config, op: str, inner: str
) -> ET.Element:
    body = _projects_soap_envelope(op, inner)
    resp = client.post(
        cfg.projects_service_url,
        content=body,
        headers={
            # No Authorization header — session travels inside the body.
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{NS_TEMPURI}{op}"',
            "User-Agent": "AltiumDesignerDevelop-ProjectsClient",
        },
        timeout=60,
    )
    resp.raise_for_status()
    return ET.fromstring(resp.text)


def _find_projects(
    client: httpx.Client, cfg: Config, count_per_page: int = 1000
) -> list[dict[str, str | None]]:
    """README § 6 FindProjects. Flattens the ProjectExt records into dicts.

    Uses a single page with a large `CountPerPage` — good enough for any
    human-sized workspace. Pagination via `StartIndex` is supported by
    the server but not exercised here.
    """
    inner = (
        f"<sessionId>{_xml_escape(cfg.session_id)}</sessionId>"
        "<paramList>"
        "<AccessType>All</AccessType>"
        "<OwnerType>Any</OwnerType>"
        "<OrderByAsc>false</OrderByAsc>"
        "<StartIndex>0</StartIndex>"
        f"<CountPerPage>{count_per_page}</CountPerPage>"
        "<IncludeAccessRights>true</IncludeAccessRights>"
        "<IncludeVariantParameters>false</IncludeVariantParameters>"
        "</paramList>"
        "<sendRealHRID>true</sendRealHRID>"
    )
    root = _post_projects_soap(client, cfg, "FindProjects", inner)

    result = _first_child(
        _first_child(_first_child(root, "Body"), "FindProjectsResponse"),
        "FindProjectsResult",
    )
    if result is None:
        return []

    rows: list[dict[str, str | None]] = []
    fields = (
        "GUID",
        "HRID",
        "NAME",
        "DESCRIPTION",
        "PROJECTTYPE",
        "REPOSITORYGUID",
        "REPOSITORYPATH",
        "CREATEDAT",
        "LASTMODIFIEDAT",
        "ACCESSTYPE",
        "ISACTIVE",
    )
    for project_ext in result:
        if _local_name(project_ext) != "ProjectExt":
            continue
        row: dict[str, str | None] = {f: _child_text(project_ext, f) for f in fields}
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# OIDC / servicediscovery login — README §§ 14.1, 14.5, 12.2
# ---------------------------------------------------------------------------


def _b64url_nopad(data: bytes) -> str:
    """base64url without trailing `=` padding — the form PKCE/JWT use."""
    return _b64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode_jwt_payload(jwt: str) -> dict:
    """Decode the middle segment of a JWT without verifying the signature.

    Only used here to extract the `username` / `email` claim from the
    `id_token` so the caller doesn't have to type their own email; the
    servicediscovery `Login` call (§ 12.2) needs it as `<userName>`.
    """
    parts = jwt.split(".")
    if len(parts) < 2:
        raise ValueError("malformed JWT")
    payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
    return _json.loads(_b64.urlsafe_b64decode(payload_b64).decode("utf-8"))


def _actionwait_pickup(client: httpx.Client, state: str, timeout_s: int) -> str:
    """Poll `actionwait.altium.com/await` until the browser has delivered the
    OAuth code keyed by `state` (§ 14.1.4), or `timeout_s` seconds pass.
    Returns the one-time authorization code.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            resp = client.post(
                ACTIONWAIT_URL,
                json={"token": state},
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=60,
            )
        except httpx.ReadTimeout:
            # The server may long-poll; just retry until our outer deadline.
            continue
        if resp.status_code == 200:
            try:
                body = resp.json()
            except ValueError:
                body = {}
            if body.get("actionResult") == "OK" and isinstance(body.get("data"), dict):
                data = body["data"]
                returned_state = data.get("state")
                if returned_state and returned_state != state:
                    raise RuntimeError(
                        f"OAuth state mismatch: sent {state!r}, got "
                        f"{returned_state!r} (possible CSRF)"
                    )
                code = data.get("code")
                if code:
                    return code
        time.sleep(1)
    raise RuntimeError(
        f"timed out after {timeout_s}s waiting for OAuth callback via {ACTIONWAIT_URL}"
    )


def _generate_pkce() -> tuple[str, str, str]:
    """Return `(state_uuid, code_verifier, code_challenge)` for PKCE.

    64 random bytes → 86-char verifier (within RFC 7636's 43-128 range);
    challenge is S256 of the verifier.
    """
    state = str(uuid.uuid4())
    code_verifier = _b64url_nopad(secrets.token_bytes(64))
    code_challenge = _b64url_nopad(hashlib.sha256(code_verifier.encode()).digest())
    return state, code_verifier, code_challenge


def _build_authorize_url(state: str, code_challenge: str) -> str:
    """Build the `/connect/authorize` URL with the standard Altium Designer
    client params (README § 14.1.3). Shared by the browser and headless
    flows."""
    return (
        OIDC_AUTHORIZE_URL
        + "?"
        + urlencode(
            {
                "client_id": OIDC_CLIENT_ID,
                "response_type": "code",
                "scope": OIDC_SCOPE,
                "redirect_uri": OIDC_REDIRECT_URI,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "state": state,
            }
        )
    )


def _exchange_code_for_tokens(
    client: httpx.Client, code: str, code_verifier: str
) -> tuple[str, str]:
    """POST `/connect/token` with an authorization code + PKCE verifier
    (RFC 6749 + RFC 7636). Returns `(access_token, id_token)`.
    """
    resp = client.post(
        OIDC_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": OIDC_REDIRECT_URI,
            "code_verifier": code_verifier,
            "client_id": OIDC_CLIENT_ID,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=60,
    )
    resp.raise_for_status()
    payload = resp.json()
    access_token = payload.get("access_token")
    id_token = payload.get("id_token")
    if not access_token or not id_token:
        raise RuntimeError(
            f"/connect/token response missing access_token/id_token: {payload!r}"
        )
    return access_token, id_token


def _oidc_pkce_login(
    client: httpx.Client, *, timeout_s: int, open_browser: bool
) -> tuple[str, str]:
    """Drive the browser-based OAuth 2.0 PKCE flow against `auth.altium.com`.
    Returns `(access_token, id_token)`.

    The browser hand-off uses Altium's own hosted callback + the
    actionwait bridge, so no localhost listener or custom URL scheme is
    needed (README § 14.1.4).
    """
    state, code_verifier, code_challenge = _generate_pkce()
    authorize_url = _build_authorize_url(state, code_challenge)

    # `print` (not logger) because this URL is load-bearing UX: the flow
    # hangs in `_actionwait_pickup` until the user clicks it. Keep plain
    # text — api.py is a library and shouldn't depend on Rich.
    print(f"Open this URL in your browser to authenticate:\n  {authorize_url}")
    if open_browser:
        try:
            webbrowser.open(authorize_url)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "could not launch browser (%s); open the URL above manually.", e
            )

    logger.info(
        "waiting for OAuth callback (state=%s..., timeout=%ds)...",
        state[:8],
        timeout_s,
    )
    code = _actionwait_pickup(client, state, timeout_s)
    return _exchange_code_for_tokens(client, code, code_verifier)


def _oidc_password_login(
    client: httpx.Client, *, email: str, password: str
) -> tuple[str, str]:
    """Headless email+password OAuth flow. Returns `(access_token, id_token)`.

    Drives the IdentityServer 4 SPA's JSON API directly (README § 14.1.6):

      1. GET  /connect/authorize   (no cookies) → 302 to /signin?ReturnUrl=...
      2. POST /api/account/signIn  {userName, password, persistent, returnUrl}
         sets `idsrv.session` + `ALU_SID_2` cookies on the client jar.
      3. GET  /connect/authorize/callback  (with cookies) → 302 to
         /api/AuthComplete?code=...&state=...
      4. POST /connect/token       trades the code for JWTs.

    Requires an httpx client with a persistent cookie jar — `http_client()`
    produces one. No browser, no actionwait, no localhost listener.
    """
    state, code_verifier, code_challenge = _generate_pkce()
    authorize_url = _build_authorize_url(state, code_challenge)

    # Step 1 — GET /connect/authorize, do NOT follow the 302. Without
    # session cookies the server stashes the authorize params behind an
    # opaque authzId and redirects us to /signin?ReturnUrl=...
    authorize_resp = client.get(authorize_url, follow_redirects=False, timeout=60)
    if authorize_resp.status_code != 302:
        raise RuntimeError(
            f"/connect/authorize expected 302, got {authorize_resp.status_code}; "
            f"body={authorize_resp.text[:400]!r}"
        )
    location = authorize_resp.headers.get("location", "")
    location_qs = parse_qs(urlparse(location).query)
    return_url_values = (
        location_qs.get("ReturnUrl") or location_qs.get("returnUrl") or []
    )
    if not return_url_values:
        raise RuntimeError(
            f"/connect/authorize redirect missing ReturnUrl: Location={location!r}"
        )
    return_url = return_url_values[0]

    # Step 2 — POST /api/account/signIn with the credentials and the
    # returnUrl from step 1. On success the server sets idsrv.session +
    # ALU_SID_2 cookies on the httpx client jar.
    signin_body = _json.dumps(
        {
            "userName": email,
            "password": password,
            "persistent": True,
            "returnUrl": return_url,
            "visitorId": None,
        }
    ).encode("utf-8")
    signin_resp = client.post(
        OIDC_SIGNIN_URL,
        content=signin_body,
        headers={"Content-Type": "application/json-patch+json"},
        timeout=60,
    )
    if signin_resp.status_code != 200:
        raise RuntimeError(
            f"/api/account/signIn failed ({signin_resp.status_code}): "
            f"{signin_resp.text[:400]!r}"
        )
    try:
        signin_payload = signin_resp.json()
    except ValueError:
        signin_payload = {}
    # The signIn response echoes the returnUrl; prefer it verbatim so the
    # server sees exactly what it expects on the callback.
    canonical_return_url = signin_payload.get("returnUrl") or return_url

    # Step 3 — Follow the callback with the now-authenticated cookie jar.
    # The server mints a fresh OAuth code and 302s to /api/AuthComplete?code=...
    callback_url = urljoin(OIDC_AUTH_BASE, canonical_return_url)
    callback_resp = client.get(callback_url, follow_redirects=False, timeout=60)
    if callback_resp.status_code != 302:
        raise RuntimeError(
            f"/connect/authorize/callback expected 302, got "
            f"{callback_resp.status_code}: {callback_resp.text[:400]!r}"
        )
    cb_location = callback_resp.headers.get("location", "")
    cb_qs = parse_qs(urlparse(cb_location).query)
    code_values = cb_qs.get("code") or []
    if not code_values:
        raise RuntimeError(f"callback redirect had no code: Location={cb_location!r}")
    returned_state = (cb_qs.get("state") or [None])[0]
    if returned_state != state:
        raise RuntimeError(
            f"OAuth state mismatch: sent {state!r}, got {returned_state!r} "
            "(possible CSRF)"
        )

    # Step 4 — Trade the code for JWTs (identical to the browser path).
    return _exchange_code_for_tokens(client, code_values[0], code_verifier)


def _get_user_workspaces(client: httpx.Client, jwt: str) -> list[dict[str, str | None]]:
    """Call `GetUserWorkspaces` on `workspaces.altium.com` with the JWT
    in a SOAP `<UserCredentials><password>` header (README § 14.4).

    Returns one dict per `UserWorkspaceInfo` with the fields we care
    about: `name`, `hostingurl`, `hostname` (extracted from hostingurl),
    `spacesubscriptionguid`, `isdefault`, `statusname`, `locationname`.

    Auth is the JWT inside the SOAP header in an element confusingly
    named `<password>` — **not** a password, just the OIDC access token.
    No HTTP `Authorization` header, no cookies.
    """
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">'
        "<SOAP-ENV:Header>"
        f'<UserCredentials xmlns="{NS_TEMPURI}">'
        f"<password>{_xml_escape(jwt)}</password>"
        "</UserCredentials>"
        "</SOAP-ENV:Header>"
        "<SOAP-ENV:Body>"
        f'<GetUserWorkspaces xmlns="{NS_TEMPURI}"/>'
        "</SOAP-ENV:Body>"
        "</SOAP-ENV:Envelope>"
    )
    resp = client.post(
        WORKSPACES_LISTER_URL,
        content=body,
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"{NS_TEMPURI}GetUserWorkspaces"',
        },
        timeout=60,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.text)

    result_el = _first_child(
        _first_child(_first_child(root, "Body"), "GetUserWorkspacesResponse"),
        "GetUserWorkspacesResult",
    )
    if result_el is None:
        return []

    fields = (
        "name",
        "hostingurl",
        "spacesubscriptionguid",
        "isdefault",
        "statusname",
        "locationname",
    )
    rows: list[dict[str, str | None]] = []
    for info in result_el:
        if _local_name(info) != "UserWorkspaceInfo":
            continue
        row: dict[str, str | None] = {f: _child_text(info, f) for f in fields}
        # hostingurl is `https://<slug>.365.altium.com:443` — the port
        # isn't useful for downstream calls, just keep the bare host.
        host_url = row.get("hostingurl")
        row["hostname"] = urlparse(host_url).hostname if host_url else None
        rows.append(row)
    return rows


def _pick_workspace(
    workspaces: list[dict[str, str | None]],
    name: str | None = None,
) -> dict[str, str | None]:
    """Pick a single workspace from a `GetUserWorkspaces` result.

    If `name` is given, require an exact match on `name`. Otherwise
    prefer the user's default workspace (`isdefault=true`), then the
    first Active one, then the first record. Raises `RuntimeError` if
    the list is empty or `name` doesn't match anything.
    """
    if not workspaces:
        raise RuntimeError("GetUserWorkspaces returned no workspaces for this user")

    if name is not None:
        match = next((w for w in workspaces if w.get("name") == name), None)
        if match is None:
            available = sorted(w.get("name") or "?" for w in workspaces)
            raise RuntimeError(f"no workspace named {name!r}; available: {available}")
        return match

    default = next(
        (w for w in workspaces if (w.get("isdefault") or "").lower() == "true"),
        None,
    )
    if default is not None:
        return default

    active = next(
        (w for w in workspaces if (w.get("statusname") or "") == "Active"),
        None,
    )
    return active or workspaces[0]


def _servicediscovery_login(
    client: httpx.Client, workspace_host: str, email: str, jwt: str
) -> tuple[str, dict[str, str]]:
    """Mint a short `AFSSessionID` from the global JWT.

    README § 12.2 / § 14.5. Calls the workspace's servicediscovery SOAP
    endpoint with `password = *IDSGS*<JWT>` and parses
    `<UserInfo><SessionId>` out of the response.

    Also parses the `<Endpoints>` directory into a `{ServiceKind:
    ServiceUrl}` dict so callers can auto-discover the regional and git
    hosts for the workspace without hard-coding regional defaults.
    """
    url = f"https://{workspace_host}/servicediscovery/servicediscovery.asmx"
    # Note: the namespace is `http://altium.com/`, *not* `http://tempuri.org/` —
    # one of the very few Altium SOAP endpoints that differs (§ 12.2 note).
    body = (
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        "<s:Header/>"
        "<s:Body>"
        '<Login xmlns="http://altium.com/">'
        f"<userName>{_xml_escape(email)}</userName>"
        f"<password>*IDSGS*{_xml_escape(jwt)}</password>"
        "<secureLogin>false</secureLogin>"
        "<discoveryLoginOptions>None</discoveryLoginOptions>"
        "<productName>Altium Designer Develop</productName>"
        "</Login>"
        "</s:Body>"
        "</s:Envelope>"
    )
    resp = client.post(
        url,
        content=body,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": '"http://altium.com/Login"',
        },
        timeout=120,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.text)

    result_el = _first_child(
        _first_child(_first_child(root, "Body"), "LoginResponse"), "LoginResult"
    )
    sid_el = _first_child(_first_child(result_el, "UserInfo"), "SessionId")
    if sid_el is None or not sid_el.text:
        raise RuntimeError(
            f"servicediscovery Login: no <UserInfo><SessionId> in response; "
            f"body={resp.text[:500]!r}"
        )
    session_id = sid_el.text.strip()

    # Parse the endpoint directory — 65 entries in a typical workspace.
    endpoints: dict[str, str] = {}
    endpoints_el = _first_child(result_el, "Endpoints")
    if endpoints_el is not None:
        for ep in endpoints_el:
            if _local_name(ep) != "EndPointInfo":
                continue
            kind_el = _first_child(ep, "ServiceKind")
            url_el = _first_child(ep, "ServiceUrl")
            if (
                kind_el is not None
                and url_el is not None
                and kind_el.text
                and url_el.text
            ):
                endpoints[kind_el.text] = url_el.text

    return session_id, endpoints


def _endpoint_host(endpoints: dict[str, str], *kinds: str) -> str | None:
    """Return the hostname of the first ServiceKind in `kinds` that is
    present in `endpoints`, or None if none match.
    """
    for kind in kinds:
        url = endpoints.get(kind)
        if url:
            host = urlparse(url).hostname
            if host:
                return host
    return None


# ---------------------------------------------------------------------------
# Altium365Api — high-level entrypoint
# ---------------------------------------------------------------------------


class Altium365Api:
    """Stateful wrapper around the high-level Altium 365 operations.

    Holds an `httpx.Client` + a (lazily loaded) workspace `Config` so
    callers don't have to thread them through every call. Two usage
    patterns:

        # 1. Own the client + config lifecycle (creates both on demand).
        #    With no `workspace_host`, `login` auto-discovers the user's
        #    default workspace via `GetUserWorkspaces` (README § 14.4).
        with Altium365Api() as api:
            token = api.login()
            rows, total = api.search_components()

        # 2. Bring your own client (e.g. reuse one from an async stack):
        with http_client() as c:
            api = Altium365Api(client=c)
            hrid = "CMP-004-00028-3"
            for child, dest, size in api.download_item(hrid, Path("./out")):
                ...

    `config` is loaded from `.auth-token.json` (via `load_config()`) on
    first access. After a successful `login()`, it is populated from the
    freshly minted `AuthToken`, so no file read is needed.
    """

    def __init__(
        self,
        client: httpx.Client | None = None,
        config: Config | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client: httpx.Client | None = client
        # The context manager returned by `http_client()` — kept so we
        # can close it in __exit__ when we own the client.
        self._client_cm = None
        self._config: Config | None = config

    def __enter__(self) -> "Altium365Api":
        if self._owns_client and self._client is None:
            self._client_cm = http_client()
            self._client = self._client_cm.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._owns_client and self._client_cm is not None:
            self._client_cm.__exit__(exc_type, exc, tb)
            self._client = None
            self._client_cm = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            raise RuntimeError(
                "Altium365Api has no active client — pass one to __init__ "
                "or use `with Altium365Api() as api:`."
            )
        return self._client

    @property
    def config(self) -> Config:
        """Lazily loaded workspace config. Reads `.auth-token.json` (plus
        env-var and hard-coded fallbacks) on first access.
        """
        if self._config is None:
            self._config = load_config()
        return self._config

    def login(
        self,
        *,
        workspace_host: str | None = None,
        workspace_name: str | None = None,
        email: str | None = None,
        password: str | None = None,
        open_browser: bool = True,
        timeout_s: int = 300,
    ) -> AuthToken:
        """Run the full OIDC → workspace discovery → servicediscovery
        bootstrap and return an `AuthToken`. **Does not write the token
        to disk** — the caller owns persistence. Populates
        `self.config` from the fresh token so subsequent calls don't
        re-read `.auth-token.json`.

        Workspace selection (README § 14.4 — Altium's own desktop
        client runs exactly this discovery chain):

          1. `workspace_host=` explicit arg (bare hostname, e.g.
             `<workspace-slug>.365.altium.com`).
          2. `$ALTIUM_WORKSPACE_HOST` env var.
          3. Auto-discovery via `GetUserWorkspaces` using the freshly
             minted JWT. `workspace_name=` picks a specific workspace
             by its display name; otherwise the user's default
             workspace (`isdefault=true`) is used, falling back to the
             first Active one, then the first record.

        This is important because the JWT alone is workspace-agnostic
        — on a brand-new login we can't know the workspace slug ahead
        of time, so we have to ask Altium for it.

        Two modes, selected by whether `password` is set:

          Browser (password is None) — OIDC PKCE flow. Prints the
          authorize URL and (optionally) opens the system browser.
          Picks up the code out-of-band via
          `actionwait.altium.com/await` (README § 14.1.4).

          Headless (password is set) — drives `/api/account/signIn`
          directly (README § 14.1.6). Requires `email`.

        In both modes the flow then calls the workspace
        `servicediscovery` `Login` SOAP op with
        `password = *IDSGS*<JWT>` to mint the short `AFSSessionID`
        (§ 12.2 / § 14.5) and parses the endpoint directory to
        discover the regional and git hosts.
        """
        client = self.client

        if password is not None:
            if not email:
                raise ValueError("headless login requires an email")
            access_token, id_token = _oidc_password_login(
                client, email=email, password=password
            )
        else:
            access_token, id_token = _oidc_pkce_login(
                client, timeout_s=timeout_s, open_browser=open_browser
            )

        # In browser mode we usually don't know the email up front —
        # pull it out of the id_token claims (README § 14.1.3).
        if email is None:
            claims = _decode_jwt_payload(id_token)
            email = claims.get("username") or claims.get("email")
            if not email:
                raise RuntimeError(
                    "id_token has no `username` / `email` claim — pass email explicitly"
                )

        # Resolve the workspace: explicit arg → env var → GetUserWorkspaces.
        if workspace_host is None:
            workspace_host = os.environ.get("ALTIUM_WORKSPACE_HOST")
        if workspace_host is None:
            workspaces = _get_user_workspaces(client, access_token)
            picked = _pick_workspace(workspaces, name=workspace_name)
            workspace_host = picked.get("hostname")
            if not workspace_host:
                raise RuntimeError(
                    f"workspace {picked.get('name')!r} has no usable hostingurl"
                )
            logger.info(
                "auto-selected workspace %r (%s) from GetUserWorkspaces",
                picked.get("name"),
                workspace_host,
            )

        session_id, endpoints = _servicediscovery_login(
            client, workspace_host, email, access_token
        )

        # The regional host shows up under several ServiceKinds;
        # PROJECTS and SEARCHBASE are always populated. GITREST is the
        # canonical git host.
        regional_host = _endpoint_host(
            endpoints, "PROJECTS", "SEARCHBASE", "PARTCATALOG_API", "SEARCH"
        )
        git_host = _endpoint_host(endpoints, "GITREST")

        token = AuthToken(
            email=email,
            session_id=session_id,
            workspace_host=workspace_host,
            regional_host=regional_host,
            git_host=git_host,
            access_token=access_token,
            id_token=id_token,
            created_at=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            endpoints=endpoints,
        )
        # Populate config from the fresh token so subsequent calls
        # don't re-read `.auth-token.json`.
        self._config = token.to_config()
        return token

    def search_components(
        self,
        *,
        component_type: str | None = None,
        limit: int = SEARCH_LIMIT_ALL,
    ) -> tuple[list[dict[str, str | None]], int | None]:
        """List components in the workspace via the regional
        `searchasync` endpoint (README § 2). Returns
        `(rows, total_hit_count)` where each row is
        `{HRID, ItemHRID, ComponentType, Description, ItemGUID,
        RevisionGUID}` and `total` is the server-reported total match
        count (may be `None` if the server omits it).
        """
        cfg = self.config
        body = _build_component_search(component_type, limit)
        r = self.client.request(
            "REPORT",
            cfg.search_url,
            json=body,
            headers={
                "Authorization": f"AFSSessionID {cfg.session_id}",
                "Accept": "application/json",
            },
            timeout=120,
        )
        r.raise_for_status()
        payload = r.json()
        if not payload.get("Success", True):
            raise RuntimeError(f"searchasync: Success=false, payload={payload!r}")
        docs = payload.get("Documents") or []
        rows = [_parse_component_row(d) for d in docs]
        return rows, payload.get("Total")

    def download_item(
        self, hrid: str, out_dir: Path
    ) -> Iterator[tuple[str, Path, int]]:
        """Download the zip(s) for an item-revision HRID. Yields
        `(child_hrid, dest_path, size_bytes)` after each zip is
        written, so the caller can render per-item progress as
        downloads complete.

        For CMP-* HRIDs, walks the link table and downloads every
        non-template child (footprint(s) + symbol(s)). For other
        HRIDs, downloads the single matching zip. Yields nothing if
        the HRID has no downloadable children (empty links or only
        template links).

        Chain: `GetALU_ItemRevisions` → (for CMP only)
        `GetALU_ItemRevisionLinks` → `GetALU_ItemRevisionDownloadURLs`
        → S3 GET. See README § 11.12.
        """
        cfg = self.config
        client = self.client
        out_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: resolve HRID → revision GUID
        item = _get_item_revisions_by_hrid(client, cfg, hrid)
        root_guid = _child_text(item, "GUID")
        if not root_guid:
            raise RuntimeError(f"no GUID in ItemRevision for HRID={hrid!r}")

        # Step 2: CMP-* walks the link table; everything else is a single zip.
        if hrid.upper().startswith("CMP-"):
            link_rows = _get_item_revision_links(client, cfg, root_guid)
            child_guids = [
                g for (link_hrid, g) in link_rows if link_hrid not in LINK_HRID_SKIP
            ]
            if not child_guids:
                return
            # Name each zip by its own child HRID, not the parent's.
            guid_to_hrid = _get_item_revisions_by_guids(client, cfg, child_guids)
            download_guids = child_guids
        else:
            download_guids = [root_guid]
            guid_to_hrid = {root_guid.upper(): hrid}

        # Step 3: mint S3 URLs
        urls = _get_download_urls(client, cfg, download_guids)
        if len(urls) != len(download_guids):
            raise RuntimeError(
                f"broker returned {len(urls)} URLs for {len(download_guids)} GUIDs"
            )

        # Step 4: stream each zip from S3 with no Altium auth. Yield
        # after each one so the caller sees progress immediately.
        for guid, url in zip(download_guids, urls, strict=True):
            child_hrid = guid_to_hrid.get(guid.upper(), guid)
            dest = out_dir / f"{child_hrid}.zip"
            with client.stream("GET", url, follow_redirects=True, timeout=300) as s:
                s.raise_for_status()
                size = 0
                with dest.open("wb") as f:
                    for chunk in s.iter_bytes():
                        f.write(chunk)
                        size += len(chunk)
            yield child_hrid, dest, size

    def list_projects(self) -> list[dict[str, str | None]]:
        """Return the managed-projects (git repositories) directory
        for the workspace. Calls `FindProjects` on the regional
        ProjectsService (README § 6) and reshapes each record into a
        presentation-ready dict with keys: `Name, HRID, Type,
        Description, ProjectGUID, RepositoryPath, GitURL,
        LastModifiedAt`.

        `GitURL` is pre-built from `REPOSITORYPATH` via
        `cfg.git_clone_url` so the caller can pass it straight to
        `git clone` (with the appropriate HTTP Basic auth — see README
        § 16).
        """
        cfg = self.config
        projects = _find_projects(self.client, cfg)
        return [
            {
                "Name": p.get("NAME"),
                "HRID": p.get("HRID"),
                "Type": p.get("PROJECTTYPE"),
                "Description": p.get("DESCRIPTION"),
                "ProjectGUID": p.get("GUID"),
                "RepositoryPath": p.get("REPOSITORYPATH"),
                "GitURL": (
                    cfg.git_clone_url(repo_path)
                    if (repo_path := p.get("REPOSITORYPATH"))
                    else None
                ),
                "LastModifiedAt": p.get("LASTMODIFIEDAT"),
            }
            for p in projects
        ]

    def clone(
        self,
        git_url: str,
        target_path: Path,
        *,
        email: str | None = None,
    ) -> _git.Repo:
        """Clone a managed-project git repo at `git_url` into `target_path`.

        Uses GitPython's `Repo.clone_from` with the current session
        injected as HTTP Basic auth (README § 16.1 / § 16.3). The auth
        scheme on the Altium git host is:

            Authorization: Basic base64("<email>:<short AFSSessionID>")

        where the password is the **short `AFSSessionID`** (not the
        OAuth JWT, not the account password — that's the same string
        that travels as `Authorization: AFSSessionID <value>` on every
        other per-workspace call). The `email` side of the colon is
        effectively ignored by the server — identity comes from the
        session — but a clean client sends the real one.

        `email` defaults to `self.config.email` (written to
        `.auth-token.json` by `login`, or read from `$ALTIUM_EMAIL`).
        Raises if neither is set — the server ignores the username, but
        we still require one so the client stays honest about identity.

        Auth delivery: the Basic header is attached via the
        `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_<n>` / `GIT_CONFIG_VALUE_<n>`
        env vars (git ≥ 2.31), which ask git to apply a transient
        `http.extraheader` config for the invocation. This is cleaner
        than `-c http.extraheader=...` on the command line because the
        credential only lives in the child's environment, not in
        `argv` where `ps` would expose it.

        Caveats:
          - When the short session expires (hours, not days) git will
            start returning `401 Unauthorized`. Re-run `login`.
          - The session is workspace-scoped: a session minted for
            workspace A cannot clone a repo from workspace B.

        Raises:
          - `RuntimeError` if no email is available, or if git exits
            non-zero (wraps `GitCommandError`).
        """
        cfg = self.config
        user = email or cfg.email
        if not user:
            raise RuntimeError(
                "no email available for git Basic auth. Run `login` to "
                "populate `.auth-token.json`, set $ALTIUM_EMAIL, or pass "
                "`email=...` explicitly."
            )
        basic = _b64.b64encode(f"{user}:{cfg.session_id}".encode()).decode("ascii")

        # git clone creates the *leaf* target dir but not its parents.
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Transient `http.extraheader` via GIT_CONFIG_* (git ≥ 2.31).
        # GitPython merges `env` with the inherited environment.
        env = {
            "GIT_TERMINAL_PROMPT": "0",  # don't hang on a stale session
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraheader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
        }

        try:
            return _git.Repo.clone_from(git_url, str(target_path), env=env)
        except _git.GitCommandError as e:
            raise RuntimeError(f"git clone {git_url!r} failed: {e}") from e
