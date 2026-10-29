# Flow Steward MCP server catalog

Remote MCP servers for [Flow Steward](https://github.com/Flow-Steward/flow-steward-app).
Flow Steward reads [`index.json`](index.json) and lists these servers under
**Registry → MCP → Catalog**. A user picks a service, clicks **Install**, and the
connection form is filled in from here: they only sign in, or paste their key.

`index.json` is generated. Never edit it by hand: a GitHub Action rebuilds it from
`servers/` after every merge.

## What is listed

A server is listed when a reviewer has checked it, not because it exists. The
[official MCP Registry](https://registry.modelcontextprotocol.io) carries tens of
thousands of servers and says itself that it is *deliberately unopinionated* and
that catalogs do the curation. Each entry here records what a person verified:

- the endpoint, and the hosts it may ever point at;
- how a user signs in — OAuth (with dynamic client registration, a client
  metadata document, or a client the user registers), headers such as an API key,
  or nothing;
- for an **Official** badge, `official.evidence_url`: a page **on the vendor's
  own site** that names this exact endpoint. A Registry namespace proves who
  published a record, not that the vendor runs the server, and directory
  "official" badges can be bought — neither counts.

Only remote servers over Streamable HTTP are listed. Servers that must be
installed locally (`packages` in the Registry) are out of scope.

## One server, one folder

```
servers/<id>/
  catalog.yaml    what the reviewer checked (written by hand)
  registry.json   the Registry's record, written by scripts/registry_sync.py
  server.json     or, for a server the Registry does not carry, its record in the
                  Registry's server.json format, written here
  icon.png        square PNG, 32–512 px, at most 100 KB
```

`catalog.yaml`:

```yaml
revision: 1                       # raise it with every change to the folder
source:
  registry: com.notion/mcp        # omit for a server.json written here
title: Notion
description: Search, read, and update Notion pages and databases, and create new pages.
category: productivity            # an id from categories.json
website_url: https://www.notion.com
help_url: https://developers.notion.com/docs/get-started-with-mcp
preview_note: ""                  # optional, e.g. "Developer Preview"
keywords: [Jira, Confluence]      # optional: other names people search for (max 20)
official:
  vendor: Notion
  evidence_url: https://developers.notion.com/docs/get-started-with-mcp
remotes:
  - id: default                   # stable: Flow Steward installs by this id
    label: Notion
    url: https://mcp.notion.com/mcp   # must be a streamable-http remote of the record
    allowed_hosts: [mcp.notion.com]   # exact names or *.example.com
    field_labels:                     # optional: what the install form calls each
      Authorization: Notion API key   #   variable or header the record declares
    auth:
      mode: oauth2                # none | headers | oauth2
      client_registration: dcr    # dcr | client_metadata | user_supplied
      requested_scopes: []
      authorization_parameters: {}
      refresh_token_required: false
      setup_url: https://...      # optional: what the user prepares first
```

The same endpoint may be listed twice with different sign-in methods (see
`servers/monday`). Headers and URL variables a user fills in come from the
server record (`remotes[].headers`, `remotes[].variables`), so they are never
copied by hand. A URL whose host is a variable (`https://{api_host}/mcp`) is
allowed when every choice is inside `allowed_hosts`; when the variable is the
customer's own subdomain under the vendor's domain
(`https://{subdomain}.freshdesk.com/mcp` with `*.freshdesk.com`); or with
`user_defined_host: true` for self-hosted products, which Flow Steward then labels
as an address the user sets.

## Add a server

```bash
pip install -r requirements.txt
python scripts/registry_sync.py candidates --search notion
python scripts/registry_sync.py add com.notion/mcp --id notion --category productivity \
    --website https://www.notion.com
```

`candidates` prints the signals a reviewer weighs: whether the namespace is a
verified domain or a GitHub account, whether the endpoint and website sit on that
domain, whether it is an aggregator. `add` writes the folder with the Registry
record, an icon (the Registry's, else the vendor site's touch icon or favicon,
else the publishing GitHub organization's avatar), and a draft `catalog.yaml`
whose comments show what probing the endpoint suggested. The draft keeps
`auth.mode: TODO`, so the check refuses it until a reviewer has:

1. read the vendor's documentation and set every remote's `auth`;
2. rewritten title and description for someone choosing a service;
3. added `official` only with a vendor page naming the endpoint;
4. looked at `icon.png`.

Then open a pull request. The **Validate submission** check tells you what to fix.

A server the Registry does not carry (Google Workspace, for example) gets a
`server.json` in the Registry's format under the `io.github.flow-steward/`
namespace instead of `registry.json`.

## Registry changes

The **Registry sync** workflow reads every page of the Registry's latest-version
listing once a day and also refreshes every listed server. When a record changed
— a new version, `deprecated`, `deleted` — it
stores the new record, raises the entry's revision and opens a pull request
describing the change. A reviewer decides; nothing merges by itself:

- `deprecated` stays listed and is shown as deprecated;
- `deleted` means the Registry removed it as spam, malware or illegal content.
  The check then refuses the entry until it is removed;
- a server that disappeared from the Registry is reported, not removed.

It also proposes up to **25 pending new remote servers**, prioritizing endpoints
on their publisher's verified namespace domain. GitHub and cross-domain publishers
are included for ownership review when queue space remains. Known aggregators,
inactive records and local-only servers are excluded. This is discovery from the
MCP Registry, not a crawl of vendor websites: official MCPs absent from that
Registry still need manual research.

```bash
python scripts/registry_sync.py sync --discover --candidate-limit 25 --report report.md
```

New proposals live in `discovery/candidates/<proposed-id>/`: the untouched
`registry.json`, a draft `catalog.yaml`, and `review.json` with ownership signals
and required checks. They are **excluded from index.json**. Domain alignment is
only a priority signal: no proposal receives an Official badge or verified
authentication automatically. Discovery does not contact candidate endpoints.

Before proposing or manually adding a server, sync compares catalog IDs, Registry
names, normalized product titles and literal remote endpoints. It checks both
Registry and manual entries, including their reviewed endpoints. It also checks
against the pending batch. Host case, HTTPS port 443 and trailing slashes are
normalized; URL path case and queries are preserved. URL templates do not prove
that two customer installations are the same service. Possible matches are
held out of the queue and recorded with reasons in `discovery/duplicates.json`;
a title collision requires a reviewer to decide whether the products differ.

To accept a proposal, verify the vendor-owned page naming the exact MCP URL, set
the category, description and authentication, obtain a square PNG icon, then move
the completed folder into `servers/<id>/` (omit `review.json`). Run the catalog
tests and validator before merging. An alternative is the existing `add` command
to draft the entry and fetch its icon, followed by the same review. A subsequent
discovery run removes proposals already represented in the catalog. To reject a
proposal permanently, add its exact Registry name and a nonempty reason to
`discovery/ignored.json`, for example:

```json
{"io.github.someone/unofficial-wrapper": "Third-party wrapper, not operated by the vendor"}
```

The workflow resumes one open draft PR on a `registry-sync/review-*` branch, preserving pending
draft edits and rejections, and merges current main before scanning. A merge
conflict or incomplete Registry scan stops the run. After the PR is closed or
merged it uses a new branch, so a retained old branch cannot block future runs.
It detects new untracked
proposal files as well as updates; it never force-pushes or merges the PR.
Accepting or rejecting pending proposals frees queue slots on the next run.
If a pending source disappears from a complete Registry listing, its draft is
retained and flagged for review rather than silently erased.

Flow Steward never changes or deletes a connection a user already made because
the catalog changed. A pull request opened by the workflow's built-in token does
not start other workflows, so run **Validate submission** on it by hand (or close
and reopen it) before merging. The sync workflow runs script tests before pushing
and includes entry-validator results in the PR. Expected validation failures
(such as a deleted Registry record) still reach review; they must be resolved
before merging and publishing.

## What the check refuses

- a folder that is not lowercase letters, digits and dashes, or that has both or
  neither of `registry.json` and `server.json`;
- a title that is an identifier, a description under 40 characters, a category
  outside [`categories.json`](categories.json) (the extension catalog's list; a
  change here alone fails);
- a remote whose URL is not a Streamable HTTP remote of the record, that is not
  https, carries a query string, or can resolve to a host outside `allowed_hosts`;
- `auth.mode` other than `none`, `headers` or `oauth2`; OAuth settings on a
  non-OAuth remote; an OAuth remote whose record requires credential headers;
  authorization parameters that belong to the flow (`client_id`, `scope`, ...);
- `official` without the vendor and an https evidence page;
- a secret value in a `server.json` written here;
- a missing, non-PNG, non-square or oversized icon;
- a Registry record marked `deleted`;
- a change to a listed entry without a higher `revision`.

## What the catalog shows, and where it comes from

| Field | Source |
| --- | --- |
| Name, summary, description, category | `catalog.yaml` |
| Official badge | `official`, after review |
| Icon | `icon.png`: URL pinned to the commit that last changed it, SHA-256, size |
| Endpoints and sign-in | `catalog.yaml` `remotes`, with headers and variables from the record |
| Status | the Registry's `status` (`active`, `deprecated`) |
| Revision | `revision`: Flow Steward refuses an install against an older one |
| Added, updated | the first and the last commit that touched the folder |

## Format

`index.json` has the shape of the Registry's `GET /v0.1/servers` response —
`{"servers": [{"server": {...}, "_meta": {...}}], "metadata": {"count": N}}` — so
Flow Steward reads this catalog and a company's private MCP registry the same way.
Everything reviewed here sits in each record under
`server._meta["io.github.flow-steward/catalog"]`; the Registry's own bookkeeping
stays under `_meta["io.modelcontextprotocol.registry/official"]`. The top level
adds `schema_version` (`mcp_server_catalog/v1`), `catalog_revision`,
`published_at` and `category_labels`.

Point Flow Steward at the catalog with

```
FS_MCP_CATALOG_URL=https://raw.githubusercontent.com/Flow-Steward/mcps/main/index.json
```

## Maintainers

```bash
pip install -r requirements.txt
python -m pytest -q scripts
python scripts/build_index.py --check --base origin/main
python scripts/build_index.py          # writes index.json
```

**Publish index** commits `index.json` to `main` with the built-in `GITHUB_TOKEN`;
if `main` is protected, allow GitHub Actions to push to it. **Registry sync**
needs *Settings → Actions → General → Allow GitHub Actions to create and approve
pull requests*.
