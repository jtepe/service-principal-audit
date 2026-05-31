# Azure RBAC Auditor for Service Principals

This script queries current Azure RBAC role assignments across all subscriptions (or a specific Management Group) for a set of service principals.

It is designed to run locally using your active Azure CLI credentials (`az login`), inheriting your permissions directly. It does not require a dedicated service principal or managed identity to run.

## Prerequisites

1. **Azure CLI Installed**: Install the Azure CLI (`az`) and ensure it's in your PATH.
2. **Log In**: Run `az login` to log in with your credentials.
3. **Permissions**:
   - **Azure RM Scope**: You must have read permissions (like `Reader`, `Security Reader`, or equivalent) at the **Management Group** level containing the subscriptions you wish to search.
   - **Entra ID Scope** (Only needed when querying by Tag): You must have a role like **Global Reader** or **Directory Readers** in your Entra ID tenant to read service principal tags.

## Usage

The script is located in `audit_rbac.py`. Run it with `--help` to view all parameters:

```bash
uv run audit_rbac.py --help
```

### Option A: Querying Service Principals from a Terraform State File

If you have a local `.tfstate` JSON file (or retrieve it via `terraform state pull > state.json`), the script will parse it to find all service principal resource definitions and extract their Object IDs, client IDs, and names:

```bash
uv run audit_rbac.py --state-file path/to/terraform.tfstate --output audit-results.json
```

### Option B: Querying Service Principals by Entra ID Tag

If you cannot access the state file directly, but the service principals are tagged in Entra ID (e.g. with tag `"terraform-iac"`), you can query them dynamically:

```bash
uv run audit_rbac.py --tag terraform-iac --output audit-results.json
```

### Option C: Specifying Service Principals Directly

To skip both the Terraform state and the Entra tag lookup, pass the service principal Object IDs (or Application/Client IDs) directly with `--service-principal`. The flag can be repeated to audit multiple principals in one run:

```bash
uv run audit_rbac.py \
  --service-principal 22222222-2222-2222-2222-222222222222 \
  --service-principal 33333333-3333-3333-3333-333333333333 \
  --output audit-results.json
```

The script will attempt to enrich each entry with its display name and Application ID via Microsoft Graph. If the lookup fails (e.g. you do not have Entra read permission), the identifier is used as-is as an Object ID and the audit still runs.

### Optional: Scope to a Specific Management Group

If you have access to multiple Management Groups or want to limit the search scope to a specific Management Group hierarchy, use the `--management-group` flag:

```bash
uv run audit_rbac.py --tag terraform-iac --management-group "mg-production"
```

## Output Structure

The script outputs a JSON document sorted by Service Principal display name. Each entry represents a service principal and contains a list of its assigned role assignments at any scope:

```json
[
  {
    "displayName": "app-frontend-sp",
    "applicationId": "11111111-1111-1111-1111-111111111111",
    "objectId": "22222222-2222-2222-2222-222222222222",
    "roleAssignmentsCount": 2,
    "roleAssignments": [
      {
        "subscriptionName": "Production Sub 1",
        "subscriptionId": "33333333-3333-3333-3333-333333333333",
        "scopeType": "Subscription",
        "scope": "/subscriptions/33333333-3333-3333-3333-333333333333",
        "roleName": "Reader"
      },
      {
        "subscriptionName": "Production Sub 2",
        "subscriptionId": "44444444-4444-4444-4444-444444444444",
        "scopeType": "Resource Group",
        "scope": "/subscriptions/44444444-4444-4444-4444-444444444444/resourceGroups/app-rg",
        "roleName": "Contributor"
      }
    ]
  }
]
```

## Rendering the Report as HTML

The companion script `render_html.py` converts the JSON report produced by `audit_rbac.py` into a single self-contained HTML file. It embeds the report data plus its own CSS and JavaScript, so the output can be opened directly in a browser or shared as a single file (e.g. as an email attachment or a Gist) without any external assets.

### Prerequisites

- Python 3.10 or newer. No third-party packages are required; the script only uses the standard library.
- A JSON report previously produced by `audit_rbac.py` (see above).

### Usage

```bash
uv run render_html.py audit-results.json --output audit-results.html
```

Optional flags:

- `--output / -o`: Path for the generated HTML file (default: `audit-results.html`).
- `--title`: Override the `<title>` tag of the generated document.

### What the HTML Contains

For each service principal the page renders one section, with alternating background colors between adjacent sections. Each section is split into three parts:

1. **Service principal header** with the display name, the Client (Application) ID, and the Object ID.
2. **One block per subscription** in which the principal has role assignments, showing the subscription name and ID.
3. **A list of permissions** within that subscription, each entry showing the role name, the scope type (Subscription / Resource Group / Resource), and the full scope string.

A sticky search input at the top of the page filters the visible service principals by display name. The match is case-insensitive: each whitespace-separated token in the query must appear as a substring of the name, in the order given. For example, `data` matches `sp-Data-pipeline` but not `sp-ata-infra`, and `sp app-` matches `sp-terraform-app-gateway`. Press `/` from anywhere on the page to focus the input, and `Esc` to clear it.

## How It Works Under the Hood

1. **Service Principal Extraction**: 
   - Under `--state-file`, the script parses the state JSON for `"type": "azuread_service_principal"`.
   - Under `--tag`, the script makes a GET request to the Microsoft Graph API `https://graph.microsoft.com/v1.0/servicePrincipals` using `az rest`, handling OData pagination dynamically.
   - Under `--service-principal`, the script uses the supplied IDs directly and performs a best-effort Graph lookup per ID to resolve display name and Application ID.
2. **Batching**: Since query length has size limits, the script chunks the ~500 service principal Object IDs into blocks of 100 and issues separate Azure Resource Graph queries. Each chunk is paginated via `--skip-token` (page size 1000) so result sets larger than a single page are retrieved completely.
3. **Azure Resource Graph (ARG) Querying**: Runs a fast, tenant-wide Kusto query using the `authorizationresources` and `resourcecontainers` tables to resolve assignments at all scopes (Subscription, Resource Group, and Resource level), along with friendly Role names and Subscription names.
4. **Aggregation**: The script builds a unified view and outputs it as JSON.
