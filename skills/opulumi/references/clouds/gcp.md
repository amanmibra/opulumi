# Google Cloud

Target: Cloud Run (services, worker pools, jobs), Cloud SQL, Memorystore, Secret Manager,
Artifact Registry, Cloud Scheduler, a global external Application Load Balancer for the custom
domain. Provider: `@pulumi/gcp`.

Cloud Run is the closest thing on any hyperscaler to the PaaS experience: deploy an image, get
HTTPS, scale by requests. Choose GCP when the user wants that feel, or already lives in Google.

## Preflight

- Pin the project: `gcloud config get-value project` and `gcloud auth list`; set `gcp:project`
  in stack config and compare with `gcp.organizations.getClientConfig()` in the program.
- Billing enabled on the project: `gcloud billing projects describe <project>`.
- APIs are off by default. Enable them in the program with `gcp.projects.Service` and make every
  resource depend on its API: `run`, `sqladmin`, `artifactregistry`, `secretmanager`,
  `cloudscheduler`, `compute`, `servicenetworking`, `redis` (if Memorystore), `iam`,
  `iamcredentials`, `sts`, `billingbudgets`.
- Org policies can block what the app needs, most often `iam.allowedPolicyMemberDomains`
  (blocks `allUsers` on a public Cloud Run service) and restricted regions. Check with
  `gcloud resource-manager org-policies list --project <project>`.
- Region: one with Cloud Run, Cloud SQL and Memorystore together, e.g. `us-central1` or
  `us-east4`. Check the Cloud Run CPU quota for that region.
- Budget: `gcp.billing.Budget` on the billing account, thresholds 0.8 / 1.0 actual and 1.0
  forecast. It emails billing admins; add a monitoring channel for anyone else.

## Program

**Network.** A VPC with one regional subnet. Cloud Run reaches private resources through
**Direct VPC egress** (`template.vpcAccess.networkInterfaces`) with
`egress: "PRIVATE_RANGES_ONLY"`, so internet calls keep using Cloud Run's own egress and you need
no Cloud NAT (`ALL_TRAFFIC` would need one). Cloud SQL private IP needs Private Services Access:
a reserved `/20` range (`gcp.compute.GlobalAddress`, purpose `VPC_PEERING`) and a
`gcp.servicenetworking.Connection`.

**Web.** `gcp.cloudrunv2.Service`:
- `minInstanceCount: 1` for production (cold starts otherwise); `maxInstanceCount` to cap spend.
- Container port from config; Cloud Run injects `PORT` (8080 default) and the app must bind
  `0.0.0.0:$PORT`.
- Startup and liveness probes on the health path.
- Public: `gcp.cloudrunv2.ServiceIamMember` with `allUsers` / `roles/run.invoker`, unless the
  org blocks it (then front it with IAP or the load balancer only).
- Ingress `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` once the load balancer is in front, so
  `*.run.app` can't be used to bypass it.
- CPU is throttled between requests by default. If the web process does work after responding
  (fire-and-forget jobs, websockets), set `cpuIdle: false`.

**Custom domain.** Use a global external Application Load Balancer: serverless NEG pointing at
the service, backend service, URL map, Google-managed certificate, HTTPS proxy, global forwarding
rule with a reserved IP, plus an HTTP→HTTPS redirect. Cloud Run domain mappings are limited
(not every region, no wildcard) and pointing a proxied Cloudflare record at `*.run.app` fails on
the Host header.

**Workers.** Cloud Run **worker pools** (`gcp.cloudrunv2.WorkerPool`) are made for
long-running, non-HTTP processes. If the provider version or region doesn't have them, use a
Cloud Run service with no public invoker, `minInstanceCount: 1`, `cpuIdle: false`, and a trivial
HTTP health endpoint in the worker.

**Cron and release commands.** `gcp.cloudrunv2.Job` per command; Cloud Scheduler
(`gcp.cloudscheduler.Job`) calls the job's `:run` endpoint with an OAuth token from a service
account that has `roles/run.invoker`. Set the scheduler `timeZone`. CI executes the migration job
with `gcloud run jobs execute <job> --wait` before switching traffic.

**Database.** `gcp.sql.DatabaseInstance`:
- `databaseVersion` matching the source major (`POSTGRES_16`, `MYSQL_8_0`).
- `settings.edition: "ENTERPRISE"` unless the user wants Enterprise Plus. New Postgres 16+
  instances default to Enterprise Plus, which is much more expensive and doesn't offer small tiers.
- `settings.tier` such as `db-custom-1-3840`. Shared-core tiers (`db-f1-micro`, `db-g1-small`)
  have no SLA; don't use them for production.
- `availabilityType: "REGIONAL"` for production (HA), `backupConfiguration` with
  point-in-time recovery, `ipConfiguration.ipv4Enabled: false` + `privateNetwork`.
- `deletionProtection: true` (Pulumi-side) and `settings.deletionProtectionEnabled: true`
  (API-side), plus `protect: true`.
- Users via `gcp.sql.User`, password from `@pulumi/random`, stored in Secret Manager as the full
  `DATABASE_URL`.

**Redis.** Memorystore for Redis or Valkey, reached over Direct VPC egress. Keep a hosted Redis if
it's only cache and the user prefers.

**Registry.** `gcp.artifactregistry.Repository` (format `DOCKER`) with cleanup policies.

**Service accounts.** One per service, with only what it uses: `secretmanager.secretAccessor` on
its secrets, `cloudsql.client`, `artifactregistry.reader`, `logging.logWriter`. Not the default
compute service account.

Preview red flags: changing a Cloud SQL instance name, `databaseVersion` major, or private
network replaces it.

## Secrets

```sh
gcloud secrets create NAME --replication-policy=automatic --data-file="$tmp" >/dev/null
```

Reference in Cloud Run with `env[].valueSource.secretKeyRef` (`secret`, `version`). Prefer a
pinned version number over `latest` so a deploy is reproducible; `latest` is read when an
instance starts, not live.

## Reaching the private database

Run the copy (`pg_move.py`, `mysqldump`) as a one-off Cloud Run job with Direct VPC egress and
the `postgres:<major>` image. Don't give the instance a public IP to copy from a laptop, even
briefly.

## CI

Workload Identity Federation: a pool, a GitHub OIDC provider with
`attributeCondition: "assertion.repository == '<owner>/<repo>'"` (and the environment), and a
deploy service account the pool can impersonate (`roles/iam.workloadIdentityUser`). In the
workflow: `google-github-actions/auth@v2` with `workload_identity_provider` and
`service_account`, then `gcloud auth configure-docker <region>-docker.pkg.dev`. Scope: push to
the repo, `run.developer` on the app's services and jobs, `iam.serviceAccountUser` on their
runtime service accounts.

Proving the deploy: `gcloud run services describe` shows `latestReadyRevisionName` equal to the
revision just created, serving 100% of traffic, and its image digest matches the build.

## Cutover target

A/AAAA records at the load balancer's reserved IP. The Google-managed certificate only issues
once DNS points at the load balancer, so expect up to ~60 minutes of cert provisioning: either
issue it beforehand on the side hostname and add the real one with a certificate map / DNS
authorization (`gcp.certificatemanager`), or accept the wait in the window. Behind Cloudflare,
use DNS authorization so the cert issues while proxied.

## Gotchas

| Symptom | Likely cause | Fix |
|---|---|---|
| `403 SERVICE_DISABLED` / `API has not been used in project` | API not enabled, or resource created before enablement finished | `gcp.projects.Service` + `dependsOn`. |
| `allUsers` binding rejected | Org policy domain restriction | Ask the org admin, or serve only through the load balancer with IAP/no-auth backend. |
| Container failed to start and listen on `PORT` | App binds 127.0.0.1 or a fixed port | Bind `0.0.0.0:$PORT`. |
| Background work stops after the response | CPU throttled between requests | `cpuIdle: false`. |
| Cloud SQL bill far above estimate | Enterprise Plus default | `edition: "ENTERPRISE"`. |
| Can't reach Cloud SQL / Redis from Cloud Run | No Direct VPC egress, or no Private Services Access | Add both; check firewall rules. |
| Outbound internet fails after adding VPC egress | `ALL_TRAFFIC` without Cloud NAT | `PRIVATE_RANGES_ONLY`, or add Cloud NAT. |
| Managed cert stuck `PROVISIONING` | DNS not at the LB yet, or CAA records exclude `pki.goog` | Point DNS, fix CAA, or use DNS authorization. |
| Requests cut at 5 minutes | Cloud Run request timeout default 300s | `timeout` up to 3600s; LB backend timeout too. |
