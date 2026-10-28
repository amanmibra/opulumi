---
name: opulumi
description: Move an app off a PaaS (Railway, Render, Heroku, Fly.io, DigitalOcean App Platform) onto AWS, Google Cloud or Azure, defined in Pulumi. Inventories the current platform, writes a Pulumi program in the project's language (ECS Fargate / Cloud Run / Container Apps, managed Postgres or MySQL, secrets, registry), copies secrets and data without printing them, sets up CI deploys over GitHub OIDC, and stages a reversible DNS cutover with human approval gates. Use when the user wants to leave a PaaS for a hyperscaler, set up AWS/GCP/Azure with Pulumi for an existing app, or asks for /opulumi.
---

# opulumi: PaaS to AWS, GCP or Azure with Pulumi

You are moving a running app from a PaaS to the user's own cloud account, defined in Pulumi. The
job can run for hours in the background. It is safe because of the gates below, not because of
speed. Work in phases, write down what you found, and stop at each gate for the user.

The target shape is the same on every cloud and fits most web apps on any stack (Node, Python,
Ruby, Go, Elixir, Java, PHP):

```
DNS ──► HTTPS load balancer / ingress ──► web containers
                                           worker containers (no inbound)
        scheduler ──► one-off container (cron, migrations)
        managed Postgres/MySQL (private)   managed Redis   secret store   image registry   object storage
```

| | AWS | GCP | Azure |
|---|---|---|---|
| Containers | ECS Fargate + ALB | Cloud Run (+ global LB) | Container Apps |
| Database | RDS | Cloud SQL | Flexible Server |
| Guide | [clouds/aws.md](references/clouds/aws.md) | [clouds/gcp.md](references/clouds/gcp.md) | [clouds/azure.md](references/clouds/azure.md) |

Before writing infrastructure, read [references/service-mapping.md](references/service-mapping.md)
(concept mapping, which cloud, cost), [references/pulumi.md](references/pulumi.md) (rules for any
cloud) and the chosen cloud's guide. Read only the chosen cloud's guide.

## Ground rules (never skip)

1. **Pin the identity.** Never rely on a default profile, project or subscription. Check the live
   identity (AWS account, GCP project, Azure subscription) with an explicit profile/config,
   confirm it with the user before the first `pulumi up`, and guard it in the program as the
   cloud guide describes, so Pulumi refuses anywhere else.
2. **Never print secret values.** Copy env vars from the platform straight into the cloud's secret
   store through a 0600 temp file. Log names, never values. See
   [references/secrets.md](references/secrets.md).
3. **Production is read-only to you until the user says otherwise.** You may read the old
   platform. You do not change its DNS, scale it down, or delete it without an explicit yes for
   that step.
4. **`pulumi preview` before every `pulumi up`.** Read the plan. Stop if it would replace or
   delete a database, a bucket, a vault, or anything stateful.
5. **Write a handoff file.** Keep `MIGRATION.md` in the repo up to date: what exists, what was
   verified (and how), what is blocked, the next step. If the session dies, the next agent
   resumes from that file alone.

## Show progress

The user is often away while this runs, and comes back to scroll. Print status in this shape so
they can see where things stand at a glance. Use these exact symbols; keep each line short.

At the start, after Phase 0 has enough to fill it in, print the plan:

```
opulumi ▸ plan  ·  Railway → AWS us-east-1  ·  Pulumi (TypeScript)

  ○ 0  Inventory        4 services · 1 Postgres 16 · 1 Redis · 2 domains
  ○ 1  Preflight        account 1234…9012 · quotas · budget
  ○ 2  Pulumi program   network · load balancer · web ×2 · worker · cron · Postgres · Redis
  ○ 3  Ship             image · 14 secrets · data rehearsal
  ○ 4  Verify           side hostname · CI over OIDC
  ○ 5  Cutover          final copy · DNS
```

When a phase finishes, print its line again with `✓` and what was achieved, with numbers:

```
  ✓ 2  Pulumi program   61 resources created · ~$94/mo · preview had no replaces
```

At a gate, print `⏸` and stop: `⏸ Gate B  61 resources, ~$94/mo. Reply "go" to create them.`
On a failure, `✗` with the one-line cause and what you're doing about it:
`✗ 1  Preflight   Fargate quota is 0 in us-west-1 → requested 8 vCPU, switching to us-east-1`.

When the cutover holds, finish with:

```
🎉 opulumi ▸ done  ·  app.example.com is on AWS

  services   web ×2 · worker · cron (3 schedules)
  data       Postgres 16 → RDS Multi-AZ · 48 tables · rows, migrations, sequences verified
  deploys    push to main → GitHub Actions → ECS, live revision checked
  rollback   Railway paused, not deleted · final dump in s3://…/cutover.dump
  next       raise DNS TTL · restore drill · cleanup when you say so
```

Mirror the same lines into `MIGRATION.md` so the file and the terminal agree.

## Phase 0: Inventory (read-only)

Find out what is actually running. Do not trust the config file alone; dashboards drift.

- Config in the repo: `railway.json`, `railway.toml`, `.railway/`, `render.yaml`, `Procfile`,
  `app.json`, `fly.toml`, `.do/app.yaml`, `Dockerfile`, `nixpacks.toml`, `docker-compose.yml`.
- The platform CLI, if logged in: `railway status` / `railway variables --json` (names only),
  `render services -o json`, `heroku apps:info` / `heroku config --shell` (names only),
  `fly status` / `fly secrets list`.
- For each service record: kind (web / worker / cron / static / private), start command, port,
  health check path, instance size, replica count, region, env var **names**, attached
  databases and versions, Redis, volumes and mount paths, custom domains, outbound
  dependencies (third-party APIs, webhooks that call back in).
- Find what the app assumes about its platform: `PORT`, `RAILWAY_*`, `RENDER_*`, `DYNO`,
  `FLY_*` env vars, `*.railway.internal` / `*.onrender.com` hostnames, a writable local disk,
  platform cron.
- Which cloud CLIs are installed and logged in (`aws`, `gcloud`, `az`), and whether the repo or
  the user's org already uses one.

Write the result as a table at the top of `MIGRATION.md`, with the target resource for each row.

**Gate A:** show the user the inventory, the proposed cloud (if they didn't name one, recommend
using the "Which cloud" table in service-mapping.md), the mapping, the account/project/subscription
and region, the Pulumi language, and the state backend. Get a yes before creating anything.

## Phase 1: Preflight

New accounts, projects and subscriptions fail in ways that look like code bugs: zero quotas,
blocked accounts, disabled APIs, unregistered resource providers, SKUs not offered in a region.
Run the chosen cloud's **Preflight** section before writing the program, and fix what it finds
(request quota now; it can take days).

Create a monthly budget with email alerts (80% actual, 100% actual, 100% forecast) as part of the
stack. It is free and catches mistakes the day they happen.

## Phase 2: Pulumi program

Write it in `infra/`, in the app's language when Pulumi supports it, following
[references/pulumi.md](references/pulumi.md) and the cloud guide's **Program** section. On every
cloud:

- **State:** Pulumi Cloud, or the cloud's own bucket plus key, readable by CI.
- **Network:** database and cache private; containers reachable only through the load balancer
  or ingress; no NAT gateway unless the app needs a fixed egress IP or the user asks.
- **Database:** same engine and major version as the source; encryption, deletion protection,
  7+ day backups with point-in-time restore, HA for production, `protect: true`.
- **Secrets:** referenced by ID; Pulumi never holds human-entered values.
- **Services:** one per PaaS service. Web with health checks; workers with no inbound; cron and
  release commands as scheduled/one-off jobs.
- **Logs** with retention, never infinite.

Run `pulumi preview`. **Gate B:** show the user the resource list and the estimated monthly cost
(see [service-mapping.md](references/service-mapping.md#rough-monthly-cost), line by line) before
`pulumi up`.

## Phase 3: Images, secrets, data

1. **Image:** reuse the app's Dockerfile. If the PaaS built it with Nixpacks or a buildpack,
   write a Dockerfile that reproduces that build, and test it locally with `docker build`. Build
   for the runtime's CPU architecture (`linux/amd64` unless you chose ARM).
2. **Secrets:** copy each env var into the cloud's secret store following
   [references/secrets.md](references/secrets.md). Variables that pointed at the platform
   (`DATABASE_URL`, `REDIS_URL`, internal hostnames) are not copied; Pulumi builds new ones. Keep
   app encryption keys (`SECRET_KEY_BASE`, `APP_KEY`, Fernet keys) identical, or stored encrypted
   data becomes unreadable.
3. **Data, first pass:** dump the old database and restore it into the new one to prove the path
   works, using [references/data.md](references/data.md). For Postgres, `scripts/pg_move.py` does
   preflight, dump, restore and a verify that compares every table's row count, the migration
   ledger and every sequence. This is a rehearsal; the real copy is in Phase 5.

## Phase 4: Verify on a side hostname

Bring the services up behind a hostname nobody uses yet (the load balancer's own address, or
`cloud.example.com`). Check, and write in `MIGRATION.md` how each was checked:

- health endpoint returns 200 over HTTPS
- sign in works (OAuth redirect URLs and cookie domains often need the new hostname added)
- a write path works end to end, and a background job runs on the worker
- cron fires once (trigger it manually)
- logs arrive; no secret values in them

Then set up CI per [references/ci.md](references/ci.md) and the cloud guide's **CI** section:
GitHub Actions signs in over OIDC (no long-lived keys), builds and pushes the image, runs
`pulumi up`, and checks that the new revision is the one serving. A green build is not a deploy.

**Gate C:** the user clicks around the side hostname and says it is good.

## Phase 5: Cutover

Follow [references/cutover.md](references/cutover.md) and the cloud guide's **Cutover target**.
In order:

1. A day before: lower the DNS TTL on the app's records to 60s.
2. Put the old app in maintenance mode or scale its writers to zero. Tell the user the window.
3. Final dump, restore, verify. Do not continue if verify fails.
4. **Gate D:** the user approves the DNS change. Point DNS at the new entry point.
5. Watch logs, error rate and health for 30 minutes. Smoke test again on the real domain.
6. Rollback stays one DNS change away: leave the old platform paused, not deleted, for at
   least a week. Data written after cutover will not exist there; say so.

## Phase 6: Cleanup (only on request)

After the user confirms the move held, list what to retire on the old platform (services,
databases, volumes, domains, paid add-ons) and its config files in the repo. Delete only what
the user approves, and take one last backup of the old database first. Update the README and
`MIGRATION.md` to describe the new setup as the only one.

## When things go wrong

[references/gotchas.md](references/gotchas.md) has the failures every cloud hits (ports, proxies,
health checks, sequences, keys, cron timezones); each cloud guide ends with its own.
