# Altium 365 — Reverse-Engineered API Notes

Reverse-engineered from `altium_proxy.har` (a proxy capture of the Altium
Designer ↔ Altium 365 traffic). This is **not** an official spec — only the
endpoints and fields actually observed are documented, with notes on what is
inferred vs. exactly known.

## 1. Common

### Hosts

There are five families of hosts in play:

| Host | Role | Auth |
|---|---|---|
| **Global identity / portal** | | |
| `auth.altium.com` | OIDC issuer. Mints the JWT (access token) that is the root credential. | — (standard OAuth2/OIDC; not in capture) |
| `portal365.altium.com` | SOAP portal: login/account/license/feature flags and the `GetPRT_GlobalServiceUrl` service-URL directory. All ops pass the JWT. | JWT in `<Handle>` / `<SessionID>` body element |
| `vault.api.altium.com` | Cross-workspace SOAP vault root. One op seen: `GetALU_VaultRecord` returns the Altium Content Vault capability flags. | JWT in `<SessionHandle>` body element |
| `workspaces.altium.com` | Workspace listing for the logged-in user (`GetUserWorkspaces`). | JWT (ambient — body is empty) |
| **Regional shared** | | |
| `usw.365.altium.com` | **US-West regional services** shared across workspaces in that region: search, partcatalog, dictionaries, componenthealth, comments, projects, settings, bom, insights, collaboration, ... (see § 14 for the full 65-entry service list). Other regions follow `<region>.365.altium.com`. | **Short** `AFSSessionID` HTTP header |
| **Per-workspace** | | |
| `<workspace-slug>.365.altium.com` (here: `atopile-2.365.altium.com`) | **Per-workspace** services: vault SOAP, service discovery, websocket, VCS, search templates, security, tasks, BMS, DSS, USERSUI, VAULTUI, etc. The slug is the workspace's URL slug (the workspace GUID `ECC90F5C-559E-4318-9CE1-88776570B379` here maps to slug `atopile-2`). Hosted on `Microsoft-IIS/10.0`. | **Short** `AFSSessionID` HTTP header **and** `<SessionHandle>` SOAP body element (same value) |
| **Binary assets** | | |
| `ccv-<region>.s3.<region>.amazonaws.com` (here: `ccv-us-west-2.s3.us-west-2.amazonaws.com`) | **Binary asset bucket** — the actual `.PcbLib` / `.SchLib` zips. Brokered by the workspace vault. | AWS SigV4 pre-signed URL (no Altium auth on the wire) |

All traffic is HTTPS / HTTP/1.1; the websocket on the workspace host upgrades
to `wss://`. The flow between them — JWT → account/license → workspace list
→ per-workspace short session — is documented as the **bootstrap flow** in
§ 14.

### Authentication

Three distinct credential forms appear across the captures:

1. **JWT (OIDC access token)** — the root credential. Issued by
   `auth.altium.com`. Claims observed:
   - `iss: https://auth.altium.com`
   - `scope: ["a365", "a365:requirements", "openid", "profile"]`
   - `sub: <user contact GUID, uppercase with dashes>`
   - `client_id: 3CD47A94-0610-4FA9-B3E4-C9C256FD84AE` (Altium Designer)
   - 30-day validity (2 592 000 s between `iat` and `exp`)

   Used on **global / identity** services: `portal365.altium.com`
   (`<SessionID>` / `<Handle>`), `vault.api.altium.com`
   (`<SessionHandle>`), `workspaces.altium.com` (ambient, body is empty),
   and as the "password" (prefixed with the magic string `*IDSGS*`) when
   calling the workspace `servicediscovery` `Login` operation.

2. **Short `AFSSessionID`** — minted by the workspace `servicediscovery`
   `Login` call (§ 14.5). Format:
   `<sessionGuid uppercase dashed><workspaceGuid lowercase dashed>`
   concatenated with no separator, e.g.:

   ```
   F29EC130-DBF9-4F11-A8C6-D39D9243DFE7ecc90f5c-559e-4318-9ce1-88776570b379
   ```

   The session prefix rotates per login; the workspace suffix is stable
   and equal to `<UserWorkspaceInfo>/spacesubscriptionguid` from
   `GetUserWorkspaces` (§ 14.4) and the first path segment of the S3
   URLs (§ 10).

   Used on **regional** (`usw.365.altium.com`) and **workspace**
   (`<workspace>.365.altium.com`) services, sent both as
   `Authorization: AFSSessionID <value>` HTTP header **and** as
   `<SessionHandle>` inside SOAP bodies (duplicated).

3. **AWS Signature V4 pre-signed URL** — minted on demand by
   `GetALU_ItemRevisionDownloadURLs` (§ 11.3) for each S3 download. Valid
   for 2 h. The signature **is** the auth; no Altium headers accompany it.

Variant cases:

| Where | Auth form | Note |
|---|---|---|
| `ProjectsService.asmx` (SOAP) | short session in `<sessionId>` body element only (no HTTP header) | legacy shape |
| WebSocket upgrade (§ 13) | `Cookie: ClientID={UUID}; IDS_SessionId=<short session>` | only place cookies are used |
| `partcatalog/.../ComponentDynamicData/Get` | short session in HTTP header **plus** a `LiveSessionId=<JWT>` string inside `Options[]` | the partcatalog backend needs the JWT to broker live supplier pricing/stock from Octopart + Altium Parts Provider |
| `servicediscovery/servicediscovery.asmx` `Login` op | `password = *IDSGS*<JWT>`, no HTTP auth | bootstrap — this is the call that mints the short AFSSessionID |

The `ClientID` cookie used on the WebSocket is a per-installation Altium
Designer instance GUID in curly-brace notation (not the user / account /
workspace / session GUID).

### Common headers

- `Accept: application/json` (REST), `text/xml` (SOAP)
- `Content-Type: application/json; charset=utf-8` (REST POST/REPORT bodies)
- `User-Agent`: not set on REST in the capture; the SOAP service uses
  `ADDevelop-ProjectsClient` in a custom header inside the SOAP `<s:Header>`
  rather than the HTTP `User-Agent`.

### Conventions

- **GUIDs**: uppercased with dashes (`B88D01D6-F899-4602-B9BD-FF7B2CDEA84A`).
  In some places (Altium Parts Provider IDs, JWT claims, user GUIDs) lowercase
  GUIDs are used. Treat case as canonical-uppercase for vault objects,
  lowercase for upstream provider ids.
- **Item / Revision model**: every "thing" in the vault has an
  `ItemGUID` (the logical part) and a `RevisionGUID` (a specific version).
  HRIDs (`CMP-004-00028-3`) are human-readable ids: item HRID + revision id.

---

## 2. Search service — `/search/v1.0/searchasync`

```
REPORT https://usw.365.altium.com/search/v1.0/searchasync
Authorization: AFSSessionID <session-id>
Content-Type: application/json; charset=utf-8
Accept: application/json
```

The HTTP method is **`REPORT`** (not `POST`). This is a WebDAV-style verb;
keep that in mind when choosing an HTTP client.

The body is a `SearchRequest` envelope:

```json
{
  "request": {
    "$type": "SearchRequest",
    "Condition": { "$type": "DtoSearchConditionBooleanQuery", "Items": [ ... ] },
    "SortFields":  [ { "$type": "DtoSortSearchField", "Name": "<score>", "Order": 1 } ],
    "ReturnFields": null,
    "Start": 0,
    "Limit": 97,
    "IncludeFacets": false,
    "UseOnlyBestFacets": false,
    "IncludeDebugInfo": false,
    "IgnoreCaseFieldNames": false
  }
}
```

### `$type` discriminator

Every node carries a `$type` string. The values seen in the capture:

| `$type` | Meaning |
|---|---|
| `SearchRequest` | top-level request envelope |
| `DtoSearchConditionBooleanQuery` | a list of subclauses joined by `Occur` flags |
| `DtoSearchConditionBooleanQueryItem` | a single subclause + its `Occur` |
| `DtoSearchConditionStrictQuery` | exact field=value match |
| `DtoSearchConditionWildcardQuery` | wildcard / prefix match (e.g. `r_`) |
| `DtoSearchConditionTerm` | a `{Field, Value}` pair |
| `DtoSortSearchField` | a sort key + order |

A `DtoSearchConditionFuzzyQuery` (and other Lucene query types) is plausible
by analogy but was not seen.

### `Occur` (Lucene `BooleanClause.Occur`)

The numeric values match Lucene's `Occur` enum exactly:

| Value | Lucene name | Meaning |
|---|---|---|
| `0` | `MUST` | clause must match |
| `1` | `SHOULD` | clause contributes to score, OR-style |
| `2` | `MUST_NOT` | clause must not match |

In the captured traffic, every "exclude this lifecycle state" filter uses
`Occur: 2`, hard required filters use `Occur: 0`, and inner `BooleanQuery`
groups (one-of) use `Occur: 1` for their children.

### Sort

```json
"SortFields": [{ "$type": "DtoSortSearchField", "Name": "<score>", "Order": 1 }]
```

- `Name` is either the magic literal `<score>` (rank by relevance) or a
  field name (e.g. `FootprintName1DD420E8DDD8B445E911A0601BB2B6D53`).
- `Order` observed values: `0` (ascending) and `1` (descending).
- `null` or `[]` means "server default".

### Paging

- `Start` — zero-based offset.
- `Limit` — page size. The capture shows the client using
  `Limit: 2147483647` (`int32` max) when fetching the full id list of a
  category, and small caps (e.g. `97`, `75`, `30`) for actual paged results.
  `Limit: 0` is used together with `IncludeFacets: true` to fetch only the
  facet histogram, no rows.

### `ReturnFields`

- `null` → return everything.
- `[]` → return nothing (used with facet-only requests).
- `["FieldA", "FieldB"]` → return only these fields. Used when paginating
  through a category to grab just `IdC623975962814A5FAAD7FA1CD85DA0DB`.

### Facets

- `IncludeFacets: true` adds a `FacetedCounters` array to the response.
- `UseOnlyBestFacets: true` asks the server to apply its own heuristic of
  "the most useful facets for this query" instead of returning every one.
- Combined with `Limit: 0` this gives a pure facet histogram for a query
  (used for the Altium "filter sidebar").

### Field naming

Field names are mangled with a 32-char hex suffix:

```
ContentTypeDD420E8DDD8B445E911A0601BB2B6D53
HRIDC623975962814A5FAAD7FA1CD85DA0DB
ComponentTypeDD420E8DDD8B445E911A0601BB2B6D53
ManufacturerDD420E8DDD8B445E911A0601BB2B6D53
LifeCycleStateGUIDC623975962814A5FAAD7FA1CD85DA0DB
```

Two suffixes are observed in this capture:

- `DD420E8DDD8B445E911A0601BB2B6D53` — content-type / catalog metadata fields
- `C623975962814A5FAAD7FA1CD85DA0DB` — vault-item / revision metadata fields

These are stable internal schema GUIDs. The same field name without the
suffix shows up too (e.g. `AppType`, `Url`, `SubmitDate`, `Language`) for
fields that are universal across content types.

Field names with `_2F` and `_20` are URL-style escapes:
`Case_2FPackage` = `Case/Package`, `Update_20Date` = `Update Date`.

### Filter fields seen in real queries

Listed without their hash suffix for readability.

| Field | Meaning |
|---|---|
| `ContentType` | always `"Component"` in this capture; could also be `"Footprint"`, `"Symbol"`, etc. |
| `Id` | composite id `R_<RevisionGUID>` or `I_<ItemGUID>`. The wildcard `r_` is used to restrict results to revisions only. |
| `LatestRevision` | `"1"` to only return the latest revision of each item. |
| `IsActive` | `"0"` excluded with `Occur: MUST_NOT` filters out soft-deleted items. |
| `LifeCycleStateGUID` | concrete state GUIDs the client wants to *exclude* (16 different `MUST_NOT` clauses appear, one per state). |
| `ComponentType` | category, e.g. `"Diodes\\"`, `"Resistors\\"`, `"LED\\"`, `"Fuses\\"`. The trailing `\\` is part of the value (path-style). |
| `Manufacturer` | lowercased manufacturer name, e.g. `"adafruit industries"`. |

### Response shape

```json
{
  "Documents": [ { "Score": 5.41, "Fields": [ { "Name": "...", "Value": "...", "FieldType": 3 }, ... ] }, ... ],
  "Total": 31,
  "Success": true,
  "FacetedCounters": [ ... ]
}
```

`FacetedCounters` is only present when the request set `IncludeFacets: true`.

- `Score` is a float (Lucene relevance score).
- `Fields[]` is a flat list of `{Name, Value, FieldType}` triples — there is
  no nested object. The same field can appear with both the suffixed and the
  unsuffixed form.
- `FieldType` numeric tags observed:
  - `2` — numeric (e.g. `ReleaseDateNum = 44231.5261226852`, an Excel-style
    serial date).
  - `3` — string.
  Other values exist (date/bool/etc.) but were not seen in this capture.
- `Total` is the total number of matches *before* paging (the server
  truncates `Documents` to `Limit`).

### Top-level fields visible per `Component` document

Suffixes elided. Each row is one entry of `Fields[]`.

| Name | Example value |
|---|---|
| `Id` | `R_60B8A20D-A95B-45AB-81F4-7E53A3F1A90A` |
| `HRID` | `CMP-004-00028-3` |
| `CreatedBy` / `ModifiedBy` | `admin admin` |
| `CreatedAt` / `Updated` | `02/04/2021 12:37:37` (m/d/y or d/m/y — ambiguous in capture) |
| `Update_20Date`, `ReleaseDate`, `ReleaseDateNum`, `SubmitDate` | Excel serial date as float (`44231.5261226852`) |
| `CreatedByGUID` / `UpdatedByGUID` | lowercased user GUIDs |
| `AppType` | `Vault` |
| `Url` | `http://vaultexplorer.$domain/Item/<ItemGUID>/<HRID>` (the literal `$domain` is in the value — clients are expected to substitute) |
| `Language` | `en` |
| `FolderGUID`, `FolderFullPath` | e.g. `Components\Diodes\` |
| `Cat`, `ComponentType` | category (`Diodes`, `Diodes\`) |
| `ItemHRID` | item-level HRID without revision (`CMP-004-00028`) |
| `Description`, `Comment` | catalog description and Altium "comment" field |
| `LifeCycleStateGUID`, `LifeCycle` | e.g. `Draft` + the state GUID |
| `ItemGUID` | the logical part GUID |
| `SourceVaultGUID`, `SourceGUID` | provenance (often empty) |
| `RevisionId` | integer revision counter (e.g. `3`) |
| `AncestorRevisionGUID` | previous revision GUID |
| `NamingSchemeGuid` | the naming-scheme template used for HRIDs |
| `ContentType`, `ContentTypeGUID` | `Component` + its schema GUID |
| `LatestRevision` | `"1"` if this row is the latest revision |
| `Case_2FPackage` | e.g. `SMD/SMT` |
| `Pins` | as string, e.g. `"2"` |
| `Mounting_20Technology` | `SMT`, `THT`, ... |
| `FootprintName1`, `FootprintDescription1`, `FootprintRevisionID1` | first footprint of the component (the trailing `1` implies an array index, with `2`, `3`, ... for additional footprints) |
| `Text` | concatenated full-text-indexed blob — useful as a catch-all search target |
| `ACL` | semicolon-separated list of role/user GUIDs that have access |
| `DynamicData` | server-side cached "live" mfg + MPN, e.g. `Rohm RB520CM-30T2R` |

### Request patterns observed

Four distinct usage patterns appear in the capture, all hitting the same
`searchasync` endpoint:

1. **Faceted page** — `Limit: ~97`, `IncludeFacets: false`, `Sort: <score>`,
   `ReturnFields: null`. The default "show me the first page".
2. **All ids in category** — `Limit: 2147483647`, `Sort: null`,
   `ReturnFields: ["IdC..."]`. Pulls every revision id in a category in one
   shot, used to drive client-side filtering.
3. **Hydrate by id list** — small `Limit` (e.g. `30`), `ReturnFields: null`,
   filter is `ContentType=Component AND ComponentType=<cat> AND
   (Id=<id1> OR Id=<id2> ...)`. Used to fetch full data for the ids the
   client decided to materialize.
4. **Facet-only** — `Limit: 0`, `ReturnFields: []`, `IncludeFacets: true`,
   `UseOnlyBestFacets: true`. Drives the filter sidebar.

### `FacetedCounters` shape

```json
{
  "FacetName": "ManufacturerDD420E8DDD8B445E911A0601BB2B6D53",
  "TotalHitCount": 2,
  "Counters": [
    { "Value": "adafruit industries", "Count": 2 }
  ],
  "SupportRange": false
}
```

`SupportRange: true` would indicate a numeric/range facet (not seen).

The full top-level facet response (from `search_adafruit_led/response.json`)
omits `Documents` entirely when `Limit: 0`:

```json
{ "Documents": [], "FacetedCounters": [ ... ], "Total": 2, "Success": true }
```

---

## 3. Part catalog — `/partcatalog/api/v1.0/PartChoices/...`

Two endpoints, both `POST` with JSON.

### 3.1 `/PartChoices/Get`

```
POST https://usw.365.altium.com/partcatalog/api/v1.0/PartChoices/Get
Authorization: AFSSessionID <session-id>
Content-Type: application/json; charset=utf-8
```

Request:

```json
{
  "Components": [
    {
      "ComponentGuid": "627AFBC7-B051-4F2B-AA53-B7E8F857242B",
      "RevisionGuids": ["8A4DE8FC-9B30-419D-8AFC-64DCF4D2FB35"]
    }
  ],
  "Options": null
}
```

Response (a JSON array — note: top-level array, not wrapped):

```json
[
  {
    "ComponentGuid":         "627AFBC7-B051-4F2B-AA53-B7E8F857242B",
    "ComponentRevisionGuid": null,
    "CreateDate":            "2017-04-21T09:08:23",
    "Description":           "Cermet trimmer potentiometer, 25 turns, 10 kΩ, 0.5 W, THT, on top, 3296W-1-103LF",
    "Guid":                  "DBB7742D-7FF7-4767-812B-DA7A6F9D8D04",
    "ManufacturerName":      "Bourns",
    "Mpn":                   "3296W-1-103LF",
    "PartId":                "957529",
    "PartSourceGuid":        "7A819525-F41E-4ADC-9CD0-9D9FA8B5FCE8",
    "Rank":                  null,
    "Type":                  1,
    "ManagedPartGuid":       "ebba5929-1bbe-434d-9d98-a8a4d1cc3e86"
  }
]
```

This is the *static* part-choice metadata (one record per
manufacturer-part choice attached to the component). `PartSourceGuid`
identifies the upstream parts provider — `7A819525-F41E-4ADC-9CD0-9D9FA8B5FCE8`
is the "Altium Parts Provider". `Type: 1` is `ManufacturerPart`; other
values are likely defined but were not seen.

### 3.2 `/PartChoices/ComponentDynamicData/Get`

The "live" version: pulls real-time pricing, stock, parameters, lifecycle,
documents, etc. from the upstream parts providers.

```
POST https://usw.365.altium.com/partcatalog/api/v1.0/PartChoices/ComponentDynamicData/Get
Authorization: AFSSessionID <session-id>
Content-Type: application/json; charset=utf-8
```

Request:

```json
{
  "Components": [
    {
      "ComponentGuid":  "B88D01D6-F899-4602-B9BD-FF7B2CDEA84A",
      "RevisionGuids":  ["60B8A20D-A95B-45AB-81F4-7E53A3F1A90A"]
    }
  ],
  "Options": [
    "IncludeManufacturerParts",
    "IncludeManufactureDocuments",
    "IncludeManufacturerAliases",
    "IncludeSupplierParts",
    "IncludeSupplierParameters",
    "IncludeSupplierStocks",
    "IncludeSupplierPrices",
    "IncludeAllProvidersLifecycles",
    "IncludeManufactureParameters",
    "LiveSessionId=<JWT access token>"
  ]
}
```

Notes on `Options`:

- It is a `List<string>`. Most entries are flag names (presence = true).
- `LiveSessionId=<jwt>` is encoded as a `key=value` string (not a separate
  field), and the value is the user's Altium auth JWT (see § 1).
- Spelling is from Altium and includes the typos `IncludeManufactureDocuments`
  and `IncludeManufactureParameters` (no `r`). They are *not* the same as
  `IncludeManufacturerParts` — preserve the spelling exactly.

Response is a list keyed by the (`ComponentGuid`, `ComponentRevisionGuid`)
pair the client asked for:

```json
[
  {
    "ComponentGuid":         "B88D01D6-F899-4602-B9BD-FF7B2CDEA84A",
    "ComponentRevisionGuid": "60B8A20D-A95B-45AB-81F4-7E53A3F1A90A",
    "ManufacturerParts":     [ ManufacturerPart, ... ]
  }
]
```

#### `ManufacturerPart`

```json
{
  "PartChoiceGuid":      "BE860D59-8BDA-47A0-A70D-A48831BA49E1",
  "Type":                 1,
  "ManagedPartGuid":     "bb077f9a-5708-4bbf-bb98-53ffccd75c20",
  "PartSourceGuid":      "7A819525-F41E-4ADC-9CD0-9D9FA8B5FCE8",
  "PartSourceName":      "Altium Parts Provider",
  "PartSourceType":       1,
  "PartId":              "22034112",
  "ManufacturerName":    "ROHM",
  "ManufacturerAliases": ["Rohm Semiconductor", "ROHM CO LTD", "..."],
  "Mpn":                 "RB520CM-30T2R",
  "Description":         "Rectifier Diode, Schottky, 1 Phase, 1 Element, 0.1A, 30V V(RRM), Silicon",
  "Category":            "Schottky Diodes",
  "ProductUrl":          "https://octopart.com/part/rohm/RB520CM-30T2R",
  "ProductPhotoUrl":     "https://sigma.octopart.com:443/186006506/image/ROHM-RB520CM-30T2R.jpg?src-supplier=TME",
  "SupportingDocuments": ["https://datasheet.octopart.com:443/RB520CM-30T2R-...pdf", "..."],
  "Parameters":          [ Parameter, ... ],
  "SupplierParts":       [ SupplierPart, ... ],
  "CustomSupplierParts": null,
  "LifeCycleStatusId":   2,
  "LifeCycleStatusName": "Volume Production",
  "Rank":                null,
  "Packaging":           null,
  "ExtraParameters":     null,
  "Metrics":             null,
  "IsRoHSCompliant":     true,
  "IsReachCompliant":    null,
  "Lifecycles":          [ { "ProviderId": "altium", "LifecycleId": "Volume Production" } ]
}
```

`Type` and `PartSourceType` are small integers; only `1` was seen. Likely an
enum; meaning of other values not determined.

#### `Parameter`

```json
{ "Name": "Average Rectified Current", "Value": "100", "ParamUnit": "mA" }
```

`Value` is always a string (even when numeric); units are not normalized.

#### `SupplierPart`

```json
{
  "SupplierName":        "DigiKey",
  "Sku":                 "RB520CM-30T2RCT-ND",
  "Currency":            "USD",
  "SupplierUrl":         "https://octopart.com/opatz8j6/a1?t=...",
  "LastUpdatedAt":       "2026-04-08T19:28:54+00:00",
  "LastUpdated":         "Updated today",
  "PriceItems":          [ PriceItem, ... ],
  "StockItems":          [ StockItem, ... ],
  "SupplierAliases":     null,
  "AvailableQuantity":   8313,
  "Packaging":           ["Cut Tape"],
  "CustomPricingStatus": "Unavailable",
  "Metrics":             null,
  "OfferId":             "ActvR04IJx8ZcCyJ9uY6dqx/"
}
```

`LastUpdated` is a human-readable string ("Updated today") generated by the
server — prefer `LastUpdatedAt` for parsing.

#### `PriceItem`

```json
{ "BreakQuantity": 1, "Price": 0.35, "Location": "US", "Currency": "USD" }
```

#### `StockItem`

```json
{ "LocationName": "US", "Quantity": 8313.0 }
```

`Quantity` is a float, which suggests it could carry partial-reel values; in
the capture all observed values are integers.

---

## 4. Component health — `/componenthealth/api/v1.0/components/{ItemGUID}/areas`

```
GET https://usw.365.altium.com/componenthealth/api/v1.0/components/{ItemGUID}/areas
Authorization: AFSSessionID <session-id>
Accept: application/json
```

Path parameter is the **item** GUID (not the revision GUID). Returns
"health area" findings (lifecycle warnings, missing models, etc.) for the
component.

Empty response observed throughout the capture:

```json
{ "areas": [] }
```

The shape of a populated `area` is not in this capture.

The capture issues this call once per `ItemGUID` of every component the
user is hydrating live data for — i.e. it is paired with each
`ComponentDynamicData/Get` call.

---

## 5. Comments — `/comments/api/v1/{ProjectGUID}/annotations`

```
GET https://usw.365.altium.com/comments/api/v1/{ProjectGUID}/annotations
Authorization: AFSSessionID <session-id>
Accept: application/json
```

Path parameter is a **project** GUID (matches the GUID used in the
ProjectsService SOAP call below). Returns project-scoped annotations
(graphical comments / sticky notes attached to PCB / Schematic documents).

Empty response observed:

```json
{ "isSuccess": true, "messages": [], "annotations": [] }
```

The `isSuccess` / `messages` / `annotations` envelope is a recurring shape
in Altium's newer JSON APIs; expect `messages` to carry server-side
warning/error strings on failures.

---

## 6. Projects service (SOAP) — `/projects/ProjectsService.asmx`

```
POST https://usw.365.altium.com/projects/ProjectsService.asmx
Content-Type: text/xml; charset=utf-8
SOAPAction: "http://tempuri.org/<OperationName>"
```

This is a classic ASMX web service — note the `tempuri.org` namespace,
which is Microsoft's default placeholder that Altium never bothered to
rename. **No `Authorization` header is sent**; the session id travels inside
the SOAP body instead.

A custom SOAP header carries client metadata:

```xml
<s:Header>
  <User-Agent>ADDevelop-ProjectsClient</User-Agent>
  <X-Request-ID/>
  <X-Request-Depth>1</X-Request-Depth>
</s:Header>
```

(`X-Request-ID` is empty; `X-Request-Depth: 1` looks like an internal
recursion depth for chained service calls.)

### Operation: `HasPermissionOnProject`

`SOAPAction: "http://tempuri.org/HasPermissionOnProject"`

Request body:

```xml
<HasPermissionOnProject
    xmlns="http://tempuri.org/"
    xmlns:i="http://www.w3.org/2001/XMLSchema-instance">
  <sessionId>8944038D-...-88776570b379</sessionId>
  <level>1</level>
  <projectGuid>63580470-DF83-43BA-8824-9DCB1C1B45F8</projectGuid>
  <checkAccessDownload i:nil="true"/>
</HasPermissionOnProject>
```

Response:

```xml
<HasPermissionOnProjectResponse xmlns="http://tempuri.org/">
  <HasPermissionOnProjectResult>true</HasPermissionOnProjectResult>
</HasPermissionOnProjectResponse>
```

Notes:

- `level` — integer permission level. Only `1` was observed; the values are
  likely an enum (`None` / `Read` / `Write` / `Admin` / ...). Not determined.
- `checkAccessDownload` — nullable bool. The client sends `i:nil="true"`
  (i.e. "don't care").

### Operation: `FindProjects`  *(the project lister)*

Paginated list of projects the caller can see. This is the "open managed
project" picker in Altium Designer.

```xml
<FindProjects xmlns="http://tempuri.org/">
  <sessionId>{short AFSSessionID}</sessionId>
  <paramList>
    <AccessType>All</AccessType>              <!-- also: Own / Shared / etc. -->
    <OwnerType>Any</OwnerType>                <!-- also: Me / User / Group -->
    <OrderByAsc>false</OrderByAsc>
    <StartIndex>0</StartIndex>
    <CountPerPage>50</CountPerPage>
    <IncludeAccessRights>true</IncludeAccessRights>
    <IncludeVariantParameters>false</IncludeVariantParameters>
  </paramList>
  <sendRealHRID>true</sendRealHRID>
</FindProjects>
```

Response has a `<FindProjectsResult>` with one `<ProjectExt>` per project:

| Field | Meaning |
|---|---|
| `GUID` | project item GUID (this is what you pass everywhere else) |
| `HRID` | numeric HRID, e.g. `PRJ-00034` (or, when `sendRealHRID=true`, the display name like `PCB_Project`) |
| `NAME` | display name |
| `DESCRIPTION` | free text |
| `PROJECTTYPE` | `PcbProject` (other values presumably exist for other project kinds) |
| `CREATEDAT` / `LASTMODIFIEDAT` | ISO timestamps |
| `CREATEDBYGUID` / `LASTMODIFIEDBYGUID` | user GUIDs |
| `ACCESSTYPE` | numeric access flag |
| `ISACTIVE` | `1` or `0` |
| `SOURCEPROJECTGUID` | for forked/copied projects |
| **`REPOSITORYGUID`** | **git repo backend store GUID** — shared across all projects in the workspace in this capture (`05bf7b90-fd69-4dee-ae39-3c035e2dc43c`), so it identifies the *backend* git store, not the individual repo |
| **`REPOSITORYPATH`** | **path segment used in the git URL** — equals the project GUID in every observed case, so the git URL is `https://<GITREST>/git/{REPOSITORYPATH}.git/` |
| `Parameters` | array of `PJS_PROJECTPARAMETER` `{PARAMETERNAME, PARAMETERVALUE}` with per-project metadata (dielectric, layer stack, doc numbers, viewer revision, ...) |

The `Parameters` list carries useful nuggets like
`Viewer Latest Processing Revision` = the git commit SHA of the last
server-rendered preview — handy if you want to know whether your local
clone matches what the web viewer is showing.

### Operation: `GetProjectByGuid`

Single-project fetch. Request is `{sessionId, projectGuid}`; response shape
is identical to one `ProjectExt` record from `FindProjects`.

### Operation: `GetProjectsExtByGuids`

Batch version of `GetProjectByGuid`. Takes a list of GUIDs, returns the
same `ProjectExt` records.

### Operation: `GetProjectParameters`

Returns just the `Parameters` array for a project without the other
metadata. Used by the UI when it only needs to refresh parameter values.
Request shape: `{sessionId, projectGuid}`.

### Operation: `GetWIPPreviewFile`  *(project thumbnail)*

```xml
<GetWIPPreviewFile xmlns="http://tempuri.org/">
  <sessionId>...</sessionId>
  <projectId>AE4E584E-6BD2-46E2-92E2-DDE72D2A57C0</projectId>
  <revisionId/>
</GetWIPPreviewFile>
```

Response:

```xml
<GetWIPPreviewFileResult>
  <FileName>Preview.png</FileName>
  <Data>{base64 PNG}</Data>
</GetWIPPreviewFileResult>
```

Returns the server-rendered board preview PNG for a project (the
thumbnail you see in the A365 web UI). When `<revisionId/>` is empty,
the server returns the WIP ("work in progress") preview — the latest
unreleased state. Passing a specific `revisionId` presumably returns
the preview at that commit.

---

## 7. Tunneling

There are 15 `CONNECT usw.365.altium.com:443` entries in the HAR. These are
just the HTTPS tunnel-establishment requests from the proxy capture — they
are not application-level Altium endpoints and can be ignored when modeling
the API.

---

## 8. Endpoint summary

### 8.1 Bootstrap / identity (§ 14)

| Method   | Host                         | Path / operation                                                 | Purpose                                          |
|----------|------------------------------|------------------------------------------------------------------|--------------------------------------------------|
| `GET`    | `auth.altium.com`            | `/api/ClientScopes?clientId={id}`                                | Fetch client scope list                          |
| `GET`    | `auth.altium.com`            | `/connect/authorize?...&code_challenge=...&state=...`            | OAuth2 authorization code flow with PKCE (§ 14.1) |
| `GET`    | `auth.altium.com`            | `/signin?ReturnUrl=/connect/authorize/callback?authzId=...`      | Login form landing (§ 14.1.6)                    |
| `POST`   | `auth.altium.com`            | `/api/userContext/current`                                       | Advertised auth methods (password, webAuth, google, facebook) — § 14.1.6 step C |
| `POST`   | `auth.altium.com`            | `/api/userContext/authenticationMethods`                         | Same shape, email-scoped (§ 14.1.6 step D)       |
| `POST`   | `auth.altium.com`            | `/api/account/signIn`  body `{userName,password,persistent,returnUrl,visitorId}` | **Password sign-in** — sets cookies incl. `ALU_SID_2` (§ 14.1.6 step E) |
| `GET`    | `auth.altium.com`            | `/connect/authorize/callback?authzId=...`                        | Post-signin redirect → `/api/AuthComplete?code=…` (§ 14.1.6 step F) |
| `POST`   | `auth.altium.com`            | `/connect/token`  `grant_type=authorization_code`                | Exchange code + code_verifier for JWT            |
| `GET`    | `auth.altium.com`            | `/connect/endsession?soft=1&closeTab=1`                          | Logout start — 302 to `/oidc-server/signout` (§ 14.1.7) |
| `POST`   | `auth.altium.com`            | `/api/account/oidcServerSignOut`  body `{"logoutId":"..."}`      | Clears IdSrv4 server session + `idsrv` cookies (§ 14.1.7) |
| `POST`   | `actionwait.altium.com`      | `/await`  body `{"token":"<state UUID>"}`                         | Out-of-band pickup of OAuth code (§ 14.1.4)      |
| `POST`   | `auth.365-gov.altium.com`    | `/connect/token`  `grant_type=...token-exchange`                 | RFC 8693 token exchange for gov cloud (§ 14.1.5) |
| `POST`   | `portal365.altium.com`       | `/?cls=soap`  ops: `LoginBySession`, `LeaseOnDemandLicense`, `GetPRT_GlobalServiceUrl`, `GetAccountDetails`, `GetPRT_ContactDetails`, `GetPRT_Settings`, `GetAllowedA365Features`, `GetLIC_AvailableLicenses_ForContactAD16Plus`, `ValidateLicenseLease` | Validate JWT, fetch account / license / features |
| `POST`   | `vault.api.altium.com`       | `/?cls=soap`  op: `GetALU_VaultRecord`                            | Central vault capability flags                   |
| `POST`   | `workspaces.altium.com`      | `/workspaceexternalservices/WorkspaceHelperService.asmx`  ops: `GetUserWorkspaces`, `CheckADWAccountLicense` | **List the user's workspaces**                   |
| `POST`   | `<workspace>.365.altium.com` | `/servicediscovery/servicediscovery.asmx`  op: `http://altium.com/Login` | **Mint short `AFSSessionID` + fetch 65-endpoint directory** |

### 8.2 Workspace vault (§ 11 — all `POST /vault/?cls=soap` on the workspace host)

| SOAPAction                               | Purpose                                                  | §     |
|------------------------------------------|----------------------------------------------------------|-------|
| `GetALU_VaultRecord`                     | Vault metadata + feature flags                           | 11.9  |
| `GetALU_Folders`                         | Full folder tree                                         | 11.6  |
| `GetALU_LifeCycleStates`                 | LifeCycleStateGUID → human state name                    | 11.7  |
| `GetALU_Tags`                            | Tag taxonomy within a family                             | 11.8  |
| `GetALU_Items`                           | **Logical parts enumeration** (filter by `ContentTypeGUID`) | 11.5 |
| `GetALU_ItemRevisions`                   | Full revision metadata + parameters                      | 11.2  |
| `GetALU_ItemRevisionLinks`               | Parent → child links (`PCBLIB`, `SCHLIB`, ...)           | 11.1  |
| `GetALU_ItemRevisionDownloadURLs`        | **Mint S3 pre-signed URLs**                              | 11.3  |
| `CheckActionPermissions`                 | What can this session do on an entity type               | 11.10 |
| `GET /vault/CheckRevision`               | Readiness/version ping                                   | 11.11 |

### 8.3 Regional shared services (`usw.365.altium.com`)

| Method   | Path                                                         | Purpose                                                       | §      |
|----------|--------------------------------------------------------------|---------------------------------------------------------------|--------|
| `REPORT` | `/search/v1.0/searchasync`                                   | Lucene-style component / vault search                         | 2      |
| `POST`   | `/search/v1.0/adsearch/querycomponenttypefacets`             | **All component-type facets in one call** (category enumeration) | 15 |
| `POST`   | `/partcatalog/api/v1.0/PartChoices/Get`                      | Static manufacturer part choices                              | 3.1    |
| `POST`   | `/partcatalog/api/v1.0/PartChoices/ComponentDynamicData/Get` | Live mfg + supplier data                                      | 3.2    |
| `GET`    | `/partcatalog/api/v1.0/{Capabilities,PartLifecycles,Permissions,PartExtraData/Providers,PartSources,PartSources/options,PartSources/suppliers,PartSources/{guid}/custom-pricing-providers}` | Bootstrap dictionaries | 14.7 |
| `GET`    | `/componenthealth/api/v1.0/components/{ItemGUID}/areas`      | Health/lint findings                                          | 4      |
| `GET`    | `/comments/api/v1/{ProjectGUID}/annotations`                 | Project annotations                                           | 5      |
| `GET`    | `/comments/api/v1/{ProjectGUID}/source`                      | Project comment source                                        | 14.7   |
| `GET`    | `/comments/v1.0/{ProjectGUID}/userSettings`                  | User comment settings                                         | 14.7   |
| `POST`   | `/projects/ProjectsService.asmx`  ops: `HasPermissionOnProject`, `FindProjects`, `GetProjectByGuid`, `GetProjectsExtByGuids`, `GetProjectParameters`, `GetWIPPreviewFile` | Project directory + metadata + thumbnails (§ 6) | 6    |
| `GET`    | `/dictionaries/api/v1.0/{dictionaries,operations}`           | Generic dictionary service                                    | 14.7   |
| `POST`   | `/settings/SettingsService.svc`  op: `GetSetting`            | Per-user settings (SOAP)                                      | 14.7   |

### 8.4 Per-workspace services (`<workspace>.365.altium.com`)

| Method   | Path                                                         | Purpose                                          | § |
|----------|--------------------------------------------------------------|--------------------------------------------------|---|
| `GET`/`POST` | `/servicediscovery/servicediscovery.asmx`                | Health ping (GET) + Login (POST)                 | 12 |
| `POST`   | `/vault/?cls=soap`                                           | Vault SOAP (see § 8.2)                           | 11 |
| `GET`    | `/vault/CheckRevision`                                       | Readiness probe                                  | 11.11 |
| `POST`   | `/ids/?cls=soap`  ops: `GetSessionInfo`, `QueryUsersDetails` | IDS: session introspection + user directory     | 16.6 |
| `POST`   | `/SearchTemplatesService/SearchTemplatesService.asmx`        | Saved search templates                           | 11.11 |
| `POST`   | `/vcs/vcswebservice.asmx`                                    | Version control service                         | 11.11 |
| `POST`   | `/websocket/WebService.asmx`  op: `GetChannelUrl`            | WebSocket handshake                              | 13.1  |
| `GET`    | `/websocket/ws.ashx`                                         | WebSocket upgrade                                | 13.2  |

(Plus 16 other `ServiceKind` endpoints on the workspace host and 40+ on
the regional host that are advertised in the `Login` response but not
exercised in the capture — see § 14.6 for the full list.)

### 8.5 Binary assets

| Method | Host                                       | Path                                                 | Purpose              | §  |
|--------|--------------------------------------------|------------------------------------------------------|----------------------|----|
| `GET`  | `ccv-<region>.s3.<region>.amazonaws.com`   | `/{workspaceGuid}/ItemRevisions/{HRID}/{HRID}.zip`   | Pre-signed S3 bundle | 10 |

### 8.6 Hosted git repositories

| Method | Host                                       | Path                                                         | Purpose                                            | §  |
|--------|--------------------------------------------|--------------------------------------------------------------|----------------------------------------------------|----|
| `GET`  | `afs-vcs-uw1.365.altium.com`               | `/git/{REPOSITORYPATH}.git/info/refs?service=git-upload-pack` | Git ref discovery (smart-HTTP v1)                  | 16.3 |
| `POST` | `afs-vcs-uw1.365.altium.com`               | `/git/{REPOSITORYPATH}.git/git-upload-pack`                  | Git clone / fetch (pack negotiation)               | 16.3 |

Auth on both: **HTTP Basic** with `<user email>:<short AFSSessionID>` —
see § 16.1.

---

## 9. Open questions / not-in-capture

### 9.1 Closed by the full-flow capture

The following were previously open and are now answered (left here as
signposts to where):

- **How to mint an `AFSSessionID` from scratch** → the servicediscovery
  `Login` call (§ 12.2 / § 14.5), after a portal365 `LoginBySession` chain
  (§ 14.2).
- **How to discover workspaces** → `workspaces.altium.com/GetUserWorkspaces`
  (§ 14.4).
- **How to get from workspace slug to workspace GUID** → same call
  (`hostingurl` + `spacesubscriptionguid` in each `UserWorkspaceInfo`).
- **How to resolve `LifeCycleStateGUID` to a human name** →
  `GetALU_LifeCycleStates` (§ 11.7).
- **How to resolve `FolderGUID` to a folder path** → `GetALU_Folders` (§ 11.6).
- **How to enumerate all components authoritatively** → either
  `GetALU_Items` with `ContentTypeGUID` filter (§ 11.5) *or*
  `adsearch/querycomponenttypefacets` (§ 15) + `searchasync`.
- **How to discover the full category taxonomy** →
  `adsearch/querycomponenttypefacets` (§ 15).
- **The wider vault SOAP surface** → `GetALU_Items`, `GetALU_Folders`,
  `GetALU_LifeCycleStates`, `GetALU_Tags`, `GetALU_VaultRecord`,
  `CheckActionPermissions` are all documented in §§ 11.5–11.10. The
  complete 65-endpoint service directory is in § 14.6.
- **How to list managed projects** → `ProjectsService.asmx` op
  `FindProjects` (§ 6).
- **How to clone a managed project as a real git repo** →
  `https://afs-vcs-uw1.365.altium.com/git/{REPOSITORYPATH}.git` with
  HTTP Basic `<email>:<short AFSSessionID>` (§ 16). The `REPOSITORYPATH`
  comes from the `FindProjects` response.
- **How to introspect the current session without re-logging in** →
  `GetSessionInfo` on the workspace IDS SOAP endpoint (§ 16.6).
- **How to resolve a user GUID to a name / email** → `QueryUsersDetails`
  on the workspace IDS SOAP endpoint (§ 16.6).
- **The OIDC flow that mints the JWT** → OAuth 2.0 authorization code
  with PKCE against `auth.altium.com/connect/authorize` +
  `/connect/token`, with the auth code delivered out-of-band through
  `actionwait.altium.com/await` (§ 14.1). The desktop client never
  needs a localhost listener or a custom URL scheme.
- **How a desktop client re-authenticates silently** → `ALU_SID_2` /
  `idsrv.session` cookies on `.altium.com` carry the previous session
  so `/connect/authorize` skips the login form (§ 14.1.3).
- **First-time username+password login** →
  `/connect/authorize` (no cookies) redirects to `/signin?ReturnUrl=...`
  which loads a SPA that calls `/api/userContext/current` then
  `/api/account/signIn` with plaintext `{userName, password,
  persistent, returnUrl}`, setting the session cookies on response
  (§ 14.1.6).
- **How the SPA knows which auth methods to offer** →
  `POST /api/userContext/{current, authenticationMethods}` returns a
  directory with `password`, `webAuth`, `google`, `facebook` entries,
  including fully-assembled OAuth URLs for federated IdPs (§ 14.1.6).
- **How to log out** → `GET /connect/endsession?soft=1&closeTab=1`
  →  `GET /oidc-server/signout?logoutId=...` →  `POST /api/account/oidcServerSignOut`
  with `{"logoutId": "..."}` (§ 14.1.7). Note: this only clears the
  `idsrv*` cookies; `ALU_SID_2` and friends persist and must be
  expired client-side.
- **How authorize-request parameters survive the sign-in form** → the
  server stashes them behind an opaque `authzId = <64-hex>_<ts>`
  handle so the client cannot tamper with `redirect_uri` / `scope` /
  `code_challenge` between `/connect/authorize` and the callback
  (§ 14.1.8).
- **Token exchange for FedRAMP / gov workspaces** → RFC 8693 token
  exchange against `auth.365-gov.altium.com/connect/token` with
  `grant_type=urn:ietf:params:oauth:grant-type:token-exchange` (§ 14.1.5).
- **What `ActionWait` is and how the desktop picks up an OAuth code
  from a browser** → `POST actionwait.altium.com/await` with
  `{"token": "<state UUID>"}` returns the stashed
  `{code, state, session_state, iss}` (§ 14.1.4).

### 9.2 Still open

- **Federated IdP sign-in flows** (Google, Facebook, Microsoft, SAML).
  `login.har` captured a password sign-in only; the `returnUrl`s for
  Google and Facebook are advertised in `/api/userContext/current` but
  the actual external bounce + `/oauth/callback/{google,facebook}`
  landing on the Altium side is not exercised. `idp: local` in every
  captured JWT confirms native auth; federated JWTs would have
  different `idp` values.
- **WebAuthn / FIDO2 sign-in.** `webAuth: {enabled: true}` is
  advertised but the passkey / security-key ceremony endpoints are
  not exercised.
- **The delivery side of `actionwait.altium.com/await`.** See § 14.1.4
  — we see the pickup side only.
- **`MODEL` / `DATASHEET` link types and content-type GUIDs.** This
  workspace happens to have no 3D models or datasheets attached to any
  component, so no `MOD-*` or `DSH-*` HRIDs appear anywhere. The
  multi-footprint case is now seen (`PCBLIB 1`, `PCBLIB 2` in link
  responses), but 3D/datasheet link types are still unverified. A
  capture of a single component that has a 3D body linked would close
  this.
- **`<InputCursor>` paging format.** Every bulk vault op supports it,
  none of the captures ever paginates. The cursor-format is unknown —
  a capture with a query that exceeds the server page cap would show it.
- **Other `EntityType` values for `CheckActionPermissions`.** Only
  `AluComponent` was exercised.
- **The 27+ `ServiceKind` endpoints advertised in `Login` but not
  called in any capture** — `MANAGEDLIBRARIESSERVICE`, `EIS`, `DDS`,
  `BMS`, `MCADCS`, `VIEWER`, `CH`, `APPLICATIONS`, `PROJECTSREST`,
  `AuthService`, `PLATFORMAPI`, `PROJECTCOMPARESERVICE`,
  `PUSH`, `IDSCloud`, `TC2`, `DSS`, `ISR`, `FeatureChecking`,
  `TASKS`, `EDS`, `INUSE`, `SearchTemplatesService`, `VCSSERVICE`,
  `Sharing`, `Invitation`, `CommentsCloud`, `PushCloud`,
  `SCHEDULER`, `LIBRARY.MODELMETADATA.WORKER`, `Library.Parts.Api`,
  `Library.Components.Api`, `InsightsRestApi`, `MANAGEDFLOWS`, `PLMSYNC`,
  `PROJECTHISTORYSERVICE`, `LWTASKS`, `COMPARISONSERVICE`,
  `EXPLORERSERVICE`, `Components`, `CollaborationService`, `PARTCATALOG`
  (legacy SOAP), `SEARCH` (legacy SOAP), `NOTIFICATIONSSERVICE`,
  `BOMSERVICE(_AD)`, `REQUIREMENTSSERVICE`, `DICTIONARIES` (only
  `v1.0/dictionaries` and `v1.0/operations` exercised). Note: `IDS`,
  `GITREST` and `ActionWait` are now partially documented
  (§§ 16.6, 16 and 14.1.4).
- **Git push (`git-receive-pack`).** Only clone/fetch is in the capture.
  Whether push needs an additional permission check beyond the short-
  session Basic auth, whether `git-receive-pack` is even enabled on the
  smart-HTTP endpoint (or whether writes go through a different path
  like `/git/api`), and how ACLs are enforced per-branch are all
  unknown.
- **The `GITREST` JSON REST endpoint** at
  `https://afs-vcs-uw1.365.altium.com/git/api`. The smart-HTTP git
  protocol is now known (§ 16.3) but this sibling REST API is not
  exercised. It presumably exposes repo metadata, branch listing, PR
  review, etc.
- **Non-clone git protocol features.** No v2 smart-HTTP, no partial
  clone / filter, no shallow / depth, and no LFS activity was captured.
  v1 only, full clone only.
- **Full enum of `Type` / `PartSourceType` / `LifeCycleStatusId`** in
  the partcatalog response.
- **Other `Occur` values / `FieldType` codes / fuzzy-query / range-query
  variants** on `searchasync`.
- **Non-empty shapes** for `componenthealth/.../areas` and
  `comments/.../annotations`.
- **The actual WebSocket binary protocol** on `/websocket/ws.ashx`
  after the upgrade.
- **Regional routing logic.** `locationid: 4` corresponds to
  `US West (Oregon)` in `GetUserWorkspaces`. The mapping from `locationid`
  to regional host (`usw.365.altium.com`, `ccv-us-west-2.s3.us-west-2`)
  is implicit; there is no documented location-id → host table.
- **Write side of the vault SOAP.** None of the captures exercise any
  `Add*` / `Update*` / `Delete*` / `Release*` operations. ("Insert into
  schematic" in the capture that produced this README is a *read-only*
  place-a-part flow, which `GetALU_ItemRevisions` +
  `GetALU_ItemRevisionDownloadURLs` + S3 download already cover.) A
  separate capture of "create and release a new component" would be
  needed to document the write path.

---

## 10. S3 asset host — `ccv-us-west-2.s3.us-west-2.amazonaws.com`

Reverse-engineered from
`ccv-us-west-2.s3.us-west-2.amazonaws.com_04_08_2026_16_18_25.har`.

The actual binary library content (footprints, symbols, presumably also 3D
models and datasheets) is **not** served by `*.altium.com` at all — Altium
issues short-lived AWS S3 pre-signed URLs and the client `GET`s the bundle
directly from S3.

### Host

- `ccv-us-west-2.s3.us-west-2.amazonaws.com` — region-specific S3 bucket.
  `ccv` is presumably "Content Vault"; the suffix matches the AWS region of
  the corresponding `usw.365.altium.com` instance. Other regions (e.g.
  `ccv-eu-central-1`) presumably exist.
- The bucket is accessed in the *path-style* virtual host
  (`{bucket}.s3.{region}.amazonaws.com`), not the SigV4 virtual-host-only
  form, so the bucket name `ccv-us-west-2` is visible in the host header.

### URL pattern

```
GET https://ccv-us-west-2.s3.us-west-2.amazonaws.com
    /{workspaceGuid}/ItemRevisions/{HRID}/{HRID}.zip
    ?X-Amz-Algorithm=AWS4-HMAC-SHA256
    &X-Amz-Credential=ASIA.../20260408/us-west-2/s3/aws4_request
    &X-Amz-Date=20260408T231536Z
    &X-Amz-Expires=7200
    &X-Amz-SignedHeaders=host
    &X-Amz-Security-Token=<base64 STS session token>
    &X-Amz-Signature=<hex sig>
```

- `{workspaceGuid}` is the lowercased workspace GUID — *the same value* that
  appears as the second half of the `AFSSessionID` (see § 1). All 10
  observed objects share the same workspace prefix.
- `{HRID}` is the item-revision HRID, e.g. `PCC-00470-1` (PCB Component
  revision 1) or `SYM-00163-1` (Symbol revision 1). The HRID appears twice
  in the path: as the directory name and again as the file basename.
- The signing credential is `ASIA...` (STS temporary credentials, not
  `AKIA...` long-lived), with `X-Amz-Expires=7200` (2 h validity).
- `X-Amz-SignedHeaders=host` means only the `Host` header is part of the
  signature — clients can add or omit other headers freely. The captured
  request, in fact, sends *only* `Host: ccv-us-west-2.s3.us-west-2.amazonaws.com`
  with no `Authorization`, no `User-Agent`, no `Accept`.

### Response

Standard S3 response:

```
HTTP/1.1 200 OK
Content-Type: application/zip
Content-Length: 41571
Last-Modified: Tue, 17 Mar 2026 01:57:32 GMT
ETag: "31ef02095a0ad2cad338348bd7d21832"
Accept-Ranges: bytes
x-amz-version-id: ggMsxaXHR6wubhUreB15mlAcqwjGfH0r
x-amz-replication-status: COMPLETED
x-amz-server-side-encryption: AES256
x-amz-id-2: ...
x-amz-request-id: ...
Server: AmazonS3
```

Notable: `Accept-Ranges: bytes` (range GETs allowed),
`x-amz-version-id` exposed (versioned bucket), and `x-amz-replication-status:
COMPLETED` (cross-region replication is enabled — so a write to one regional
bucket eventually appears in the others).

### HRID prefixes seen

The 10 zips in the capture cover two prefixes:

| Prefix  | Meaning           | Bundle contains                           | Count seen |
|---------|-------------------|-------------------------------------------|------------|
| `PCC-`  | PCB Component     | `Released/<name>.PcbLib` (footprint lib)  | 6          |
| `SYM-`  | Schematic Symbol  | `Released/<name>.SchLib` (symbol lib)     | 4          |

Other Altium HRID prefixes (`CMP-`, `MOD-` for 3D models, `DSH-` for
datasheets, etc.) are presumably served the same way but were not in this
capture.

### Bundle layout (zip contents)

Each `.zip` is a small structured archive with two top-level "directories":

```
Released/
  <library-file-name>.PcbLib    # for PCC-*  -- the actual binary library
  <library-file-name>.SchLib    # for SYM-*

Images/
  ItemRevisions/
    <item-revision-GUID>/
      2D-F.png                  # full-size preview   (PCC: ~16-21 KB)
      2D-L.png                  # large preview       (= 2D-F in observed bundles)
      2D-M.png                  # medium preview      (~5-7 KB)
      2D-S.png                  # small preview       (~1-1.5 KB)
```

Notes:

- The `Images/ItemRevisions/<guid>/` path uses the **item-revision GUID**
  (e.g. `F4F48169-E839-485C-BD54-DD67CC19C971`), *not* the HRID. So a
  non-`searchasync` lookup is needed to map between HRID and revision GUID
  if you start from one and want the other.
- For symbols (`SYM-*`), the preview filenames are `1-F.png` / `1-L.png` /
  `1-M.png` / `1-S.png` instead of the `2D-*` family — the `2D-` prefix is
  specific to the footprint / 3D-view rendering pipeline.
- The `Released/` directory only contains the latest "released" library
  file. Drafts presumably live elsewhere or under a different folder name.
- All file entries are deflate-compressed (zip method 8).

### Implications for clients

- The pre-signed URL is brokered by the workspace vault SOAP service:
  `GetALU_ItemRevisionDownloadURLs` (§ 11.3). It takes a list of item-
  revision GUIDs and returns one S3 URL per item.
- No Altium auth headers go to S3 — the signature *is* the auth.
- To pull by component, you need to chain three vault SOAP calls:
  `GetALU_ItemRevisionLinks` → `GetALU_ItemRevisions` →
  `GetALU_ItemRevisionDownloadURLs`. See § 11.4 for the full workflow.
- The 2-hour TTL on the pre-signed URL means caching the URL itself is OK
  for short-lived workflows, but a long-lived tool should always ask the
  broker fresh.

### Tunnel entries

This HAR has no `CONNECT` entries (the capture was taken downstream of the
TLS terminator, so only the application-level S3 GETs are visible). All 10
entries are real `GET`s — there are some duplicates (e.g. `PCC-00470-1.zip`
and `SYM-00163-1.zip` each appear twice), suggesting either a client retry
or a re-fetch on cache miss.

---

## 11. Workspace vault SOAP — `<workspace>.365.altium.com/vault/?cls=soap`

Reverse-engineered from
`atopile-2.365.altium.com_04_08_2026_16_34_38.har`.

This is the **per-workspace** SOAP vault service. The host is the
workspace-slug subdomain (`atopile-2.365.altium.com` here); the workspace
GUID inside the responses (uppercased `ECC90F5C-559E-4318-9CE1-88776570B379`)
matches the lowercased workspace GUID embedded in the `AFSSessionID` and the
S3 paths.

### Common envelope

```
POST https://<workspace>.365.altium.com/vault/?cls=soap
Host: <workspace>.365.altium.com
User-Agent: AltiumDesignerDevelop-VaultClient
Authorization: AFSSessionID <id>
SOAPAction: "<OperationName>"
Content-Type: text/xml; charset=utf-8
X-Request-ID:
X-Request-Depth: 2
```

```xml
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Header>
    <APIVersion>2.0</APIVersion>
    <User-Agent>AltiumDesignerDevelop-VaultClient</User-Agent>
    <X-Request-ID/>
    <X-Request-Depth>2</X-Request-Depth>
  </s:Header>
  <s:Body>
    <OperationName xmlns="http://tempuri.org/">
      ...operation parameters...
      <SessionHandle>{AFSSessionID, same string as the HTTP header}</SessionHandle>
    </OperationName>
  </s:Body>
</s:Envelope>
```

Notes:

- The query parameter `?cls=soap` is required. The presence of `cls=`
  strongly hints there are other transport classes (`?cls=binary` etc.) on
  the same `/vault/` endpoint, but they were not seen.
- All operations seen are namespaced `http://tempuri.org/` (yes, still the
  Microsoft default, like the other SOAP services).
- All operations seen carry the session twice — once as
  `Authorization: AFSSessionID` and once as `<SessionHandle>` in the body.
  Whether the server actually requires both, or just one, is not tested.
- `APIVersion` is `2.0`; the operation prefix `ALU_` (Altium Live Update?
  / Altium Library Update?) suggests the vault has versioned RPC families.
- `X-Request-Depth` looks like a recursion depth limiter for chained
  service calls; the client always sends `2`.
- All bulk-fetch operations support `<InputCursor/>` for paging; the
  capture never paginates so the cursor format is unknown.
- The bulk filter syntax in `<Filter>` is **SQL-like**:
  `<column> IN ('GUID1', 'GUID2', ...)`. GUID literals are accepted in
  either case (the captured client mixes uppercase and lowercase in the
  same `IN` list). Other operators (`=`, `AND`, `OR`, `LIKE`) are
  presumably supported but not exercised here.
- `<Options>` is an array of `<item>key=value</item>` strings. Booleans
  use `True`/`true` (the capture is inconsistent).
- The server responds in SOAP 1.1 with `xmlns:soap-env=` (note: not
  `soap:`). Two different wrapper shapes appear in practice:
  - **Bulk reads** (`GetALU_ItemRevisions`, `GetALU_ItemRevisionLinks`,
    `GetALU_Items`, `GetALU_Folders`, ...) wrap their records directly
    under the response operation element, with no `<MethodResult>`
    intermediate: `<{op}Response><Records xmlns:i="..."><item>...</item>*</Records></{op}Response>`.
    Verified against `atopile-2.har` for both ops. The `xmlns:i=...`
    XSI namespace declaration on `<Records>` is a hint to
    `i:nil="true"` markers on empty fields inside items.
  - **Broker / single-result ops** (`GetALU_ItemRevisionDownloadURLs`,
    `CheckActionPermissions`, ...) wrap their result in a
    `<MethodResult><Success>true</Success><Results>...</Results></MethodResult>`
    envelope. The presence of `<Success>` lets the client fail fast on
    per-operation errors before walking the result payload.

  A single library helper that always expects `<MethodResult><Records>`
  will quietly return zero records for every bulk read — the bulk-read
  shape is strictly simpler and callers must walk `{op}Response →
  Records` directly.

### 11.1 `GetALU_ItemRevisionLinks`

Reads the link table that connects parent item-revisions (e.g. a
component revision) to their child item-revisions (the symbol it uses,
the footprint(s) it uses, the component template that defines its
parameter schema).

```xml
<GetALU_ItemRevisionLinks xmlns="http://tempuri.org/">
  <Filter>ParentItemRevisionGUID IN ('GUID1','GUID2',...)</Filter>
  <InputCursor/>
  <Options>
    <item>IncludeAllChildObjects=True</item>
    <item>NotFilterRbComponentLinks=True</item>
  </Options>
  <SessionHandle>...</SessionHandle>
</GetALU_ItemRevisionLinks>
```

Each `<item>` in the response carries:

| Element | Meaning |
|---|---|
| `GUID` | the link record's own GUID |
| `HRID` | **the link type**: `PCBLIB`, `SCHLIB`, `ComponentTemplate` (others almost certainly exist — `MODEL`, `DATASHEET`, etc.) |
| `ParentItemRevisionGUID` / `ParentVaultGUID` | the source side (e.g. the component revision) |
| `ChildItemRevisionGUID` / `ChildVaultGUID` | the target side (e.g. the footprint revision) |
| `Data` | a JSON blob with link-type-specific config — for `PCBLIB`: `{"Footprint":{"FootprintIndex":0,"IsDefaultFootprint":true,"PinMapAsString":null}}` |
| `LinkTypeGUID` | typically empty in the capture |
| `LinkParameters` | typically empty in the capture |
| `CreatedAt` / `LastModifiedAt` / `*ByName` / `*ByGUID` / `Is*Masked` / `Is*WorkspaceGuest` | standard audit fields, present on every record returned by the vault |

The `ParentVaultGUID` and `ChildVaultGUID` are both equal to the
workspace GUID — i.e. links never cross workspaces in this capture.

### 11.2 `GetALU_ItemRevisions`

Bulk-fetch full item-revision metadata by GUID list.

```xml
<GetALU_ItemRevisions xmlns="http://tempuri.org/">
  <Filter>GUID IN ('GUID1','GUID2',...)</Filter>
  <InputCursor/>
  <Options>
    <item>IncludeItemRevisionParameters=true</item>
    <item>SupportOwnerAclType=true</item>
    <item>IncludeExtendedRevParameterTypes=True</item>
  </Options>
  <SessionHandle>...</SessionHandle>
</GetALU_ItemRevisions>
```

Each `<item>` in the response carries (selected fields):

| Element | Example |
|---|---|
| `GUID` | revision GUID, e.g. `08CE020C-8B43-4169-8FA4-7EF317BC3E93` |
| `HRID` | full revision HRID, e.g. `PCC-015-0000-1` |
| `ItemGUID` / `ItemHRID` | logical part GUID + HRID (`PCC-015-0000`) |
| `RevisionId` / `RevisionIdLevels` / `RevisionIdSeparators` | revision number + multi-level revision support |
| `AncestorItemRevisionGUID` | previous revision (empty for first) |
| `Description` / `Comment` | catalog text |
| `LifeCycleStateGUID` | which state in the lifecycle this revision is in |
| `ContentTypeGUID` | one of the content-type GUIDs in the table below |
| `FolderGUID` | which vault folder it lives in |
| `SharingControl` | numeric, `0` in capture |
| `AccessRights` | bitfield, `2147483647` (`int32 max`) in capture = full access |
| `ReleaseDate` | ISO timestamp |
| `RevisionParameters` | nested array of `{GUID, HRID, Value, ParamType, ...}` parameter records — includes things like `altium.hash` (a content fingerprint) |

#### Content-type GUIDs (confirmed from this capture)

| HRID prefix | Meaning             | ContentTypeGUID                          |
|-------------|---------------------|------------------------------------------|
| `CMP-`      | Component           | `CB3C11C4-E317-11DF-B822-12313F0024A2`   |
| `CMPT-`     | Component Template  | `D26647D6-2546-4945-852E-CDE06B7E55AD`   |
| `PCC-`      | PCB Component (footprint) | `CB09A478-E317-11DF-B822-12313F0024A2`   |
| `SYM-`      | Schematic Symbol    | `CB22DA24-E317-11DF-B822-12313F0024A2`   |

Three of the four share the suffix `-E317-11DF-B822-12313F0024A2`
(legacy DXP installation GUID — the same family as the search-service
schema suffixes in § 2). The Component Template uses an unrelated GUID,
so it was almost certainly added later.

Other Altium HRID prefixes (`MOD-`, `DSH-`, etc.) presumably have their
own ContentTypeGUIDs but were not in this capture.

### 11.3 `GetALU_ItemRevisionDownloadURLs`  *(the S3 broker)*

This is the call that mints the pre-signed S3 URLs documented in § 10.

```xml
<GetALU_ItemRevisionDownloadURLs xmlns="http://tempuri.org/">
  <ItemRevisionGUIDList>
    <item>8D7A1913-571B-4F14-8FF4-1E690D5C80ED</item>
    <item>5D7D48C5-1B52-432D-BBBD-AC897B298AC6</item>
    ...
  </ItemRevisionGUIDList>
  <Options>
    <item>GetDirectLinks=true</item>
  </Options>
  <SessionHandle>...</SessionHandle>
</GetALU_ItemRevisionDownloadURLs>
```

Response:

```xml
<GetALU_ItemRevisionDownloadURLsResponse xmlns="http://tempuri.org/">
  <MethodResult>
    <Success>true</Success>
    <Results>
      <item>
        <Message/>
        <Success>true</Success>
        <URL>https://ccv-us-west-2.s3.us-west-2.amazonaws.com/{workspaceGuid}/ItemRevisions/{HRID}/{HRID}.zip?X-Amz-Expires=7200&amp;X-Amz-Security-Token=...&amp;X-Amz-Algorithm=AWS4-HMAC-SHA256&amp;X-Amz-Credential=ASIA.../20260408/us-west-2/s3/aws4_request&amp;X-Amz-Date=...&amp;X-Amz-SignedHeaders=host&amp;X-Amz-Signature=...</URL>
      </item>
      <item>...</item>
    </Results>
  </MethodResult>
</GetALU_ItemRevisionDownloadURLsResponse>
```

Notes:

- The order of `<Results>` matches the order of the input
  `<ItemRevisionGUIDList>`. Each item is `{Message, Success, URL}` so
  individual items can fail without failing the whole batch.
- `GetDirectLinks=true` is the option that causes the broker to return
  S3 URLs directly. With that option absent the response is presumably
  a vault-relative URL that the client would have to hit through the
  vault HTTP server (not exercised in capture).
- The 2-hour TTL of the pre-signed URL (§ 10) is set here on the broker
  side via `X-Amz-Expires=7200`.

### 11.4 Workflow: from a search hit to a footprint binary

The full chain — what the Designer client does in this capture, and what a
client written from scratch needs to replicate — is:

1. **Search** (§ 2):
   `searchasync` with `ContentType=Component` and any user filter →
   each `Documents[i].Fields` has `IdC...="R_<RevisionGUID>"` and
   `ItemGUIDC...="<ItemGUID>"`. Take the `R_`-stripped GUID as the
   component revision GUID.
2. **Parent → child links** (§ 11.1):
   `GetALU_ItemRevisionLinks` with
   `Filter=ParentItemRevisionGUID IN ('<componentRevGUID>', ...)` →
   the response has one item per child link. Filter by `HRID` to pick
   `PCBLIB` (footprint), `SCHLIB` (symbol), `ComponentTemplate`, etc.
   The `ChildItemRevisionGUID` is the GUID of the footprint / symbol
   revision. The `Data` JSON tells you whether it's the default
   footprint and what its index is when there are several.
3. **Resolve child HRIDs** (§ 11.2, *optional but typical*):
   `GetALU_ItemRevisions` with
   `Filter=GUID IN ('<childRevGUID1>', ...)` → you get the human-readable
   `HRID` (`PCC-015-0000-1`, `SYM-015-0000-1`) and the `ContentTypeGUID`
   that confirms what kind of object it is. This step is also where
   you pick up `altium.hash` if you want to cache by content.
4. **Mint S3 URLs** (§ 11.3):
   `GetALU_ItemRevisionDownloadURLs` with
   `<ItemRevisionGUIDList>` of the child revision GUIDs from step 2
   (the HRID is *not* needed — the broker resolves GUID → S3 path
   server-side) and `Options[GetDirectLinks=true]` → array of pre-signed
   S3 URLs.
5. **Download from S3** (§ 10):
   plain `GET` of each URL with no Altium auth headers → zip containing
   `Released/<name>.PcbLib` (or `.SchLib`) plus preview PNGs.

The footprint name shown in the search response
(`FootprintName1DD420E8DDD8B445E911A0601BB2B6D53`, e.g. `ROHM-VMN2-2_V`)
is just the *display name* of the footprint inside the `.PcbLib` — not an
HRID and not a revision GUID. To get from "this component" to "this
footprint binary" you must go through the link table, you cannot map by
name.

### 11.5 `GetALU_Items`  *(logical parts — enumeration)*

Reads **logical items** (i.e. items independent of their revisions).
Supports filtering by `ContentTypeGUID`, which makes it the primary
enumeration tool for "list every component / template / project / footprint
item in the workspace".

```xml
<GetALU_Items xmlns="http://tempuri.org/">
  <SessionHandle>...</SessionHandle>
  <Options>
    <item>IncludeItemParameters=true</item>
    <item>ExcludeACLEntries=true</item>
    <item>SupportOwnerAclType=true</item>
  </Options>
  <Filter>ContentTypeGUID IN ('CB3C11C4-E317-11DF-B822-12313F0024A2')</Filter>
  <InputCursor/>
</GetALU_Items>
```

The filter is SQL-like and can use either `IN ('...')` or `='...'`
(`CONTENTTYPEGUID='...'` was also seen — field names are case-insensitive).

Response records (one `<item>` per logical part) include:

| Element | Meaning |
|---|---|
| `GUID` | `ItemGUID` — the stable logical-part identifier |
| `HRID` | item-level HRID, e.g. `PRJT-0000`, `CMP-015-00042`, `PCC-015-0000` |
| `Description` | free text |
| `FolderGUID` | folder the item lives in (resolve via § 11.6) |
| `LifeCycleDefinitionGUID` | which lifecycle applies |
| `RevisionNamingSchemeGUID` | how revision HRIDs are minted |
| `ContentTypeGUID` | kind of item (see § 11.2 content-type table) |
| `Revisions` | **nested array of revisions** — each entry is a full `GetALU_ItemRevisions`-style record (§ 11.2). When the server returns this inline you do not need a separate `GetALU_ItemRevisions` call. |
| `Tags` | nested array of `{GUID, TagFamilyGUID, ...}` |
| `ItemParameters` | item-level parameter array (separate from revision-level `RevisionParameters`) |
| `IsShared`, `IsActive`, `SharingControl`, `AccessRights` | standard |

The `MethodResult` envelope also carries `<MoreDataAvailable>true|false</MoreDataAvailable>`
— if true you paginate with `<InputCursor/>` (cursor format is returned in
the response but not paginated in any capture; format not directly
observed).

### 11.6 `GetALU_Folders`  *(folder tree)*

Returns the full vault folder hierarchy. Used to resolve `FolderGUID` to a
human folder path and to discover the library taxonomy.

```xml
<GetALU_Folders xmlns="http://tempuri.org/">
  <Filter/>                           <!-- empty filter = all folders -->
  <InputCursor/>
  <Options>
    <item>IncludeAllChildObjects=True</item>
    <item>ExcludeACLEntries=True</item>
    <item>IncludeSystemFolders=true</item>
    <item>IncludeFolderParameters=True</item>
    <item>SupportOwnerAclType=true</item>
  </Options>
  <SessionHandle>...</SessionHandle>
</GetALU_Folders>
```

Each record:

| Element | Meaning |
|---|---|
| `GUID` | folder GUID |
| `HRID` | folder display name (e.g. `Datasheets`, `Sample - Kame_IMU`) |
| `ParentFolderGUID` | parent in the tree (root folders have an empty parent) |
| `FolderTypeGUID` | folder type (Datasheets, Samples, Library categories, ...) |
| `SubFolders` | nested children when `IncludeAllChildObjects=True` |
| `FolderParameters` | arbitrary folder metadata; includes magic entries like `$$!NAMING_SCHEME!$$` that bind a naming scheme to the folder |
| `IsShared`, `IsActive`, `SharingControl`, `AccessRights`, `Attributes`, `Weight`, `FacetCount` | standard |

### 11.7 `GetALU_LifeCycleStates`  *(dictionary)*

Resolves `LifeCycleStateGUID` → human name. The response is exactly what's
needed to turn the opaque GUIDs in `GetALU_ItemRevisions` into strings like
`Released`, `Draft`, `In Review`, ....

```xml
<GetALU_LifeCycleStates xmlns="http://tempuri.org/">
  <SessionHandle>...</SessionHandle>
  <Options><item>IncludeAllChildObjects=true</item></Options>
  <Filter>GUID IN ('9138CC89-47A5-4E3D-942A-2C2A881D7DEC')</Filter>
  <InputCursor/>
</GetALU_LifeCycleStates>
```

Response record:

| Element | Example / meaning |
|---|---|
| `GUID` | the state GUID |
| `HRID` | state name, e.g. `Draft` |
| `Description` | e.g. `Just released` |
| `StateIndex` | numeric ordering within a lifecycle definition, e.g. `20` |
| `LifeCycleStageGUID` | which stage the state belongs to |
| `LifeCycleDefinitionGUID` | which lifecycle definition this state is part of |
| `IsInitialState`, `IsVisible`, `IsApplicable` | booleans |
| `Color`, `TextColor` | ARGB hex (`00F0F0F0`) for UI rendering |

Calling with an empty `<Filter/>` presumably returns every state across
every lifecycle definition; only GUID-list filters were exercised.

### 11.8 `GetALU_Tags`  *(taxonomy)*

Returns the tag taxonomy within a `TagFamilyGUID`. Tags form a tree
(`ParentTagGUID` / `SubTags`).

```xml
<GetALU_Tags xmlns="http://tempuri.org/">
  <SessionHandle>...</SessionHandle>
  <Options><item>IncludeAllChildObjects=true</item></Options>
  <Filter>(TagFamilyGUID IN ('4D9E8D0B-5C9B-43FD-8668-DD6BDD97C109'))</Filter>
  <InputCursor/>
</GetALU_Tags>
```

Observed tags within that family: `Clock&Timing`, `Memory`, `Wireless`, ...
(standard component-taxonomy tags). Each record carries `{GUID, HRID,
TagFamilyGUID, ParentTagGUID, SubTags}` plus the usual audit fields.

Tag families themselves are presumably exposed by a separate operation
(`GetALU_TagFamilies`?) that was not exercised.

### 11.9 `GetALU_VaultRecord`  *(vault metadata + capability flags)*

Returns the one record describing the vault itself — what software version
it runs, what feature flags are enabled, which adjacent services it
advertises. Called both on the workspace (`<workspace>.365/vault/`) and on
the central **`vault.api.altium.com`** host (§ 14.3) with the JWT.

```xml
<GetALU_VaultRecord xmlns="http://tempuri.org/">
  <SessionHandle>...</SessionHandle>
</GetALU_VaultRecord>
```

Response has a single `<Vault>` record with:

| Element | Example |
|---|---|
| `GUID` | vault GUID (`B7D15EC3-160A-466F-A46C-7801FE6295A7` on the central vault) |
| `HRID` | `Altium Content Vault` |
| `VersionId` | e.g. `AltiumVault-1.0.1` |
| `Parameters` | pipe-delimited feature-flag list — see code block below |
| `AcquisitionServiceURL`, `AppRegistryServiceURL` | adjacent legacy service URLs |
| `AccessRights` | bitfield (`1` on central vault, `2147483647` on workspace) |

Example `Parameters` value (pipe-delimited, one `key=value` pair per flag):

```
ProductVersion=3.0.5|RawSearchSupport=True|HiddenRevisionSupport=True|RawParamsSearchSupport=True|SystemFolderSupport=True|ComponentsSupport=True|ContentTypesLinkSupport=True|AdvancedSearchSupport=True|DataSheetsSupport=True|DynamicDataInSearch=True|Comments20Support=True|GeneralTasksSupport=True|PartRequestsSupport=True|ComponentCertificationSupport=True|DesignReuse2WhereUsedSupport=True|...
```

The `Parameters` blob is the authoritative source of truth for "does this
vault support X" feature checks. Clients should parse it rather than
hard-coding version assumptions.

### 11.10 `CheckActionPermissions`

Asks the server what the current session can do with a given entity type.

```xml
<CheckActionPermissions xmlns="http://tempuri.org/">
  <EntityType>AluComponent</EntityType>
  <Options i:nil="true"/>
  <SessionHandle>...</SessionHandle>
</CheckActionPermissions>
```

Response:

```xml
<CheckActionPermissionsResponse xmlns="http://tempuri.org/">
  <MethodResult><Success>true</Success></MethodResult>
  <Permissions>
    <item>Release</item>
  </Permissions>
</CheckActionPermissionsResponse>
```

`EntityType` values observed: `AluComponent`. Presumably also `AluFolder`,
`AluItem`, `AluItemRevision`, etc. The returned `<Permissions>` list is
free-form string tokens (only `Release` was seen).

### 11.11 Other vault ops used in the flow

The full-flow capture also exercises these with bodies identical in shape
to the above but not individually documented:

- `/vault/CheckRevision` (GET) — a plain GET, used as a readiness/version
  check during the session.
- `/SearchTemplatesService/SearchTemplatesService.asmx` — SOAP service for
  saved search templates.
- `/vcs/vcswebservice.asmx` — version control service; exercised during
  session bootstrap with one opaque POST.

### 11.12 Updated workflow: enumerate everything

Given the expanded operation set, the full "enumerate all components and
hydrate them" chain is:

1. **Discover categories** (optional, one call):
   `POST /search/v1.0/adsearch/querycomponenttypefacets` (§ 2.x) with body
   `{"ExcludeLifecycleGuids":[]}` → returns one entry per component-type
   path in the vault (`resistors\`, `capacitors\`,
   `integrated circuits\amplifiers\`, ...) with per-type facet histograms
   for all indexed fields. Use this to enumerate the taxonomy without
   having to guess category names.
2. **Resolve dictionaries once**:
   - `GetALU_Folders` (§ 11.6) with empty filter → full folder tree.
   - `GetALU_LifeCycleStates` (§ 11.7) with empty filter → GUID → name.
   - `GetALU_Tags` (§ 11.8) per tag family → taxonomy.
3. **Enumerate components** (two alternatives):
   - **a.** `GetALU_Items` (§ 11.5) with
     `Filter=ContentTypeGUID='CB3C11C4-E317-11DF-B822-12313F0024A2'`
     → one record per logical component, with all revisions inline. This
     is the *authoritative* enumeration (includes drafts / hidden /
     archived, whatever the ACL allows).
   - **b.** `searchasync` (§ 2) with
     `ContentType=Component, LatestRevision=1` and no category filter, and
     `Limit: 2147483647`. This is the *search-indexed* enumeration — might
     miss items that are not indexed (drafts, items in `MUST_NOT`
     lifecycle states).
4. **For each component revision**: `GetALU_ItemRevisionLinks` (§ 11.1)
   with `ParentItemRevisionGUID IN (...)` to discover child item-revision
   GUIDs (PCBLIB, SCHLIB, PCBLIB 1/2/... for multi-footprint, ComponentTemplate,
   and presumably MODEL / DATASHEET link types not seen in any capture).
5. **Hydrate children**: `GetALU_ItemRevisions` (§ 11.2) for any child
   revision GUIDs you did not already get inline from `GetALU_Items`.
6. **Mint S3 URLs**: `GetALU_ItemRevisionDownloadURLs` (§ 11.3). Batches
   of many GUIDs in a single call are supported.
7. **Download** from S3 (§ 10).
8. **(Optional) Live supplier data**: `PartChoices/ComponentDynamicData/Get`
   (§ 3.2) with the live JWT in `Options[]`.

The capture shows the Designer client actually batches steps 4/5/6
aggressively — up to 11 revision GUIDs per `GetALU_ItemRevisionDownloadURLs`
call.

---

## 12. Service discovery — `<workspace>.365.altium.com/servicediscovery/servicediscovery.asmx`

This endpoint has two roles on the same URL:

### 12.1 `GET` — health/ping

```
GET https://<workspace>.365.altium.com/servicediscovery/servicediscovery.asmx
```

Plain `GET` with only the `Host` header. Response is the literal string
`OK`. The Designer client calls this periodically as a liveness check.

### 12.2 `POST` — `Login` (the **critical** call)

This is the call that converts the global JWT into a workspace-scoped
short `AFSSessionID` **and** returns the full list of endpoints for every
per-workspace / per-region service the session should talk to.

```
POST https://<workspace>.365.altium.com/servicediscovery/servicediscovery.asmx
SOAPAction: http://altium.com/Login
Content-Type: text/xml; charset=utf-8
```

```xml
<Login xmlns="http://altium.com/">
  <userName>user@example.com</userName>
  <password>*IDSGS*{JWT}</password>
  <secureLogin>false</secureLogin>
  <discoveryLoginOptions>None</discoveryLoginOptions>
  <productName>Altium Designer Develop</productName>
</Login>
```

Notes on the request:

- The **namespace** is `http://altium.com/` (not `http://tempuri.org/` —
  one of the only Altium SOAP endpoints where the namespace is
  non-default).
- `password` is the JWT prefixed with the literal string `*IDSGS*`. This
  magic prefix tells the vault "this is an IDS (Identity Service) token,
  not a plaintext password". Without it you will get a normal password
  auth path.
- `userName` is the account email.
- `productName` observed: `Altium Designer Develop` (the internal/dev
  build). A shipping client would send `Altium Designer`.
- `secureLogin=false` and `discoveryLoginOptions=None` are the defaults.
  Other values not exercised.

Response:

```xml
<LoginResponse xmlns="http://altium.com/">
  <LoginResult>
    <Endpoints>
      <EndPointInfo>
        <ServiceKind>VAULT</ServiceKind>
        <ServiceUrl>https://atopile-2.365.altium.com:443/vault/?cls=soap</ServiceUrl>
      </EndPointInfo>
      <EndPointInfo>
        <ServiceKind>SEARCH</ServiceKind>
        <ServiceUrl>https://usw.365.altium.com/search/SearchService.asmx</ServiceUrl>
      </EndPointInfo>
      ... 63 more EndPointInfo entries ...
    </Endpoints>
    <UserInfo>
      <SessionId>F29EC130-DBF9-4F11-A8C6-D39D9243DFE7ecc90f5c-559e-4318-9ce1-88776570b379</SessionId>
      <UserId>FEBEFC48-C3A9-4769-8DE5-ED5317192083</UserId>
      <AccountId>ECC90F5C-559E-4318-9CE1-88776570B379</AccountId>
      <Email>...</Email>
      <FullName>...</FullName>
      <Organisation>Atopile</Organisation>
      <AuthType>0</AuthType>
      <Parameters>
        <UserParameter><Name>HRID</Name><Value>user@example.com</Value></UserParameter>
        <UserParameter><Name>IDSHostName</Name><Value>I-00EAA88C57052</Value></UserParameter>
        ...
      </Parameters>
    </UserInfo>
  </LoginResult>
</LoginResponse>
```

Key facts:

- `UserInfo/SessionId` is the **short `AFSSessionID`** used by every
  subsequent call in §§ 2–6, 10, 11 and 13. Extract it and use it as the
  `Authorization: AFSSessionID <value>` header and as the
  `<SessionHandle>` body element.
- `AccountId` here equals the workspace GUID (the workspace ID
  `ECC90F5C-559E-4318-9CE1-88776570B379` uppercased). This is the same
  value that appears as the second half of `SessionId` (lowercased).
- `UserId` is the workspace-scoped user GUID (not the `ContactGUID` from
  the global portal login — they are different identifiers).
- `Endpoints` is a list of 65 `{ServiceKind, ServiceUrl}` records — this
  is the **entire** service directory for this workspace. See § 14.5 for
  the full list of `ServiceKind` values.
- The capture shows this endpoint returning `OK` on `GET` *in addition*
  to the SOAP surface, so probing the URL with a `GET` does not break
  the `POST` login path.

---

## 13. WebSocket — `<workspace>.365.altium.com/websocket/...`

The Designer client opens a long-lived WebSocket to the workspace host for
push notifications. The handshake is split across two calls.

### 13.1 `POST /websocket/WebService.asmx` — `GetChannelUrl`

```
POST https://<workspace>.365.altium.com/websocket/WebService.asmx
Content-Type: text/xml; charset=utf-8
SOAPAction: http://tempuri.org/GetChannelUrl
```

```xml
<SOAP-ENV:Envelope ...>
  <SOAP-ENV:Body>
    <GetChannelUrl xmlns="http://tempuri.org/">
      <SessionId>{AFSSessionID}</SessionId>
      <ChannelName>AltiumDesigner-DxpServerManager</ChannelName>
    </GetChannelUrl>
  </SOAP-ENV:Body>
</SOAP-ENV:Envelope>
```

Response:

```xml
<GetChannelUrlResponse xmlns="http://tempuri.org/">
  <GetChannelUrlResult>wss://atopile-2.365.altium.com:443/websocket/ws.ashx</GetChannelUrlResult>
</GetChannelUrlResponse>
```

Notes:

- `ChannelName` is `AltiumDesigner-DxpServerManager` in the capture.
  Other channel names presumably exist for other Altium subsystems.
- This call is unauthenticated apart from the `SessionId` element.
- The returned URL is just the same workspace host with the
  `/websocket/ws.ashx` path, so step 13.2 could probably skip this call —
  but the official client always does the handshake, presumably so the
  server can return a different node when it scales out.

### 13.2 `GET /websocket/ws.ashx` — WebSocket upgrade

```
GET wss://<workspace>.365.altium.com/websocket/ws.ashx
Host: <workspace>.365.altium.com
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Key: <base64>
Sec-WebSocket-Version: 13
User-Agent: websocket-sharp/1.0
Cookie: ClientID={DB770A61-005A-4732-840F-992FA78D39D2}; IDS_SessionId={AFSSessionID}
```

Response:

```
HTTP/1.1 101 OK
Upgrade: websocket
Connection: upgrade
Sec-WebSocket-Accept: <base64>
Server: Microsoft-IIS/10.0
```

Notes:

- This is the *only* place in any capture where authentication is carried
  by HTTP cookies instead of the `Authorization` header — the IIS
  WebSocket handler reads `IDS_SessionId` from the cookie jar.
- `ClientID` cookie is the per-installation Designer instance GUID (in
  the curly-brace notation Altium uses internally). It is not the same as
  the workspace GUID or the session GUID.
- The Designer client uses `websocket-sharp/1.0` (a .NET WebSocket
  library), confirming this is the desktop Altium Designer talking, not
  a browser.
- After the upgrade, the actual message protocol is binary / framed
  WebSocket data and is **not** decoded by the HAR capture.

---

## 14. Bootstrap and global identity services

Reverse-engineered from
`checkappexec.microsoft.com_04_08_2026_16_52_39.har` (full start-up →
search → insert flow, 249 entries, 35 hosts).

Before any of the endpoints in §§ 2–13 can be used, the client has to go
through a **five-host bootstrap dance** that exchanges the global OIDC
JWT for a workspace-scoped short `AFSSessionID`, picks up account/license
info, and discovers the per-workspace endpoint directory. This is the
flow in order.

### 14.1 Root credential: OIDC JWT from `auth.altium.com`

Reverse-engineered from `auth.har` (silent re-authentication of an
existing session) and `login.har` (full logout → fresh login →
re-auth). Every step of the OIDC flow is now in a capture except
federated IdP redirects (Google / Facebook / WebAuthn — see § 14.1.9).

The client obtains its JWT access token from an IdentityServer 4 OIDC
issuer at `auth.altium.com`, using **OAuth 2.0 Authorization Code flow
with PKCE** (RFC 7636). What makes this flow unusual for a desktop app
is that the redirect URI is **server-hosted on `auth.altium.com`
itself**, not localhost or a custom URL scheme; the auth code is handed
back to the desktop client out-of-band through
`actionwait.altium.com/await` (§ 14.1.4).

#### 14.1.1 Client configuration

Static values for the Altium Designer desktop client:

| Field | Value |
|---|---|
| `client_id` | `3CD47A94-0610-4FA9-B3E4-C9C256FD84AE` |
| `scope` | `a365 a365:requirements openid profile` |
| `redirect_uri` | `https://auth.altium.com/api/AuthComplete` |
| `response_type` | `code` |
| `code_challenge_method` | `S256` (in practice — see the bug note below) |

The scopes can be fetched dynamically from
`GET https://auth.altium.com/api/ClientScopes?clientId={client_id}&includeOfflineAccess=False`
which returns a bare JSON array:

```json
["a365", "a365:requirements", "openid", "profile"]
```

Note: `includeOfflineAccess=False` means the client does **not** request
the `offline_access` scope, so the token response will **not** include a
`refresh_token`. Re-authentication relies on the `ALU_SID_2` cookie (see
§ 14.1.3) instead of refresh tokens.

#### 14.1.2 The happy-path flow

```
 ┌──────────────┐                                  ┌──────────────────┐
 │ desktop app  │                                  │  auth.altium.com │
 └──────┬───────┘                                  └─────────┬────────┘
        │                                                    │
        │  1. generate state (UUID) + code_verifier          │
        │     + code_challenge = base64url(sha256(verifier)) │
        │                                                    │
        │  2. open browser at /connect/authorize             │
        │ ──────────────────────────────────────────────────▶│
        │                                                    │
        │        (browser follows redirects + login form,    │
        │         or re-uses ALU_SID_2 cookie silently)      │
        │                                                    │
        │ ◀────────────────── 302 to /api/AuthComplete?code= │
        │                                                    │
        │ ────────────▶ /api/AuthComplete?code=...           │
        │ ◀────────────────── 302 to /authCompleted         │
        │                                                    │
        │ ────────────▶ /authCompleted (HTML + auth-module.js)
        │     (JS extracts code from URL,                    │
        │      POSTs to actionwait.altium.com/await          │
        │      keyed by state UUID — § 14.1.4)               │
        │                                                    │
        │  3. desktop polls actionwait.altium.com/await      │
        │     with state UUID, picks up the code             │
        │                                                    │
        │  4. POST /connect/token                            │
        │     grant_type=authorization_code                  │
        │     code=<pickedup>                                │
        │     code_verifier=<locally generated>              │
        │     client_id=3CD47A94-...                         │
        │ ──────────────────────────────────────────────────▶│
        │ ◀────────── { id_token, access_token, expires_in } │
        │                                                    │
        │  5. cache JWT, use as subject_token on portal365   │
        │     (§ 14.2) and vault.api (§ 14.3)                │
        └────────────────────────────────────────────────────┘
```

#### 14.1.3 Wire-level detail

**Step 1 (browser): `GET /connect/authorize`**

```
GET https://auth.altium.com/connect/authorize
  ?client_id=3CD47A94-0610-4FA9-B3E4-C9C256FD84AE
  &response_type=code
  &scope=a365+a365%3arequirements+openid+profile
  &redirect_uri=https%3a%2f%2fauth.altium.com%2fapi%2fAuthComplete
  &code_challenge=<base64url(sha256(code_verifier))>
  &state=<UUID>
  &code_challenge_method=
Cookie: ALU_SID_2=<previous JWT if remembered>; ALU_UID_3=<ContactGUID>; ...
```

Notes:

- `code_challenge_method` — the fresh-login capture (`login.har`,
  § 14.1.7) sends it as `S256` correctly, but the silent-re-auth
  capture (`auth.har`) sends it **empty**. Either shape is accepted
  by the Altium IdentityServer, which infers S256 from the challenge
  length. A clean implementation should always send
  `code_challenge_method=S256`; the empty-string case looks like a
  bug in one of the Altium client code paths.
- PKCE verifier length: 64+ chars (72 and 68 in the two captures),
  base64url charset.

Silent SSO: if the browser already has an `ALU_SID_2` cookie (a previous
JWT stashed on `.altium.com`), the server **skips the login form**
entirely and 302s straight to `/api/AuthComplete` with a fresh code.
First-time login would instead serve an HTML login form here — that
form's submit target is not in any capture (§ 14.1.5).

**Step 1 response (silent SSO path):**

```
HTTP/1.1 302 Found
Location: https://auth.altium.com/api/AuthComplete?code=<ONE_TIME_CODE>&scope=a365+a365%3Arequirements+openid+profile&state=<same UUID>&session_state=<...>&iss=https%3A%2F%2Fauth.altium.com
Set-Cookie: idsrv.session=<hex>; path=/; samesite=none
Set-Cookie: idsrv=<encrypted blob>; path=/; httponly
Set-Cookie: ALU_SID_2=<new JWT>; domain=.altium.com; path=/; secure
Set-Cookie: ALU_LLR_2=True; domain=.altium.com; path=/; secure; httponly  ; expires=<+30d>
Set-Cookie: ALU_UID_3=<ContactGUID>; domain=.altium.com; path=/; secure    ; expires=<+30d>
```

The server also re-stashes the JWT in `ALU_SID_2` so subsequent silent
re-auth works without forcing another login.

**Step 2: `GET /api/AuthComplete?code=...&state=...`**

A simple 302 to `/authCompleted`. This is the server-hosted landing page
that receives the OAuth code. No body, no additional work on the
server side.

**Step 3: `GET /authCompleted` and `/auth-module.js`**

Returns a small HTML page that loads `auth-module.js` (the client-side
code that does the out-of-band hand-off to `actionwait.altium.com` —
§ 14.1.4):

```html
<!DOCTYPE html>
<html>
  <head>
    <title>Authenticate server</title>
    <link rel="stylesheet" href="/promo.css" />
  </head>
  <body>
    <div id="auth-module-page"></div>
    <script type="module" src="/auth-module.js?ver=20260406152224"></script>
    <script>
      document.addEventListener('DOMContentLoaded', () => {
        const app = window.__initAuthModule('#auth-module-page', { /* config */ });
      });
    </script>
  </body>
</html>
```

The JS reads `/api/config` for its runtime settings (Sentry DSN,
GoogleOneTap client id, legal resources URLs, localization, root
cookie domain).

**Step 4: `POST /connect/token`**

Standard RFC 6749 authorization-code token exchange with PKCE:

```
POST https://auth.altium.com/connect/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code
&code=<one-time-code from step 1>
&redirect_uri=https%3A%2F%2Fauth.altium.com%2Fapi%2FAuthComplete
&code_verifier=<original verifier from step 1>
&client_id=3CD47A94-0610-4FA9-B3E4-C9C256FD84AE
```

No client secret — this is a public client (PKCE substitutes for the
secret).

Response:

```json
{
  "id_token":     "eyJ...",
  "access_token": "eyJ...",
  "expires_in":   2592000,
  "token_type":   "Bearer",
  "scope":        "a365 a365:requirements openid profile"
}
```

- `expires_in = 2592000` = 30 days.
- No `refresh_token` (because `offline_access` was not requested).
- `token_type = Bearer`.

**`id_token` payload** (OIDC user profile):

| Claim | Example | Meaning |
|---|---|---|
| `iss` | `https://auth.altium.com` | issuer |
| `aud` | `3CD47A94-0610-4FA9-B3E4-C9C256FD84AE` | the Altium Designer `client_id` |
| `nbf` / `iat` / `exp` | unix seconds | `exp - iat = 2 592 000` (30 days) |
| `amr` | `["pwd"]` | authentication method: password |
| `at_hash` | `eALGib39LGoP4LKHLLcCmg` | base64url of first half of `sha256(access_token)`, per OIDC core spec |
| `sid` | `C1256026CD247EDC77723F1DB06253CB` | matches the `idsrv.session` cookie |
| `sub` | `95921344-1622-4BEC-8CBE-DB9961995230` | user ContactGUID (also in `ALU_UID_3` cookie) |
| `idp` | `local` | Altium's own user DB (not federated SSO) |
| `username` | `user@example.com` | login email |
| `organization_id` | `95A5B1DE-4038-4009-80DA-57B01FB471C9` | portal-level AccountId (§ 14.2.2) |
| `given_name` / `family_name` / `email` | | standard OIDC profile claims |

**`access_token` payload** (used as the `Handle` / `SessionHandle` / Bearer on every global service):

| Claim | Example | Meaning |
|---|---|---|
| `iss` | `https://auth.altium.com` | |
| `scope` | `["a365", "a365:requirements", "openid", "profile"]` | |
| `amr` | `["pwd"]` | |
| `client_id` | `3CD47A94-0610-4FA9-B3E4-C9C256FD84AE` | |
| `sub` | `<ContactGUID>` | |
| `idp` | `local` | |
| `ip` | `99.0.86.108` | client IP at issue time (audit trail) |
| `sid` | `<same as id_token>` | idsrv session link |
| `jti` | `<hex>` | JWT id for revocation |

Note the access_token has **no** `username`, `email`, or
`organization_id` — those are id_token-only claims. For user display,
decode the id_token.

The signing key (`kid: 09729A92D54D9DF22D430CA23C6B8B2E`) is an RSA
public key that can presumably be fetched from the standard JWKS URL
`https://auth.altium.com/.well-known/openid-configuration` /
`/.well-known/jwks` (not exercised in capture but trivial to verify).

#### 14.1.4 Out-of-band code delivery via `actionwait.altium.com`

The `ActionWait` ServiceKind from § 14.6 is finally documented. This is
a **tiny polling bridge** that lets the browser hand an OAuth code back
to the desktop client without either side needing a localhost listener
or a custom URL scheme.

```
POST https://actionwait.altium.com/await
Content-Type: application/json; charset=utf-8

{"token":"<state UUID from /connect/authorize>"}
```

Response (when the other side of the bridge has delivered the code):

```json
{
  "actionResult": "OK",
  "data": {
    "code":          "<OAuth authorization code>",
    "scope":         "a365 a365:requirements openid profile",
    "state":         "<same UUID the client posted>",
    "session_state": "<IDSrv4 session state>",
    "iss":           "https://auth.altium.com"
  }
}
```

Mechanism (inferred — both sides of the bridge are not in the capture):

- The desktop client generates a `state` UUID and uses it as **both**
  the OIDC `state` parameter (CSRF protection) **and** the bridge
  channel key.
- The desktop opens a browser at `/connect/authorize`.
- After the browser has followed all the redirects and landed on
  `/authCompleted`, the `auth-module.js` code extracts `{code, state,
  session_state, iss}` from the URL and POSTs them to
  `actionwait.altium.com/await` (presumably with a different request
  shape than above — "deliver", not "pick up"). This half is not in
  the capture.
- The desktop client meanwhile polls
  `POST actionwait.altium.com/await` with `{"token": <state UUID>}`.
  When the browser has delivered, the server returns the stashed data.
  Until then it presumably long-polls or returns a "not ready" status.
- The desktop then does the PKCE token exchange against
  `/connect/token` using its locally-held `code_verifier`.

The capture shows the pickup side only. The bridge stores the OAuth
code (not the token itself), so the server that hosts `actionwait` does
not need any privileged relationship with the auth server — it's a
dumb key/value rendezvous.

Benefits of this design over the common alternatives:

- No localhost listener (no firewall prompts, no port conflicts)
- No custom URL scheme (no OS-level registration, no file-
  association/taskbar weirdness)
- No embedded browser (works with the system default)
- Nothing to forward to from corporate proxies
- PKCE still prevents the `actionwait` operator from stealing tokens —
  whoever picks up the code still needs the `code_verifier`, which
  never leaves the desktop client

#### 14.1.5 Token exchange for gov cloud (`auth.365-gov.altium.com`)

Immediately after getting the standard-cloud JWT, the Designer client
speculatively performs an RFC 8693 token exchange to get a
**second** JWT from the gov-cloud issuer:

```
POST https://auth.365-gov.altium.com/connect/token
Content-Type: application/x-www-form-urlencoded

grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Atoken-exchange
&subject_token=<auth.altium.com JWT>
&subject_token_type=urn:ietf:params:oauth:token-type:access_token   (implied)
```

Response:

```json
{
  "access_token": "eyJ... (new JWT, iss=https://auth.365-gov.altium.com)",
  "expires_in":   2592000,
  "token_type":   "Bearer",
  "scope":        "a365 a365:requirements openid profile"
}
```

The new access token has:

- Same `sub`, same `client_id`, same 30-day TTL
- **Different `iss`**: `https://auth.365-gov.altium.com`
- **New `secure: "1"` claim** marking it as gov-cloud-scoped
- No `id_token` in the response — the user profile claims are already
  known from the standard-cloud id_token

This lets a single desktop client talk to both the public Altium 365
cloud and a FedRAMP/gov workspace using its own issuer. Regular users
who don't have a gov-cloud entitlement can skip this entirely.

#### 14.1.6 First-time login via password

Reverse-engineered from `login.har`. When the browser has **no**
`idsrv.session` / `ALU_SID_2` cookies (either first-time or after
logout), `/connect/authorize` redirects to a sign-in form and the
desktop client drives it via a small JSON API. Five extra calls vs.
the silent re-auth path.

**Step A: `GET /connect/authorize` → `302 /signin?ReturnUrl=...`**

Same `/connect/authorize` request as § 14.1.3 (full PKCE + state +
code_challenge), but this time the request carries **no session
cookies**, so the server generates an opaque `authzId` that
encapsulates the authorize request parameters server-side and redirects
the browser to its login form:

```
HTTP/1.1 302 Found
Location: https://auth.altium.com/signin
  ?ReturnUrl=/connect/authorize/callback
    ?authzId=8F61445E30AC848C49403BEF5927152B3F2C0BDD566473FE50BE23DD91C8C333_1775750632
    &client_id=3CD47A94-...
    &redirect_uri=https://auth.altium.com/api/AuthComplete
```

The `authzId` is `<64-hex>_<unix-timestamp>`. It's a **server-side
pointer** into the stashed authorize request, so the client cannot
modify `redirect_uri` / `scope` / `state` / `code_challenge` after
the login — whatever the client sent to `/connect/authorize` is
locked in by the time the login form is shown.

**Step B: `GET /signin?ReturnUrl=...`** returns a small HTML stub
that loads `auth-module.js` + `/api/config` (same SPA as
`/authCompleted` in § 14.1.3).

**Step C: `POST /api/userContext/current`**

Driven by the JS. Fetches **globally** available authentication
methods for the current authorize request.

```
POST https://auth.altium.com/api/userContext/current
Content-Type: application/json-patch+json

{
  "returnUrl": "/connect/authorize/callback?authzId=...&client_id=...&redirect_uri=...",
  "force":     false,
  "includeMethods": null
}
```

Note: `Content-Type: application/json-patch+json` is used for **every**
auth JSON endpoint in this subsystem. ASP.NET Core accepts it as JSON;
use either value.

Response — **this is the auth-methods directory**:

```json
{
  "password": { "enabled": true },
  "webAuth":  { "enabled": true },
  "google":   {
    "enabled":   true,
    "returnUrl": "https://accounts.google.com/o/oauth2/v2/auth?client_id=94467084020-...&redirect_uri=https%3a%2f%2fauth.altium.com%2foauth%2fcallback%2fgoogle%3fforce%3dtrue&scope=profile+email&state=..."
  },
  "facebook": {
    "enabled":   true,
    "returnUrl": "https://www.facebook.com/v2.9/dialog/oauth?client_id=144743119526933&response_type=code&redirect_uri=...&scope=email,public_profile&state=..."
  }
}
```

Four methods are observable on this tenant:

| Method | What it does |
|---|---|
| `password` | Standard email + password form, hits `/api/account/signIn` (step E) |
| `webAuth` | **WebAuthn / FIDO2** — passkeys or hardware security keys. The client-side JS handles the WebAuthn ceremony and posts to an endpoint not exercised in the capture (probably `/api/account/webAuthnSignIn` or similar) |
| `google` | **Federated Google OAuth.** The `returnUrl` is a fully-assembled Google authorize URL — clicking it bounces the browser through Google and then back to `auth.altium.com/oauth/callback/google?force=true` |
| `facebook` | **Federated Facebook OAuth**, same pattern as Google, callback at `/oauth/callback/facebook` |

Other SSO providers (Microsoft, Apple, SAML) are presumably
advertised the same way on tenants that have them enabled.

**Step D: `POST /api/userContext/authenticationMethods`**

Once the user has typed an email but before submitting the password,
the JS re-queries with the email so the server can disable methods the
user doesn't actually have configured.

```
POST https://auth.altium.com/api/userContext/authenticationMethods
Content-Type: application/json-patch+json

{
  "userName":        "user@example.com",
  "returnUrl":       "/connect/authorize/callback?authzId=...",
  "includeMethods":  null
}
```

Same shape of response as 14.1.6.C, plus a top-level `user` object:

```json
{
  "password": { "enabled": true },
  "webAuth":  { "enabled": true },
  "google":   { "enabled": true, "returnUrl": "..." },
  "facebook": { "enabled": true, "returnUrl": "..." },
  "user": {
    "userName":           "user@example.com",
    "fullName":           "Full Name",
    "canSelectAnotherUser": true
  }
}
```

`canSelectAnotherUser: true` is the "not you?" link on the login UI
that resets the form back to the email-entry step.

**Step E: `POST /api/account/signIn`  *(the password POST)*

```
POST https://auth.altium.com/api/account/signIn
Content-Type: application/json-patch+json

{
  "userName":   "user@example.com",
  "password":   "<plaintext password>",
  "persistent": true,
  "returnUrl":  "/connect/authorize/callback?authzId=...&client_id=...&redirect_uri=...",
  "visitorId":  null
}
```

Notes:

- **The password is sent plaintext over TLS.** No client-side hashing,
  no challenge/response. Standard bearer-style form auth.
- `persistent: true` = "remember me" — causes the server to set the
  `ALU_LLR_2=True` cookie with a ~30-day expiry. With
  `persistent: false` the cookie is a session cookie.
- `visitorId` — optional fingerprint / bot-detection blob. `null` in
  the capture. Probably used on the public sign-up flow.
- No CSRF header. The request relies on same-site cookies + the
  server-held `authzId` (which is single-use) for integrity.

Response — `{ returnUrl }` only — but the important payload is in the
`Set-Cookie` headers:

```
HTTP/1.1 200 OK
Content-Type: application/json; charset=utf-8

Set-Cookie: ALU_UID_2=user%40example.com; path=/
Set-Cookie: idsrv.session=<new hex sid>; path=/; samesite=none
Set-Cookie: idsrv=<encrypted blob>; path=/; samesite=lax; httponly
Set-Cookie: ALU_SID_2=<new JWT>; domain=.altium.com; path=/; secure
Set-Cookie: ALU_LLR_2=True; domain=.altium.com; path=/; secure; httponly; expires=+30d
Set-Cookie: ALU_UID_3=<ContactGUID>; domain=.altium.com; path=/; secure; expires=+30d
Set-Cookie: ALU_USO_2=; expires=Thu, 01 Jan 1970 00:00:00 GMT; domain=.altium.com; path=/

{"returnUrl":"/connect/authorize/callback?authzId=..."}
```

After this point the browser has the **same cookie jar** as a
successful silent re-auth (§ 14.1.3 response to
`/connect/authorize`): `idsrv.session`, `idsrv`, `ALU_SID_2`,
`ALU_LLR_2`, `ALU_UID_3`, plus the new email cookie `ALU_UID_2`.

**Step F: `GET /connect/authorize/callback?authzId=...`**

The JS reads `returnUrl` from the signIn response and navigates to it.
Now that cookies are set, the callback issues a fresh OAuth code and
302s to `/api/AuthComplete?code=...&state=<original>&iss=https://auth.altium.com`
— same shape as the silent flow in § 14.1.3 step 1.

From there the flow rejoins the silent path:
`/api/AuthComplete` → `/authCompleted` → JS extracts code → `POST /connect/token` → JWT.

**Where the user profile claims come from**

The post-login id_token has the **same** claims as the silent re-auth
id_token (§ 14.1.3) — `amr: ['pwd']`, `idp: local`, `sub`, `email`,
`organization_id`, etc. `idp: local` confirms this is Altium's native
user database, distinct from what a federated Google/Facebook login
would produce (those would presumably set `idp` to `google` /
`facebook` and have a different `sub` namespace).

#### 14.1.7 Logout

Reverse-engineered from `login.har` entries 393–398. Logout is also a
multi-step ceremony, mixing the OIDC standard endpoint and an
IdentityServer 4 SPA.

**Step A: `GET /connect/endsession?soft=1&closeTab=1`**

Standard OIDC end-session endpoint. Carries the current session
cookies. Server returns 302 to
`/oidc-server/signout?logoutId=<opaque>`.

`soft=1` and `closeTab=1` are Altium-specific hints for the UI
(whether to do a "soft" logout that clears client-side state without
breaking other tabs, and whether to auto-close the browser tab after
signout). Neither affects what gets cleared on the server.

**Step B: `GET /oidc-server/signout?logoutId=...`**

Returns an HTML stub that loads `auth-module.js` and `/api/config`.
The `logoutId` is an opaque IdentityServer 4 reference to the
in-flight logout operation.

**Step C: `POST /api/account/oidcServerSignOut`**

Driven by the JS. This is the call that actually clears server-side
state.

```
POST https://auth.altium.com/api/account/oidcServerSignOut
Content-Type: application/json-patch+json

{"logoutId": "<opaque from step A>"}
```

Response:

```json
{"frontChannelLogoutUrls": []}
```

Plus a burst of cookie-clearing `Set-Cookie` headers that expire the
session cookies:

```
Set-Cookie: idsrv=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/
Set-Cookie: mp_9b01c5af..._mixpanel=; expires=Thu, 01 Jan 1970 ...; domain=.altium.com
Set-Cookie: __q_state_9Ye7VbKcmoDFGWrj=; expires=Thu, 01 Jan 1970 ...; domain=.altium.com
```

Note: **`ALU_SID_2`, `ALU_UID_3`, `ALU_LLR_2` are NOT cleared by the
server.** They persist after logout. This means "logout" in Altium
clears the IdentityServer 4 server session (`idsrv*` cookies) but
leaves the A365 remember-me layer intact. On the next visit to
`/connect/authorize`, the server will still see a stale `ALU_SID_2`
but no `idsrv.session`, which forces the user through the login form
again. This is a surprise — if you're building a logout flow in a
custom client, you probably want to expire those cookies yourself
to prevent stale JWT reuse.

`frontChannelLogoutUrls: []` would list front-channel logout URLs to
iframe if any relying party (e.g. a third-party app integrating A365)
had registered one. Empty in this capture.

#### 14.1.8 `authzId` (server-side state stashing)

The opaque `authzId` parameter deserves a separate note because it
changes how you'd write a custom client.

When `/connect/authorize` redirects to the login form, all of the
authorize-request parameters (`client_id`, `redirect_uri`, `scope`,
`state`, `code_challenge`, `code_challenge_method`) are stashed
server-side behind an opaque handle:

```
authzId = <64-hex>_<unix-timestamp>
```

All of the subsequent form calls (`/api/userContext/current`,
`/api/account/signIn`, `/connect/authorize/callback`) only pass the
`authzId` + a timestamp — they never re-submit the original
parameters. The server looks up the stashed request by `authzId`,
verifies the timestamp hasn't expired, and the flow proceeds.

Implications for a custom client:

- You **cannot** modify the authorize parameters between
  `/connect/authorize` and `/connect/authorize/callback`. In
  particular, the PKCE `code_challenge` is locked in on the first
  request; the `code_verifier` you send to `/connect/token` at the end
  must match.
- The `authzId` is **single-use** (or at least single-pass). Replaying
  it with a stale code doesn't work.
- The `_<timestamp>` suffix implies time-boxed expiry. A long-idle
  login form will need to restart at `/connect/authorize`.

#### 14.1.9 Still open

Everything about the standard password + PKCE flow is now captured.
What's still open:

- **WebAuthn / FIDO2 sign-in.** `/api/userContext/current` advertises
  `webAuth: {enabled: true}` but the actual passkey ceremony
  endpoints (`/api/account/webAuthnBeginSignIn` /
  `webAuthnCompleteSignIn` or similar) are not exercised. Needs a
  capture of a user signing in with a physical security key or
  platform authenticator.
- **Federated IdP flows** (Google / Facebook / others). We see the
  pre-assembled Google and Facebook `returnUrl`s in
  `/api/userContext/current`, but the actual browser bounce through
  the external IdP and the `/oauth/callback/google` (or `/facebook`)
  landing endpoint on the Altium side are not captured.
- **The delivery side of `actionwait.altium.com/await`.** We see the
  pickup (desktop polling with state UUID) but not the deliver
  (browser posting the stashed code). Probably just `POST /await` with
  a different body shape like
  `{"token": "<state>", "data": {...}}`.
- **Refresh tokens.** The client does not request `offline_access`,
  so there is no refresh token. Silent re-auth via `ALU_SID_2` is
  the only long-running option.
- **The JWKS URL.** Standard OIDC issuers expose their public keys at
  `/.well-known/openid-configuration` → `jwks_uri`. Not probed in any
  capture but should be trivially present.

The happy-path (fresh login, silent re-auth, PKCE exchange) is
sufficient to build a headless client for any user with
password-based login enabled. Federated-IdP users are out of reach
until one of those flows is captured.

---

This JWT is the only credential used on the *global* services in §§ 14.2
through 14.4. It does **not** work on regional / workspace services —
those require the short `AFSSessionID` minted in § 14.5.

### 14.2 `portal365.altium.com/?cls=soap`  (portal SOAP)

The portal is a SOAP service at the literal path `/` on
`portal365.altium.com`, with `?cls=soap` (same transport-class convention
as the workspace vault SOAP in § 11). The `SOAPAction` header is unquoted
and uses bare operation names (no namespace, so not the `tempuri.org`
style — even though the inner `xmlns` of the body element still is
`http://tempuri.org/`).

Operations observed, in bootstrap order:

| # | Op | Purpose |
|---|---|---|
| 1 | `GetPRT_GlobalServiceUrl` | Look up a service URL by `<ServiceName>` + `<SetName>` |
| 2 | `GetPRT_Settings` | Fetch portal settings |
| 3 | `LoginBySession` | Validate the JWT, return account/user info |
| 4 | `LeaseOnDemandLicense` | Acquire an Altium Designer license lease |
| 5 | `GetPRT_ContactDetails` | User contact record |
| 6 | `GetAccountDetails` | Account (organisation) record |
| 7 | `GetAllowedA365Features` | Feature flags for A365 subscription |
| 8 | `GetLIC_AvailableLicenses_ForContactAD16Plus` | License catalog for the contact |
| 9 | `ValidateLicenseLease` | Confirm the lease took effect |

#### 14.2.1 `GetPRT_GlobalServiceUrl`

```xml
<GetPRT_GlobalServiceUrl xmlns="http://tempuri.org/">
  <Handle/>                 <!-- empty or the JWT -->
  <ServiceName>CiivaApi</ServiceName>
  <SetName>Secure</SetName>
</GetPRT_GlobalServiceUrl>
```

Response:

```xml
<GetPRT_GlobalServiceUrlResponse xmlns="http://tempuri.org/">
  <ServiceURL>https://api3.ciiva.com</ServiceURL>
  <Message i:nil="true"/>
</GetPRT_GlobalServiceUrlResponse>
```

This is the *portal-level* service directory — it resolves a
(`ServiceName`, `SetName`) pair to a URL. The capture only shows
`ServiceName=CiivaApi, SetName=Secure` → `https://api3.ciiva.com`
(Ciiva is Altium's parts database service). Other service names are not
exercised here; the per-workspace endpoint directory is obtained instead
from the workspace `servicediscovery` `Login` call (§ 12.2 / § 14.5).

#### 14.2.2 `LoginBySession`

```xml
<LoginBySession xmlns="http://tempuri.org/">
  <SessionID>{JWT}</SessionID>
</LoginBySession>
```

Response:

```xml
<LoginBySessionResponse xmlns="http://tempuri.org/">
  <LoginResult>
    <Success>true</Success>
    <SessionHandle>{JWT, echoed back unchanged}</SessionHandle>
    <UserName>user@example.com</UserName>
    <FirstName>...</FirstName>
    <LastName>...</LastName>
    <FullName>...</FullName>
    <ContactGUID>95921344-1622-4BEC-8CBE-DB9961995230</ContactGUID>
    <Email>...</Email>
    <LastLoginDate>2026-03-17T20:21:15Z</LastLoginDate>
    <PasswordExpired>false</PasswordExpired>
    <ProfilePicture><Small/><Medium/><Large/><Full/></ProfilePicture>
    <Parameters>
      <Parameter><Name>AccountId</Name><Value>95A5B1DE-4038-4009-80DA-57B01FB471C9</Value></Parameter>
      <Parameter><Name>Account</Name><Value>Atopile</Value></Parameter>
      <Parameter><Name>AutoSync</Name><Value>False</Value></Parameter>
      <Parameter><Name>IDSHostName</Name><Value>I-0C8402ADF801C</Value></Parameter>
      <Parameter><Name>IsActive</Name><Value>True</Value></Parameter>
      <Parameter><Name>ActivationStatus</Name><Value>Active</Value></Parameter>
      ... (empty/nil fields omitted)
    </Parameters>
  </LoginResult>
</LoginBySessionResponse>
```

**Important**: `SessionHandle` in the response is the **same JWT string**
the client sent in. `LoginBySession` does **not** mint a new token — it
validates the JWT and returns user info. The real short-session minting
happens later in § 14.5.

The `ContactGUID` matches the JWT's `sub` claim. The `AccountId` here is
a *portal-level* account GUID (`95A5B1DE-...`) and is distinct from both
the workspace GUID (`ECC90F5C-...`) and the contact GUID.

#### 14.2.3 `LeaseOnDemandLicense`

```xml
<LeaseOnDemandLicense xmlns="http://tempuri.org/">
  <Handle>{JWT}</Handle>
  <ProductLineGUID>09D0A56F-4AC4-4159-9D7F-CE64A7A29346</ProductLineGUID>
  <LicenseAssignmentGUID>5CA03B79-DB09-441C-A080-63C4DB4198AE</LicenseAssignmentGUID>
  <Version>26.3.0.5</Version>
  <Borrowed>false</Borrowed>
  <NodeDetails>MAC&lt;02640AF14958&gt;DRV&lt;&gt;</NodeDetails>
  <LeaseDuration>1899-12-30T04:00:00.000Z</LeaseDuration>
  <LeaseKind>USE</LeaseKind>
</LeaseOnDemandLicense>
```

Notes:

- `NodeDetails` is a freeform string that encodes host fingerprints
  (`MAC<...>DRV<...>`). It identifies the installation for license
  enforcement.
- `LeaseDuration` `1899-12-30T04:00:00.000Z` is the Delphi "zero date"
  literal — it means "server default". The server actually assigns a
  real TTL in the response.
- `Borrowed=false` vs. `true` distinguishes online leases from offline
  "license borrow" flows.
- `LeaseKind=USE` is the normal interactive lease.

Response is a big opaque `<EncryptedData>` blob (base64) inside a
`<LeasedLicense>` wrapper. The license is presumably validated on the
client side via embedded cryptography — not reverse-engineered here.

### 14.3 `vault.api.altium.com/?cls=soap`  (central vault record)

One operation used: `GetALU_VaultRecord` (documented in § 11.9). This
call is made against the **central cross-workspace** vault
(`vault.api.altium.com`) rather than any specific workspace. It returns
the Altium Content Vault record with its global capability flags
(`RawSearchSupport=True`, `DataSheetsSupport=True`, `Comments20Support=True`,
...).

Auth is the JWT in `<SessionHandle>` inside the SOAP body.

### 14.4 `workspaces.altium.com`  (workspace listing — the slug mapper)

```
POST https://workspaces.altium.com/workspaceexternalservices/WorkspaceHelperService.asmx
SOAPAction: http://tempuri.org/GetUserWorkspaces
Content-Type: text/xml; charset="utf-8"
User-Agent: Altium Designer Develop/26.3.0.5
```

This is the **only** call that maps a logged-in user to a list of
workspace slugs / hosting URLs. Before this call, the JWT is
workspace-agnostic (it identifies the user only, via `sub =
ContactGUID`); after this call, the client knows which
`<workspace-slug>.365.altium.com` host to talk to.

**Auth is a SOAP header, not an HTTP header.** The JWT goes inside a
`<UserCredentials>` element in the SOAP `<Header>`, stored in an
element confusingly named `<password>` — **it is not a password**, it
is the OIDC access token from § 14.1. No HTTP `Authorization`, no
cookies, no username sibling. Legacy Altium ASMX convention.

Two operations seen, both with an identical envelope shape. Full body
of `GetUserWorkspaces` (the only field that varies is the JWT):

```xml
<?xml version="1.0" encoding="utf-8"?>
<SOAP-ENV:Envelope
    xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <SOAP-ENV:Header>
    <UserCredentials xmlns="http://tempuri.org/">
      <password>{JWT access_token}</password>
    </UserCredentials>
  </SOAP-ENV:Header>
  <SOAP-ENV:Body>
    <GetUserWorkspaces xmlns="http://tempuri.org/"/>
  </SOAP-ENV:Body>
</SOAP-ENV:Envelope>
```

The `GetUserWorkspaces` request payload is otherwise empty — no
pagination, no filters. You get every workspace the user can reach.
`Content-Type` uses the literally-quoted charset (`charset="utf-8"`)
form; strict SOAP clients typically send it without quotes and both
are accepted.

#### 14.4.1 `CheckADWAccountLicense`

```xml
<CheckADWAccountLicense xmlns="http://tempuri.org/" />
```

Response: `<CheckADWAccountLicenseResult>false</CheckADWAccountLicenseResult>`
(the bool meaning is unclear; probably "does the user have a legacy ADW
— Altium Designer Workspace — entitlement", `false` in this workspace).

#### 14.4.2 `GetUserWorkspaces`  *(the workspace lister)*

```xml
<GetUserWorkspaces xmlns="http://tempuri.org/" />
```

Response — one `<UserWorkspaceInfo>` per workspace the caller can access:

```xml
<UserWorkspaceInfo>
  <workspaceid>284564</workspaceid>
  <ownerid>0</ownerid>
  <name>atopile</name>
  <createdate>2026-03-17T20:14:44</createdate>
  <status>2</status>
  <startdate>2026-03-17T20:14:19</startdate>
  <expirationdate>2126-03-17T20:14:19</expirationdate>
  <maxuser>1000</maxuser>
  <currentusercount>2</currentusercount>
  <type>1</type>
  <typename>Altium Designer Workspace</typename>
  <hostingurl>https://atopile-2.365.altium.com:443</hostingurl>
  <spacesubscriptionguid>ecc90f5c-559e-4318-9ce1-88776570b379</spacesubscriptionguid>
  <displayhostingurl>https://atopile-2.365.altium.com:443</displayhostingurl>
  <locationid>4</locationid>
  <locationname>US West (Oregon)</locationname>
  <creator>user@example.com</creator>
  <isadministrator>true</isadministrator>
  <isdefault>true</isdefault>
  <statusname>Active</statusname>
  <legacyhostingurl>https://adworkspaces-uw.altium.com:443/e0nobk5xfutiyeavkozjoee7t</legacyhostingurl>
  <owneraccountguid>95A5B1DE-4038-4009-80DA-57B01FB471C9</owneraccountguid>
  <owneruserguid>95921344-1622-4BEC-8CBE-DB9961995230</owneruserguid>
  <issecure>false</issecure>
</UserWorkspaceInfo>
```

Key fields (in enumeration order of importance to a client):

| Field | Meaning |
|---|---|
| `hostingurl` | **the workspace host** — use this as the base URL for all per-workspace calls (§§ 11–13). Here: `https://atopile-2.365.altium.com:443`. |
| `spacesubscriptionguid` | **the workspace GUID** — lowercased, dashed. This is the same string that appears as the second half of the short `AFSSessionID`, as the first path segment of every S3 URL (§ 10), and as `AccountId` in `Login` (§ 12.2). |
| `name` | workspace display name (`atopile`) |
| `locationid`, `locationname` | region (here `4` / `US West (Oregon)`) — this selects which `usw.` / `euw.` / ... regional host to use |
| `typename` | `Altium Designer Workspace` vs. other subscription types |
| `statusname` | `Active` vs. expired / suspended |
| `isdefault` | the user's default workspace |
| `isadministrator` | admin rights |
| `legacyhostingurl` | old-style ADW URL (pre-365) with a random token path — preserved for back-compat |
| `workspaceid` | numeric internal id (`284564`) — rarely useful to clients |
| `owneraccountguid`, `owneruserguid` | original creator account/user |

This is the **only** way a client discovers the workspace slug →
workspace GUID mapping. Every other call either assumes you already have
the slug (because you got it here) or uses the workspace GUID directly.

### 14.5 Workspace `servicediscovery` `Login`  (the short-session mint)

See § 12.2 for the full request/response. Its place in the bootstrap:

- **Input**: user email + `*IDSGS*<JWT>` "password" + the workspace
  host URL from § 14.4.
- **Output**: a list of 65 `(ServiceKind, ServiceUrl)` endpoints + a
  short `AFSSessionID` in `<UserInfo>/SessionId`.

After this call, the global JWT is no longer needed for normal
operations — everything (except `partcatalog/ComponentDynamicData/Get`,
which still wants the JWT in its `Options[]`) uses the short session.

### 14.6 Full list of workspace service endpoints

The `Login` response contains 65 `EndPointInfo` entries. Grouped by host:

**On the workspace host (`<workspace>.365.altium.com:443`)**:

| ServiceKind | URL path |
|---|---|
| `IDS` | `/ids/?cls=soap` |
| `VAULT` | `/vault/?cls=soap` |
| `SECURITY` | `/security/SecurityService.svc` |
| `TC2` | `/tc2/WebService.asmx` |
| `VCSSERVICE` | `/vcs/vcswebservice.asmx` |
| `DSS` | `/dss/DataStorageService.asmx` |
| `ISR` | `/isr` |
| `FeatureChecking` | `/featurechecking/FeatureCheckerService.svc` |
| `USERSUI` | `/usermanagement` |
| `PROJECTSUI` | `/designs` |
| `PARTCATALOGUI` | `/catalogmanagement` |
| `VAULTUI` | `/vaultexplorer` |
| `EDS` | `/eds/EDSService.svc` |
| `TASKS` | `/tasks/TasksService.asmx` |
| `INUSE` | `/inuse/api` |
| `SEARCHTEMPLATES` | `/SearchTemplatesService/SearchTemplatesService.asmx` |
| `BMS` | `/bms` |
| `COMMENTSUI` | `/comments` |
| `MANAGEDLIBRARIESSERVICE` | `/managedlibraries/api` |

**On the regional host (`usw.365.altium.com` — and presumably per region)**:

| ServiceKind | URL path |
|---|---|
| `PARTCATALOG` | `/partcatalog/PartCatalogService.svc` (legacy SOAP) |
| `PARTCATALOG_API` | `/partcatalog/api/` (newer REST) |
| `Components` | `/partcatalog/` |
| `SEARCH` | `/search/SearchService.asmx` (legacy SOAP) |
| `SEARCHBASE` | `/search` (base for `searchasync`, `adsearch`, ...) |
| `SETTINGS` | `/settings/SettingsService.svc` |
| `DICTIONARIES` | `/dictionaries/api` |
| `PROJECTS` | `/projects/ProjectsService.asmx` |
| `COMMENTS` | `/comments/CommentsService.asmx` |
| `COMMENTSBASE`, `ANNOTATIONS` | `/comments` |
| `CollaborationService` | `/collaboration/CollaborationService.svc` |
| `COMPARISONSERVICE` | `/comparison/api` |
| `EXPLORERSERVICE` | `/explorer/api` |
| `Library.Parts.Api` | `/libraryparts/api` |
| `Library.Components.Api` | `/librarycomponentsapi/api` |
| `InsightsRestApi` | `/insights/api` |
| `MANAGEDFLOWS` | `/managedflows` |
| `EIS` | `/eis` |
| `PLMSYNC` | `/partcatalog/api/PlmSyncService.svc` |
| `PROJECTHISTORYSERVICE` | `/projecthistory/api` |
| `LWTASKS` | `/lwtasks/api` |
| `DDS` | `/dds/api` |
| `BOMSERVICE` | `/bom/api` |
| `BOMSERVICE_AD` | `/bom/ad/api` |
| `REQUIREMENTSSERVICE` | (path seen in capture, truncated) |
| `MCADCS`, `VIEWER`, `CH`, `APPLICATIONS`, `PROJECTSREST`, `AuthService`, `ActionWait`, `PLATFORMAPI`, `PROJECTCOMPARESERVICE`, `PUSH` | (paths not yet extracted from the truncated response) |

**On other global / internal hosts** (served *from* the login response
but not on either the regional or workspace pattern):

| ServiceKind | URL |
|---|---|
| `APPREGISTRY` | `https://appregistry.api.altium.com/AppRegistryWebService.asmx` |
| `NOTIFICATIONSSERVICE` | `https://int-a365.k8s.us-west-2.prod.int-cloud.altium.com/notifications/NotificationsService.asmx` |
| `LIBRARY.MODELMETADATA.WORKER` | `https://int-a365.k8s.us-west-2.prod.int-cloud.altium.com/librarymodelmetadataworker/api` |
| `SCHEDULER` | `https://int-a365.k8s.us-west-2.prod.int-cloud.altium.com/scheduler` |
| `GITREST` | `https://afs-vcs-uw1.365.altium.com/git/api` |
| `Invitation` | `https://invitation.altium.com/InvitationService.svc` |
| `Sharing` | `https://sharingservice.altium.com` |
| `CommentsCloud` | `https://commentservice.altium.com/CommentsService.asmx` |
| `PushCloud` | `https://push.altium.com/WebService.asmx` |
| `IDSCloud` | `https://ids.api.altium.com/?cls=soap` |

The presence of `int-a365.k8s.us-west-2.prod.int-cloud.altium.com`
internal-cloud hostnames in the *public* login response is a minor but
real operational leak — they hint at the underlying K8s topology.

### 14.7 Extra dictionaries / capabilities fetched after bootstrap

Immediately after the workspace `Login`, the Designer client fetches a
burst of capability dictionaries from the regional services. They are
cheap read-only calls that return static/configuration data and can be
cached:

| Endpoint | Purpose |
|---|---|
| `GET /dictionaries/api/v1.0/dictionaries` | generic dictionary service; returned `{"items":[]}` (empty in this workspace) |
| `GET /dictionaries/api/v1.0/operations` | returned `{"hasManagement":true}` — whether the user can manage dictionaries |
| `GET /partcatalog/api/v1.0/Capabilities` | partcatalog feature flags |
| `GET /partcatalog/api/v1.0/PartExtraData/Providers` | list of extra-data providers (Octopart, etc.) |
| `GET /partcatalog/api/v1.0/PartLifecycles` | partcatalog lifecycle definitions |
| `GET /partcatalog/api/v1.0/Permissions` | partcatalog ACLs for the session |
| `GET /partcatalog/api/v1.0/PartSources` | list of configured part sources |
| `GET /partcatalog/api/v1.0/PartSources/options` | options for the part-source picker |
| `GET /partcatalog/api/v1.0/PartSources/suppliers` | suppliers list |
| `GET /partcatalog/api/v1.0/PartSources/{sourceGuid}/custom-pricing-providers` | custom pricing providers (per source) |
| `POST /settings/SettingsService.svc` `GetSetting` | per-user settings lookup (SOAP) |

Response bodies are all simple JSON or tiny SOAP payloads; they are not
documented in detail here but are easy to re-capture on demand.

### 14.8 Aux endpoints worth noting but not on the hot path

- `dams.altium.com/api/UserResources/GetUserResources` — plain JSON
  POST (with CORS preflight), called from the browser side of the
  session. `dams` = Direct Asset Management Service; this is the
  portal/UI sibling of the vault, used by the web UI for shared
  images/icons. Not needed for a headless client.
- `stat.api.altium.com` — single POST at `/`; telemetry. Can be
  ignored.
- `www.altium.com/geoip.php` + `www.altium.com/live-altium/isloggedin`
  — marketing-site shims; ignore.
- `api3.ciiva.com` — Ciiva parts database; the URL was obtained via
  `GetPRT_GlobalServiceUrl(CiivaApi, Secure)` in § 14.2.1. Ciiva is
  Altium-owned and is one of the upstream providers the partcatalog
  talks to.
- `sigma.octopart.com` — Octopart image CDN; URLs appear as
  `ProductPhotoUrl` in the partcatalog response (§ 3.2).

---

## 15. Category enumeration — `POST /search/v1.0/adsearch/querycomponenttypefacets`

A regional-host REST endpoint that returns the full component-type
taxonomy of the workspace in one call, together with per-type facet
histograms over the indexed fields. This is the call the Designer client
uses to populate the "Component Types" tree in the library browser —
and the single call a headless client should make before starting an
enumeration.

```
POST https://usw.365.altium.com/search/v1.0/adsearch/querycomponenttypefacets
Authorization: AFSSessionID <short session>
Content-Type: application/json; charset=utf-8
```

Request body:

```json
{ "ExcludeLifecycleGuids": [] }
```

That is literally the entire request. Pass a list of `LifeCycleStateGUID`
values in `ExcludeLifecycleGuids` to hide items in those states (e.g.
`Draft`) from the histogram counts.

Response:

```json
{
  "Facets": {
    "resistors\\": [
      {
        "FacetName": "Case_2FPackageDD420E8DDD8B445E911A0601BB2B6D53",
        "TotalHitCount": 173,
        "Counters": [
          { "Value": "0402", "Count": 117 },
          { "Value": "0805", "Count": 12 },
          { "Value": "0603", "Count": 10 },
          ...
        ],
        "SupportRange": false
      },
      { "FacetName": "Value_5FT_40x_5EDD420E8DDD8B445E911A0601BB2B6D53", ... },
      { "FacetName": "RoHS_20CompliantDD420E8DDD8B445E911A0601BB2B6D53", ... },
      ...
    ],
    "capacitors\\": [...],
    "integrated circuits\\amplifiers\\": [...],
    "integrated circuits\\wireless\\": [...],
    ...
  }
}
```

Observations:

- The top-level keys in `Facets` are the **full component-type paths** —
  they are lowercased and trailing-backslash-terminated. Nested paths
  like `integrated circuits\amplifiers\` and `integrated circuits\wireless\`
  appear alongside the top-level `integrated circuits\`, so a client gets
  the full tree in one shot.
- In the capture (29 top-level types): `audio`, `batteries`,
  `capacitors`, `connectors`, `crystals & oscillators`, `diodes`,
  `fuses`, `inductors`, `integrated circuits` (plus seven nested
  children), `led`, `mechanical`, `miscellaneous`, `optoelectronics`,
  `radio&rf`, `relays`, `resistors`, `sensors`, `switches`,
  `transformers`, `transistors`.
- The `FacetName` values use the same hash-suffixed field names as
  `searchasync` (§ 2) and can be cross-referenced with the field list
  in § 2's "Top-level fields per component" table.
- `Counters[].Value` is lowercased (same as the facet responses from
  `searchasync`).
- Use this to populate a category picker; then use `searchasync` (§ 2)
  with `ComponentType=<category>` filter or `GetALU_Items` (§ 11.5)
  with `ContentTypeGUID='CB3C11C4-E317-11DF-B822-12313F0024A2'` to
  actually enumerate.

---

## 16. Hosted git repositories — `afs-vcs-uw1.365.altium.com`

Reverse-engineered from `repos.har`.

Altium 365 managed projects are stored as **real git repositories** behind
a standard smart-HTTP git server. The `GITREST` endpoint advertised in the
workspace `Login` response (§ 14.6) —
`https://afs-vcs-uw1.365.altium.com/git/api` — is the JSON REST side of
this; the git protocol itself sits at
`https://afs-vcs-uw1.365.altium.com/git/{REPOSITORYPATH}.git/`.

The host is region-suffixed (`afs-vcs-uw1` = "Altium Cloud Services VCS,
US-West-1"); other regions presumably have `afs-vcs-euc1`, `afs-vcs-apse1`,
etc.

### 16.1 Authentication

**HTTP Basic** with:

| Field | Value |
|---|---|
| Username | the user's account email (e.g. `user@example.com`) |
| Password | the **short `AFSSessionID`** (§ 1 Authentication), same string that is sent as `Authorization: AFSSessionID <value>` on every other per-workspace call |

Header format, decoded from a live capture:

```
Authorization: Basic base64("user@example.com:<sessionGuid><workspaceGuid>")
```

Notes:

- This is the **only** place in any capture where the short session is
  used as a Basic-auth password instead of as an `Authorization: AFSSessionID`
  header or a `<SessionHandle>` SOAP element. The server rewrites it
  back into the session lookup internally.
- Because it's standard Basic, any git client that supports credential
  helpers (libgit2, git-cli, JGit, ...) can talk to this server without
  any Altium-specific glue — just point it at the URL with the right
  username/password.
- The same session rotation rules apply: when the short session expires
  the git calls will start returning 401.

### 16.2 URL pattern

```
https://afs-vcs-uw1.365.altium.com/git/{REPOSITORYPATH}.git/{git smart-http path}
```

Where `{REPOSITORYPATH}` comes from the project's `REPOSITORYPATH` field
returned by `FindProjects` / `GetProjectByGuid` / `GetProjectsExtByGuids`
(§ 6). In every observed project `REPOSITORYPATH` equals the project item
GUID (uppercased, dashed), so in practice:

```
https://afs-vcs-uw1.365.altium.com/git/{projectItemGUID}.git/
```

The sibling `REPOSITORYGUID` field (e.g.
`05bf7b90-fd69-4dee-ae39-3c035e2dc43c`) is a **different** identifier —
it is the backend git *store* GUID and is **shared across every project in
the workspace**. It is not part of the URL; it's probably used internally
by the REST `/git/api` layer to route multi-tenant traffic.

### 16.3 Git smart-HTTP flow

Only the **clone / fetch** side is in the capture (`git-upload-pack`).
Push (`git-receive-pack`) has not been exercised.

#### Step 1: ref discovery

```
GET /git/{projectGuid}.git/info/refs?service=git-upload-pack HTTP/1.1
Host: afs-vcs-uw1.365.altium.com
Authorization: Basic <base64>
Accept: */*
User-Agent: git/2.0 (libgit2 1.7.1)
Pragma: no-cache
```

Response (`Content-Type: application/x-git-upload-pack-advertisement`,
standard pkt-line format):

```
001E# service=git-upload-pack
0000
0117<sha1> HEAD\0multi_ack thin-pack side-band side-band-64k ofs-delta
    shallow deepen-since deepen-not deepen-relative no-progress include-tag
    multi_ack_detailed no-done symref=HEAD:refs/heads/master
    object-format=sha1 agent=git/2.45.1.windows.1
003f<sha1> refs/heads/master
0000
```

Notes on the advertisement:

- **Server identifies as `git/2.45.1.windows.1`** — a recent git running
  on a Windows host. Along with the other IIS hints throughout the
  capture this strongly suggests the backend is a Microsoft stack.
- **Default branch is `master`** (via `symref=HEAD:refs/heads/master`).
- Supported capabilities: `multi_ack`, `multi_ack_detailed`, `thin-pack`,
  `side-band` / `side-band-64k`, `ofs-delta`, shallow operations
  (`shallow`, `deepen-since`, `deepen-not`, `deepen-relative`),
  `include-tag`, `no-done`, `no-progress`, `object-format=sha1`.
  **No `v2` protocol support** is advertised in this response (the
  capture shows only v1 smart-HTTP).
- SHA-1 object format only — no SHA-256 support advertised.

#### Step 2: pack negotiation + fetch

```
POST /git/{projectGuid}.git/git-upload-pack HTTP/1.1
Host: afs-vcs-uw1.365.altium.com
Authorization: Basic <base64>
Content-Type: application/x-git-upload-pack-request
Accept: application/x-git-upload-pack-result
User-Agent: git/2.0 (libgit2 1.7.1)
```

Request body (pkt-line, standard `want` list):

```
0074want <sha1> multi_ack_detailed side-band-64k include-tag thin-pack ofs-delta
0000
0009done
```

Response (`Content-Type: application/x-git-upload-pack-result`):

```
0008NAK
0085\x02Enumerating objects: 61, done.
    Counting objects: ... (progress messages over side-band-64k channel 2)
<binary packfile bytes over side-band channel 1>
```

In the captured clone: 61 objects, ~3.8 MB of packfile content.

#### Step 3 (not observed): push

`git-receive-pack` would follow the same auth + URL shape with a
different path suffix (`/git-receive-pack`) and would presumably require
write permission on the project's `ACCESSTYPE`. No push is in any
capture, so this is unverified — in particular, whether push needs an
additional permission check beyond the short-session Basic auth is
unknown.

### 16.4 Client fingerprint

The Altium Designer client uses **libgit2 1.7.1** as its git
implementation (`User-Agent: git/2.0 (libgit2 1.7.1)`), which means the
on-wire protocol is whatever libgit2 negotiates. No Altium-specific
headers or query parameters are added on the git paths — it is plain
smart-HTTP.

### 16.5 Workflow: from project lister to git clone

```
FindProjects (§ 6)                              → ProjectExt[]
  for each ProjectExt:
    repo_path = ProjectExt.REPOSITORYPATH       (= projectItemGUID)
    url = f"https://afs-vcs-uw1.365.altium.com/git/{repo_path}.git"
    auth = (user_email, short_AFSSessionID)

git clone {url}                                  (libgit2 / git-cli / JGit)
```

Or equivalently, to just verify a repo is reachable without downloading
anything:

```
GET {url}/info/refs?service=git-upload-pack
    Authorization: Basic <base64(email:session)>
    → 200 OK + pkt-line advertisement  = repo exists & you have access
    → 401                                = session expired / no access
    → 404                                = wrong REPOSITORYPATH
```

### 16.6 Related: IDS SOAP service (`<workspace>.365.altium.com/ids/?cls=soap`)

The same `repos.har` capture also exercises two new operations on the
workspace IDS endpoint (advertised as `ServiceKind=IDS` in the `Login`
response, § 14.6):

#### `GetSessionInfo`

Empty-body introspection call — returns the *current* session's
`UserInfo` (the same shape returned by `servicediscovery/Login`, § 12.2)
without having to re-login. Useful for:

- Checking whether the short `AFSSessionID` is still valid (200 OK with
  payload = yes; 401 = no).
- Fetching the user's `GlobalUserGUID`, `IsAdmin` flag, group
  membership, and other fields that are not in the JWT.

```xml
<GetSessionInfo xmlns="http://tempuri.org/"/>
```

Response carries the full `<LoginResult>` including `<SessionId>`,
`<User>`, `<Parameters>` (same set as in § 12.2), and crucially
`<Groups>` listing e.g. `Administrators` group membership with its
`GroupId` (`36a485a4-5620-4a58-889a-89a8b94b7793` in capture).

#### `QueryUsersDetails`

Generic user lookup — given any combination of `UserIds`, `UserName`,
`FullName`, `EMail`, `AccountId`, `EmailDomain`, `UserNames`, returns
full `UserRecord[]`. Paginated via `BatchSize` + `RecordLocator`.

```xml
<QueryUsersDetails xmlns="http://tempuri.org/">
  <Filter>
    <UserName/>
    <FullName/>
    <EMail/>
    <AccountId/>
    <Exact>false</Exact>
    <UserIds><item>F8604FAE-8879-4BA0-B719-715F1F449A3B</item></UserIds>
    <Modified>0001-01-01T00:00:00</Modified>
    <GetExtraData>false</GetExtraData>
    <ActiveOnly>false</ActiveOnly>
  </Filter>
  <BatchSize>100</BatchSize>
  <RecordLocator/>
  <Sort>Default</Sort>
  <SortDirection>Asc</SortDirection>
</QueryUsersDetails>
```

The captured query resolves a `CreatedByGUID` from a project record
back to a user name (`Sample Data User`, `sample@altium.com`,
`Organisation=Atopile`, `IsActive=False`, `ActivationStatus=Inactive`).
This is how the Altium UI renders "created by" bylines for legacy
records where the underlying user is deactivated.

Response envelope shape:

```xml
<QueryUsersDetailsResponse>
  <QueryResult>
    <Done>true</Done>               <!-- false = more batches available -->
    <RecordLocator>100</RecordLocator>
    <Records>
      <item>
        <UserId>.../UserName>.../FullName>.../Email>.../Organisation>...
        <AuthType>atNative</AuthType>  <!-- also: atOIDC/atSAML? -->
        <Parameters><Records>{key-value list}</Records></Parameters>
        <Groups><Records/></Groups>
        <Spaces><Records/></Spaces>
      </item>
    </Records>
    <Total i:nil="true"/>
  </QueryResult>
</QueryUsersDetailsResponse>
```

This is the same record shape as the `<User>` element inside
`GetSessionInfo` — i.e. IDS has a single user model and exposes it via
both self-introspection (`GetSessionInfo`) and directory lookup
(`QueryUsersDetails`).

Authentication on both ops is the standard short `AFSSessionID` in the
`Authorization` HTTP header + `<SessionHandle>` in the SOAP body
(same pattern as the vault SOAP in § 11 Common envelope).

Other likely IDS ops by analogy with the naming (none exercised):
`AddUser`, `UpdateUser`, `DeactivateUser`, `ChangePassword`,
`CreateGroup`, `AddUserToGroup`, ...
