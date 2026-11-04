#!/usr/bin/env bash
set -euo pipefail

if [[ $# != 5 ]]; then
    printf '%s\n' \
        'Usage: bash bootstrap.sh SUBSCRIPTION LOCATION STATE_RESOURCE_GROUP STORAGE_ACCOUNT OWNER/REPO' \
        'Creates state storage, environment resource groups and four scoped OIDC identities.' \
        'Requires explicit approval.'
    exit 1
fi

subscription=$1
location=$2
state_group=$3
storage_account=$4
repository=$5
[[ "$repository" =~ ^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$ ]]
[[ "$storage_account" =~ ^[a-z0-9]{3,24}$ ]]

az account set --subscription "$subscription"
az group create --name "$state_group" --location "$location" \
    --tags project=isa9000 env=shared managedBy=manual --output none
az storage account create --name "$storage_account" --resource-group "$state_group" \
    --location "$location" --sku Standard_LRS --kind StorageV2 --https-only true \
    --min-tls-version TLS1_2 --allow-blob-public-access false --allow-shared-key-access false \
    --tags project=isa9000 env=shared managedBy=manual --output none
az storage account blob-service-properties update --account-name "$storage_account" \
    --resource-group "$state_group" --enable-versioning true \
    --enable-delete-retention true --delete-retention-days 30 \
    --enable-container-delete-retention true --container-delete-retention-days 30 --output none
state_scope=$(az storage account show --name "$storage_account" \
    --resource-group "$state_group" --query id --output tsv)
tenant=$(az account show --query tenantId --output tsv)
assignable_roles='ba92f5b4-2d11-453d-a403-e96b0029c9fe, 2a2b9908-6ea1-4ae2-8e65-a410df84e7d1,
3913510d-42f4-4e42-8a64-420c390055eb, 69a216fc-b8fb-44d8-bc22-1f3c2cd27a39,
4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0, a97b65f3-24c7-4388-baec-2e87135dc908,
4633458b-17de-408a-b874-0445c86b69e6'
rbac_condition="(
    !(ActionMatches{'Microsoft.Authorization/roleAssignments/write'}) OR (
        @Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId]
            ForAnyOfAnyValues:GuidEquals {$assignable_roles} AND
        @Request[Microsoft.Authorization/roleAssignments:PrincipalType]
            ForAnyOfAnyValues:StringEqualsIgnoreCase {'ServicePrincipal'}
    )
) AND (
    !(ActionMatches{'Microsoft.Authorization/roleAssignments/delete'}) OR (
        @Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId]
            ForAnyOfAnyValues:GuidEquals {$assignable_roles} AND
        @Resource[Microsoft.Authorization/roleAssignments:PrincipalType]
            ForAnyOfAnyValues:StringEqualsIgnoreCase {'ServicePrincipal'}
    )
)"

for environment in dev test prod preview; do
    identity="isa9000-deploy-$environment"
    az identity create --name "$identity" --resource-group "$state_group" --location "$location" \
        --tags project=isa9000 env="$environment" managedBy=manual --output none
    principal=$(az identity show --name "$identity" --resource-group "$state_group" \
        --query principalId --output tsv)
    client=$(az identity show --name "$identity" --resource-group "$state_group" \
        --query clientId --output tsv)
    if [[ "$environment" == preview ]]; then
        target_environment=dev
        state_role='Storage Blob Data Reader'
    else
        target_environment=$environment
        state_role='Storage Blob Data Contributor'
        az group create --name "rg-isa9000-$environment" --location "$location" \
            --tags project=isa9000 env="$environment" managedBy=manual --output none
        if [[ "$environment" == prod ]]; then
            az lock create --name isa9000-prod-delete-protection --lock-type CanNotDelete \
                --resource-group rg-isa9000-prod --output none
        fi
        az storage container-rm create --storage-account "$storage_account" \
            --resource-group "$state_group" --name "pulumi-$environment" \
            --public-access off --output none
    fi
    subject="repo:$repository:environment:$environment"
    deploy_scope="/subscriptions/$subscription/resourceGroups/rg-isa9000-$target_environment"
    az identity federated-credential create --name github --identity-name "$identity" \
        --resource-group "$state_group" --issuer https://token.actions.githubusercontent.com \
        --subject "$subject" --audiences api://AzureADTokenExchange --output none
    if [[ "$environment" == preview ]]; then
        az role assignment create --assignee-object-id "$principal" \
            --assignee-principal-type ServicePrincipal --role Reader \
            --scope "$deploy_scope" --output none
    else
        az role assignment create --assignee-object-id "$principal" \
            --assignee-principal-type ServicePrincipal --role Contributor \
            --scope "$deploy_scope" --output none
        az role assignment create --assignee-object-id "$principal" \
            --assignee-principal-type ServicePrincipal \
            --role 'Role Based Access Control Administrator' --scope "$deploy_scope" \
            --condition "$rbac_condition" --condition-version 2.0 --output none
    fi
    az role assignment create --assignee-object-id "$principal" \
        --assignee-principal-type ServicePrincipal --role "$state_role" \
        --scope "$state_scope/blobServices/default/containers/pulumi-$target_environment" \
        --output none
    printf '%s\n' "$environment AZURE_CLIENT_ID=$client" "$environment PRINCIPAL_ID=$principal" \
        "$environment PULUMI_BACKEND_URL=azblob://pulumi-$target_environment"
done

printf '%s\n' "AZURE_TENANT_ID=$tenant" "AZURE_SUBSCRIPTION_ID=$subscription" \
    "AZURE_STORAGE_ACCOUNT=$storage_account" \
    'Use a different PULUMI_<ENV>_CONFIG_PASSPHRASE environment secret for dev, test and prod.' \
    'Protect the preview environment with required reviewers; give it only the dev passphrase.' \
    'Use createResourceGroup=false with the environment resource groups created above.' \
    'Grant Application.ReadWrite.OwnedBy to the three deploy identities before using authApp=create.'
az resource list --tag project=isa9000 \
    --query '[].{name:name,type:type,group:resourceGroup}' --output table
