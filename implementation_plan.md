> **Historical design note.** This document captures early design thinking and no
> longer describes the shipped tool (it predates the `{ meta, servicePrincipals }`
> envelope, the two-plane model, and the removal of Terraform/`--management-group`).
> For the current source of truth see [`README.md`](README.md) and
> [`CONTEXT.md`](CONTEXT.md). Kept for historical context only.

# Azure RBAC Permission Auditing Plan for Terraform-Managed Service Principals

This document presents a technical proposal and plan for auditing Azure RBAC permissions assigned to approximately 500 service principals across 160 subscriptions under a single Management Group.

## User Review Required

Please review the updated architecture based on your feedback:
- **Authentication**: The query will run entirely on the user's terminal after they run `az login`, inheriting the user's personal permissions. No service principal or managed identity is needed.
- **Output Format**: The script will generate a structured `audit-results.json` document written to the local directory, which can then be committed to the Git repository.
- **Nested Scopes**: The query **will** retrieve permissions assigned at all levels, including the subscription level, resource group level, and individual resource level.
- **Scale (500 SPs)**: With ~500 service principals, the query remains highly efficient and fits well within Azure Resource Graph query size limits.

## Proposed Solution Architecture

We analyzed the constraints and requirements:
1. **Scale**: ~160 subscriptions and ~500 target service principals.
2. **Execution**: Run on-demand from a local terminal using `az login`.
3. **Target Scope**: Retrieve role assignments at Subscription, Resource Group, and Resource levels.
4. **Input Source**: Extract Object IDs from Terraform state (using user storage account permissions) OR query Entra ID dynamically using tags/name-prefixes (leveraging the user's Entra Global Reader or Directory Readers role).

### How Nesting is Handled in Azure Resource Graph (ARG)
The `authorizationresources` table containing role assignments stores assignments at all scopes. The query classifies the scope of assignment:
* **Subscription Level**: e.g., `/subscriptions/12345678-1234-1234-1234-1234567890ab`
* **Resource Group Level**: e.g., `/subscriptions/.../resourceGroups/my-rg`
* **Resource Level**: e.g., `/subscriptions/.../resourceGroups/my-rg/providers/Microsoft.Storage/storageAccounts/my-storage`

### Proposed Kusto Query Language (KQL) Query
The query will be parameterized with the 500 Object IDs extracted from the input source. It resolves subscription names, role definition names, and classifies the scope of assignment:

```kusto
authorizationresources
| where type =~ 'microsoft.authorization/roleassignments'
| extend principalId = tostring(properties.principalId)
| extend roleDefinitionId = tostring(properties.roleDefinitionId)
| extend scope = tostring(properties.scope)
// Filter by target Service Principal Object IDs (populated dynamically by the script)
| where principalId in~ ('sp-object-id-1', 'sp-object-id-2', '...')
| project subscriptionId, scope, principalId, roleDefinitionId
// Join with role definitions to resolve friendly names (e.g., "Contributor")
| join kind=leftouter (
    authorizationresources
    | where type =~ 'microsoft.authorization/roledefinitions'
    | project roleDefinitionId = id, roleName = tostring(properties.roleName)
) on roleDefinitionId
// Join with subscriptions to resolve subscription names
| join kind=leftouter (
    resourcecontainers
    | where type == 'microsoft.resources/subscriptions'
    | project subscriptionId, subscriptionName = name
) on subscriptionId
// Classify the scope depth
| extend scopeSegments = split(scope, '/')
| extend scopeType = iif(array_length(scopeSegments) == 3, 'Subscription',
                        iif(array_length(scopeSegments) == 5, 'Resource Group', 'Resource'))
| project subscriptionName, subscriptionId, scopeType, scope, roleName = coalesce(roleName, split(roleDefinitionId, '/')[-1]), principalId
| order by subscriptionName asc, scopeType asc
```

---

## Step-by-Step Implementation Plan

### 1. User Permissions Requirement
To execute the script successfully:
- **Azure RM Scope**: The user executing the script must have read access to the role assignments across all subscriptions. A role like `Reader` or `Security Reader` (or custom equivalent containing `Microsoft.Authorization/roleAssignments/read` and `Microsoft.Resources/subscriptions/read` actions) at the **Management Group** level is required.
- **Entra ID Scope (If querying by tags)**: If retrieving service principals dynamically, the user needs `Directory.Read.All` or the **Global Reader** / **Directory Readers** role in the Entra tenant to read service principal tags.

### 2. Service Principal Resolution Strategies
The script will support two methods to retrieve the list of ~500 service principal Object IDs:

#### Method A: Pulling from Terraform State
If the remote state is accessible in Azure Blob Storage:
1. The script authenticates to the storage account (using the user's `az login` token with `Storage Blob Data Reader` role).
2. Downloads the state file or runs `terraform state pull`.
3. Parses the JSON to extract all service principal Object IDs (e.g., matching resource types `azuread_service_principal`).

#### Method B: Querying Entra ID by Tag (Fallback/Alternative)
If the Terraform state cannot be accessed, the script will use the user's Entra credentials to query the Microsoft Graph API for service principals.
* **Tagging requirement**: The Terraform configuration must tag the service principals during creation (e.g., adding `"terraform-iac"` to the `tags` list in the `azuread_service_principal` resource).
* **Graph API Call**: The script queries Microsoft Graph via `az rest`:
  ```bash
  az rest --method get \
    --uri "https://graph.microsoft.com/v1.0/servicePrincipals?\$filter=tags/any(c:c eq 'terraform-iac')&\$select=id,displayName,appId"
  ```
  Since the result can be paged, the script will handle pagination (using `@odata.nextLink`) to retrieve all ~500 service principals.

### 3. Execution Script Workflow (Python or Bash)
A local wrapper script (e.g. `audit_rbac.py` or `audit_rbac.sh`) will automate the process:

1. **Check Prerequisites**: Ensure the user is logged in via `az account show`.
2. **Resolve Service Principals**: 
   - Read from a local JSON TF state file, fetch from remote state, or run the Entra ID tags query to compile a dictionary mapping `objectId` to `{ displayName, appId }`.
3. **Execute ARG Query**:
   - Construct the KQL query inserting the list of `objectId`s.
   - Run the query using `az graph query -q "<KQL_QUERY>"`.
4. **Format & Write Output**:
   - Merge the ARG output with the SP dictionary to include the service principal's display name and application (client) ID in the final report.
   - Write the consolidated list to `audit-results.json` in the local directory.

---

## Verification Plan

### Automated/Local Tests
- Create a test script that targets a single test subscription or resource group to verify that:
  - The script correctly identifies a test Service Principal's Object ID (via tag or local state).
  - The KQL query correctly retrieves assignments at subscription, resource group, and resource levels.
  - The output matches the Azure Portal UI assignments exactly.
