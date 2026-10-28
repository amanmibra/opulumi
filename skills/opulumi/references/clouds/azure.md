# Azure

Target: Azure Container Apps (apps and jobs) in a VNet-integrated environment, Azure Database for
PostgreSQL / MySQL Flexible Server, Azure Managed Redis, Key Vault, Azure Container Registry,
Log Analytics. Provider: `@pulumi/azure-native` (not the older classic `@pulumi/azure`).

Choose Azure when the user's company runs on Microsoft (Entra ID, credits, enterprise agreement)
or a customer requires it.

## Preflight

- Pin the subscription: `az account show --query '{id:id,tenant:tenantId}'`; set
  `azure-native:subscriptionId` in stack config and compare with
  `azure_native.authorization.getClientConfig()` in the program.
- Register resource providers (once per subscription), or creates fail with
  `MissingSubscriptionRegistration`: `Microsoft.App`, `Microsoft.OperationalInsights`,
  `Microsoft.DBforPostgreSQL` (or `DBforMySQL`), `Microsoft.ContainerRegistry`,
  `Microsoft.KeyVault`, `Microsoft.Cache`, `Microsoft.Network`, `Microsoft.ManagedIdentity`.
  `az provider register --namespace Microsoft.App --wait`.
- Region capacity: new and free-credit subscriptions are often restricted from some Postgres
  Flexible Server SKUs or regions. Check with
  `az postgres flexible-server list-skus --location <region>` before choosing. `eastus2` and
  `westus3` are usually good.
- Quotas: vCPU cores per region for Container Apps environments.
- Budget: `azure_native.consumption.Budget` at subscription scope, notifications at 80% / 100%
  actual and 100% forecast.

## Program

**Resource group** per stack; everything lives in it.

**Network.** A VNet with: a subnet for the Container Apps environment (at least `/27`, delegated
to `Microsoft.App/environments` for a workload-profiles environment), a subnet delegated to
`Microsoft.DBforPostgreSQL/flexibleServers` for the database, and private DNS zones
(`<name>.private.postgres.database.azure.com`) linked to the VNet. Container Apps have outbound
internet by default; no NAT gateway needed unless the user wants a fixed egress IP.

**Environment.** `app.ManagedEnvironment` with the workload-profiles model (Consumption profile is
fine), VNet config, and a Log Analytics workspace with retention set.

**Web.** `app.ContainerApp`:
- `ingress.external: true`, `targetPort` from config, `transport: auto` (websockets work).
- `scale.minReplicas: 1` for production, `maxReplicas` to cap spend; HTTP scale rule.
- Liveness/readiness/startup probes on the health path.
- A **user-assigned managed identity** with `AcrPull` on the registry and
  `Key Vault Secrets User` on the vault, used for both image pulls and secret references. Create
  the role assignments before the app, or the first revision fails to pull.

**Workers.** A Container App with no ingress, `minReplicas: 1`. KEDA scale rules on the queue
(Redis list, Service Bus) are optional later.

**Cron and release commands.** `app.Job` with `triggerType: Schedule` and a cron expression
(UTC) per cron; a `Manual` job for migrations that CI starts with
`az containerapp job start` and waits on.

**Database.** `dbforpostgresql.Server` (Flexible Server):
- `version` matching the source major; `sku` such as `Standard_B1ms` (Burstable) for small apps
  or `Standard_D2ds_v5` (General Purpose).
- `highAvailability.mode: ZoneRedundant` for production. Not available on Burstable; HA needs
  General Purpose or higher.
- `backup.backupRetentionDays` 7-35; `network.delegatedSubnetResourceId` +
  `privateDnsZoneArmResourceId` for private access, `publicNetworkAccess: Disabled`.
- Extensions must be allow-listed in the `azure.extensions` server parameter
  (`dbforpostgresql.Configuration`) before `CREATE EXTENSION` works; the restore fails otherwise.
- `protect: true`, plus a `CanNotDelete` management lock
  (`authorization.ManagementLockAtResourceLevel`).

MySQL: `dbformysql.Server`, same pattern.

**Redis.** Azure Managed Redis for new deployments (Azure Cache for Redis tiers are being
retired). Private endpoint into the VNet. Or keep a hosted Redis.

**Registry.** `containerregistry.Registry`, Basic or Standard SKU, admin user disabled. Name is
globally unique, 5-50 alphanumerics, no dashes.

**Key Vault.** RBAC authorization mode, soft delete and purge protection on. Name globally unique,
3-24 characters.

Preview red flags: changing a Flexible Server's name, VNet/subnet, or major version (in-place
major upgrade exists but is a separate, deliberate step) replaces it. A Key Vault, once deleted,
holds its name until purged.

## Secrets

Key Vault secret names allow only letters, digits and dashes: map `STRIPE_SECRET_KEY` to
`stripe-secret-key` and keep the env var name in the Container App.

```sh
az keyvault secret set --vault-name <vault> --name stripe-secret-key --file "$tmp" >/dev/null
```

In the Container App: `configuration.secrets[]` with `keyVaultUrl` (versionless URL) and
`identity` (the user-assigned identity), then `env[].secretRef`. Container Apps reads the value
when a revision starts; restart the revision after changing a secret.

## Reaching the private database

A one-off Container Apps job in the same environment running the `postgres:<major>` image (or
`pg_move.py`). Don't open the server's firewall to a laptop IP.

## CI

A user-assigned managed identity (or Entra app registration) with a **federated credential**:
issuer `https://token.actions.githubusercontent.com`, subject
`repo:<owner>/<repo>:environment:production`. Workflow: `azure/login@v2` with `client-id`,
`tenant-id`, `subscription-id` (no client secret), `az acr login`, push, then `pulumi up` or
`az containerapp update --image`. Scope: `AcrPush` on the registry, `Contributor` on the app's
Container Apps and jobs only (not the resource group, if the user wants it tight).

Proving the deploy: `az containerapp revision list` shows the newest revision `Running`,
healthy, with 100% traffic and the new image tag.

## Cutover target

CNAME to the app's default FQDN (`<app>.<env-id>.<region>.azurecontainerapps.io`) plus a TXT
record `asuid.<host>` with the environment's custom domain verification ID. Bind the hostname,
then add the free managed certificate (two steps; the cert needs the binding first). Managed cert
validation can fail while Cloudflare is proxying; issue it with the record DNS-only, then turn
the proxy back on with SSL Full (strict).

## Gotchas

| Symptom | Likely cause | Fix |
|---|---|---|
| `MissingSubscriptionRegistration` | Resource provider not registered | `az provider register`. |
| Postgres SKU or region not available | Subscription offer restriction | Pick another region/SKU, or open a quota request. |
| First revision stuck pulling the image | Role assignment for `AcrPull` not there yet | Create the identity and role assignments first, `dependsOn` them. |
| `secretRef` fails at revision start | Identity lacks `Key Vault Secrets User`, or the vault is in access-policy mode | RBAC mode + role assignment. |
| `CREATE EXTENSION` fails on restore | Extension not in `azure.extensions` | Set the server parameter first. |
| HA option rejected | Burstable tier | General Purpose for HA. |
| Name rejected | ACR / storage / Key Vault naming rules | Alphanumeric, length limits, globally unique. |
| Environment create fails on the subnet | Subnet too small or not delegated | `/27`+ delegated to `Microsoft.App/environments`. |
| Managed cert won't issue | Missing `asuid` TXT, or Cloudflare proxy on | Add TXT; DNS-only during issuance. |
