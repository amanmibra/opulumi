# Mapping PaaS concepts to AWS

| On the PaaS | On AWS | Notes |
|---|---|---|
| Web service (Railway service with a domain, Render web service, Heroku `web` dyno, Fly app with `http_service`) | ECS Fargate service behind an ALB target group | Health check path from the PaaS config; default to `/` only if there is none. |
| Background worker (Render background worker, Heroku `worker` dyno, Railway service without a domain) | ECS Fargate service, no load balancer, security group with no inbound rules | Same image as web is fine; override the command. |
| Private service (Render private service, Railway private networking, Fly `.internal`) | ECS service registered in Cloud Map (`<name>.<app>.internal`) | Replace `*.railway.internal` / `*.onrender.com` / `*.flycast` hostnames in env. |
| Cron job (Render cron, Railway cron, Heroku Scheduler) | EventBridge Scheduler schedule → `ecs:RunTask` with a command override | Schedules are UTC unless you set a timezone. Heroku Scheduler is best-effort; Scheduler is not, so jobs that were never quite idempotent may now double-run less but fail louder. |
| Release / pre-deploy command (Heroku `release`, Render `preDeployCommand`, Railway pre-deploy) | A one-off ECS task run by CI before the service update, or migrate on startup if the app already does | Never run migrations from every web replica at once without a lock. |
| Managed Postgres / MySQL | RDS (same engine, same major version) | Aurora Serverless v2 only if the user wants it; RDS on `db.t4g` is cheaper for small apps. |
| Managed Redis / Key Value | ElastiCache (Valkey or Redis OSS), serverless or `cache.t4g.micro` | Or keep a hosted Redis (Upstash) if it was only a cache. TLS is on for serverless; clients may need `rediss://`. |
| Persistent disk / volume | S3 for files the app writes as objects; EFS if the app truly needs a POSIX path | Fargate has no persistent local disk. A volume is the most common blocker; find it in Phase 0. |
| Static site | S3 + CloudFront, or keep it where it is (Cloudflare Pages, Vercel) | Moving a static site rarely buys reliability; ask. |
| Env groups / shared variables | Secrets Manager entries under `<app>/<stack>/`, referenced by several task definitions | |
| Custom domain + automatic TLS | ACM certificate (free) on the ALB; DNS CNAME/ALIAS at the ALB | Validate the cert with one DNS record before cutover. |
| Autoscaling | ECS Service Auto Scaling on CPU or ALB request count | Start with a fixed count equal to the PaaS's; add scaling later. |
| Preview environments | A second Pulumi stack, or skip | Most small teams don't need them on day one. Ask. |
| Logs / metrics | CloudWatch Logs (set retention), Container Insights optional | Ship to the user's existing log tool if they have one. |

## Rough monthly cost

us-east-1 list prices, rounded; check current pricing before quoting a total to the user.

| Item | ≈ USD / month |
|---|---|
| Fargate task, 0.5 vCPU / 1 GB, always on (x86) | 18 (ARM about 20% less) |
| Fargate task, 0.25 vCPU / 0.5 GB | 9 |
| Application Load Balancer, light traffic | 18-22 |
| Public IPv4 address (one per task in public subnets) | 3.65 each |
| NAT gateway (avoid unless required) | 33 each + data |
| RDS `db.t4g.micro` Postgres, single-AZ / Multi-AZ, 20 GB gp3 | 14 / 28 |
| ElastiCache `cache.t4g.micro` | 12 |
| Secrets Manager | 0.40 per secret |
| CloudWatch Logs | 0.50 per GB ingested |
| ECR | 0.10 per GB stored; add a lifecycle policy |

A web + worker app with a small Multi-AZ Postgres usually lands around $80-100/month, comparable
to a PaaS's paid tier. The trade is cost for control and an SLA, not a big saving; say so plainly.
