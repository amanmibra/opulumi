# Mapping PaaS concepts to a cloud

## Which cloud

Ask the user. If they have no preference, recommend in this order:

| Cloud | Pick it when | Feels like |
|---|---|---|
| **AWS** | Default. Largest ecosystem, most answers online, predictable small-app costs. | More wiring (VPC, ALB), most control. |
| **GCP** | They want the closest thing to a PaaS: Cloud Run deploys an image and scales by request. | Closest to Railway/Render. |
| **Azure** | Their company is on Microsoft (Entra ID, credits, enterprise agreement) or a customer needs it. | Container Apps is close to Cloud Run; more naming and registration rules. |

All three have first-class Pulumi providers and managed containers, Postgres, MySQL, Redis,
secrets and registries. Oracle, IBM and Alibaba are out of scope (thinner Pulumi support, few PaaS
users). DigitalOcean and Hetzner have good providers but aren't covered here.

## Concept by concept

| On the PaaS | AWS | GCP | Azure |
|---|---|---|---|
| Web service | ECS Fargate service + ALB | Cloud Run service (+ global external ALB for the domain) | Container App with external ingress |
| Background worker | ECS service, no inbound | Cloud Run worker pool (or service with `cpuIdle: false`) | Container App, no ingress |
| Private service / internal networking | ECS + Cloud Map | Cloud Run, internal ingress | Container App, internal ingress |
| Cron job | EventBridge Scheduler → ECS RunTask | Cloud Scheduler → Cloud Run job | Container Apps job, schedule trigger |
| Release / pre-deploy command | One-off ECS task from CI | Cloud Run job executed by CI | Manual Container Apps job started by CI |
| Postgres / MySQL | RDS | Cloud SQL (Enterprise edition) | Database for PostgreSQL / MySQL Flexible Server |
| Redis / Key Value | ElastiCache (Valkey) | Memorystore | Azure Managed Redis |
| Volume / disk | S3 (EFS if a path is required) | Cloud Storage (Filestore if a path is required) | Blob Storage (Azure Files if a path is required) |
| Env vars / secrets | Secrets Manager | Secret Manager | Key Vault |
| Image registry | ECR | Artifact Registry | Container Registry |
| Custom domain + TLS | ACM cert on the ALB | Google-managed cert on the LB | Managed certificate on the Container App |
| Autoscaling | ECS Service Auto Scaling | Built in (requests/CPU) | Built in (KEDA rules) |
| Logs | CloudWatch Logs | Cloud Logging | Log Analytics |
| CI auth from GitHub | IAM OIDC role | Workload Identity Federation | Federated credential on a managed identity |
| Budget alerts | AWS Budgets | Cloud Billing budget | Cost Management budget |

Things that usually need a code change on any cloud, to flag at Gate A: writing files to local
disk, reading platform env vars (`RAILWAY_*`, `RENDER_*`, `DYNO`, `FLY_*`), hard-coded internal
hostnames, and relying on the platform to run migrations.

Static sites: moving them rarely buys reliability. Leave them where they are unless asked.

## Rough monthly cost

Ballparks for **one web service, one worker, an HA Postgres, light traffic**, at list price in a
US region. Prices change; check the cloud's calculator before quoting a total, and show the user
the line items.

| | AWS | GCP | Azure |
|---|---|---|---|
| Web (always on, ~0.5-1 vCPU) | Fargate 0.5 vCPU/1 GB ≈ $18 | Cloud Run min 1 instance, 1 vCPU ≈ $25-50 | Container App 0.5 vCPU/1 GiB ≈ $30-40 |
| Worker | ≈ $18 | ≈ $25-50 | ≈ $30-40 |
| Load balancer | ALB ≈ $18-22 | Global external ALB ≈ $18-25 | included in Container Apps |
| Postgres, HA, smallest production tier | RDS `db.t4g.micro` Multi-AZ ≈ $28 | Cloud SQL `db-custom-1-3840` regional ≈ $100 | Flexible Server GP `D2ds_v5` zone-redundant ≈ $250; Burstable `B1ms` without HA ≈ $15 |
| Public IPs / NAT | $3.65 per task IP; avoid NAT ($33) | none needed | none needed |
| Secrets, registry, logs | $5-10 | $5-10 | $5-10 |
| **Typical total** | **$80-100** | **$170-250** | **$110-350** depending on HA |

The cheap options on GCP and Azure skip HA or an SLA (shared-core Cloud SQL, Burstable Postgres).
Say which you chose and why. Leaving a PaaS is about control and reliability, not saving money.
