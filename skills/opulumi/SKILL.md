---
name: opulumi
description: Move an app off a PaaS (Railway, Render, Heroku, Fly.io, DigitalOcean App Platform) onto AWS, defined in Pulumi. Inventories the current platform, writes a Pulumi program (ECS Fargate, ALB, RDS, Secrets Manager, ECR) in the project's language, copies secrets and data without printing them, sets up CI deploys over GitHub OIDC, and stages a reversible DNS cutover with human approval gates. Use when the user wants to leave a PaaS for AWS, set up AWS with Pulumi for an existing app, or asks for /opulumi.
---

# opulumi: PaaS to AWS with Pulumi

You are moving a running app from a PaaS to the user's own AWS account, defined in Pulumi. The
job can run for hours in the background. It is safe because of the gates below, not because of
speed. Work in phases, write down what you found, and stop at each gate for the user.

The target shape fits most web apps on any stack (Node, Python, Ruby, Go, Elixir, Java, PHP):

```
DNS (Cloudflare / Route 53) ──► ALB (HTTPS, ACM cert) ──► ECS Fargate: web
                                                           ECS Fargate: worker(s), no inbound
                                                           EventBridge Scheduler ──► ECS task (cron)
                         RDS Postgres/MySQL (private subnets)    ElastiCache (if Redis)
                         Secrets Manager ──► task env at start    ECR (images)    S3 (files)
```

Read [references/service-mapping.md](references/service-mapping.md) for how each PaaS concept maps
to AWS before you write any infrastructure.

## Ground rules (never skip)

1. **Pin the AWS account.** Never use the default profile. Run `aws sts get-caller-identity` with
   an explicit `AWS_PROFILE` and confirm the account ID with the user before the first
   `pulumi up`. Put the ID in `aws:allowedAccountIds` in the stack config so Pulumi refuses any
   other account.
2. **Never print secret values.** Copy env vars from the platform straight into Secrets Manager
   through a 0600 temp file or a pipe. Log names, never values. See
   [references/secrets.md](references/secrets.md).
3. **Production is read-only to you until the user says otherwise.** You may read the old
   platform. You do not change its DNS, scale it down, or delete it without an explicit yes for
   that step.
4. **`pulumi preview` before every `pulumi up`.** Read the plan. Stop if it would replace or
   delete a database, a bucket, or anything stateful.
5. **Write a handoff file.** Keep `MIGRATION.md` in the repo up to date: what exists, what was
   verified (and how), what is blocked, the next step. If the session dies, the next agent
   resumes from that file alone.

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

Write the result as a table at the top of `MIGRATION.md`, with the AWS target for each row.

**Gate A:** show the user the inventory and the proposed mapping, the AWS account and region, the
Pulumi language, and the state backend. Get a yes before creating anything.

## Phase 1: Account preflight

New AWS accounts and less-used regions fail in ways that look like code bugs. Check first:

- Fargate On-Demand vCPU quota (code `L-3032A538`) in the target region:
  `aws service-quotas get-service-quota --service-code fargate --quota-code L-3032A538`.
  It can be `0` on a new account. If it is, request an increase now and consider another region;
  the request can take days.
- A new account can be blocked from launching anything until it is verified
  (`BlockedException`). Check the account email for verification requests.
- Pick a region with every service you need. `us-east-1` is the least surprising.
- Create a monthly AWS Budget with email alerts (80% actual, 100% actual, 100% forecast) as
  part of the stack. It is free and catches mistakes the day they happen.

## Phase 2: Pulumi program

Use the language the app is written in when Pulumi supports it (TypeScript, Python, Go, C#,
Java); otherwise YAML or TypeScript. Put it in `infra/`. Follow
[references/pulumi-program.md](references/pulumi-program.md) for the layout and the decisions that
matter. The short version:

- **State:** Pulumi Cloud (simplest) or a self-managed S3 bucket with a KMS secrets provider.
  If self-managed, leave `profile=` out of the `awskms://` URL so CI can decrypt it too.
- **Network:** VPC with two AZs. Tasks in **public subnets with public IPs**, a security group
  that only admits the load balancer. Database in private subnets with no internet route.
  This avoids a NAT gateway ($33+/month each) for apps that only make outbound calls.
- **Names:** ALB and target group names cap at 32 characters. Use a short prefix.
- **Database:** RDS with encryption, deletion protection, 7-day backups, a final snapshot on
  delete, Multi-AZ for production. Match the source's major version.
- **Secrets:** Pulumi references secrets the human copied in by ARN; it never holds their values.
  Values Pulumi generates (DB password) land in state encrypted; say so in a comment.
- **Services:** one ECS service per PaaS service. Web behind the ALB with a health check; workers
  with no inbound rule. Cron jobs as EventBridge Scheduler targets running a one-off task.
- **Logs:** CloudWatch log group per service with a retention period (14-30 days), never infinite.

Run `pulumi preview`. **Gate B:** show the user the resource list and the estimated monthly cost
(see [references/service-mapping.md](references/service-mapping.md#rough-monthly-cost)) before
`pulumi up`.

## Phase 3: Images, secrets, data

1. **Image:** reuse the app's Dockerfile. If the PaaS built it with Nixpacks or a buildpack,
   write a Dockerfile that reproduces that build, and test it locally with `docker build`. Build
   for `linux/amd64` (or set the task to ARM64 and build for that; Graviton is cheaper).
2. **Secrets:** copy each env var from the platform into Secrets Manager under
   `<app>/<stack>/<NAME>` following [references/secrets.md](references/secrets.md). Variables
   that pointed at the platform (`DATABASE_URL`, `REDIS_URL`, internal hostnames) are not
   copied; Pulumi builds new ones. Keep app encryption keys (`SECRET_KEY_BASE`, `APP_KEY`,
   Fernet keys) identical, or stored encrypted data becomes unreadable.
3. **Data, first pass:** take a dump of the old database and restore it into RDS to prove the
   path works, using [references/data.md](references/data.md). For Postgres,
   `scripts/pg_move.py` does preflight, dump, restore and a verify that compares every table's
   row count, the migration ledger and every sequence. This is a rehearsal; the real copy is in
   Phase 5.

## Phase 4: Verify on a side hostname

Bring the services up on AWS behind a hostname nobody uses yet (the ALB DNS name, or
`aws.example.com`). Check, and write in `MIGRATION.md` how each was checked:

- health endpoint returns 200 over HTTPS
- sign in works (OAuth redirect URLs and cookie domains often need the new hostname added)
- a write path works end to end, and a background job runs on the worker
- cron fires once (trigger it manually)
- logs arrive in CloudWatch; no secret values in them

Then set up CI per [references/ci.md](references/ci.md): GitHub Actions assumes a scoped role
through OIDC (no long-lived keys), builds and pushes to ECR, runs `pulumi up` or updates the ECS
service, and waits until the new task revision is running and healthy. A green build is not a
deploy; the workflow must check the live revision.

**Gate C:** the user clicks around the side hostname and says it is good.

## Phase 5: Cutover

Follow [references/cutover.md](references/cutover.md). In order:

1. A day before: lower the DNS TTL on the app's records to 60s.
2. Put the old app in maintenance mode or scale its writers to zero. Tell the user the window.
3. Final dump, restore, verify. Do not continue if verify fails.
4. **Gate D:** the user approves the DNS change. Point DNS at the ALB.
5. Watch logs, error rate and the health check for 30 minutes. Smoke test again on the real
   domain.
6. Rollback stays one DNS change away: leave the old platform paused, not deleted, for at
   least a week. Data written to AWS after cutover will not exist there; say so.

## Phase 6: Cleanup (only on request)

After the user confirms the move held, list what to retire on the old platform (services,
databases, volumes, domains, paid add-ons) and its config files in the repo. Delete only what
the user approves, and take one last backup of the old database first. Update the README and
`MIGRATION.md` to describe the new setup as the only one.

## When things go wrong

See [references/gotchas.md](references/gotchas.md) for the failures this migration hits most:
quotas, health checks that fail on the wrong path, tasks that can't pull images or read secrets,
sequences behind their max id, OAuth callbacks, `PORT`, websockets and timeouts on the ALB.
