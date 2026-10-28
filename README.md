# opulumi (Opus + Pulumi)

An agent skill that moves your app off a PaaS (Railway, Render, Heroku, Fly.io, DigitalOcean App
Platform) and onto your own **AWS, Google Cloud or Azure** account, defined in
[Pulumi](https://www.pulumi.com).

Your coding agent does the work, often for hours in the background: inventory, infrastructure,
secrets, data, CI, cutover. You approve the four steps that matter. It works with any stack the
PaaS could run (Node, Python, Ruby, Go, Elixir, Java, PHP), Postgres or MySQL, and writes Pulumi in
your app's language.

```
/opulumi move this app from Railway to AWS
/opulumi move us off Render onto GCP
/opulumi Heroku → Azure, keep Postgres 15
```

## Install

```sh
npx skills add amanmibra/opulumi
```

That's it. Open your agent in the app's repo and run `/opulumi`. Works with Claude Code, Codex,
Cursor and [other agents](https://github.com/vercel-labs/skills). Add `-g` to install for every
project instead of just this one.

<details>
<summary>Other ways to install</summary>

**Claude Code plugin** (runs as `/opulumi:opulumi`)

```
/plugin marketplace add amanmibra/opulumi
/plugin install opulumi@opulumi
```

**By hand** (runs as `/opulumi`)

```sh
git clone https://github.com/amanmibra/opulumi
cp -r opulumi/skills/opulumi ~/.claude/skills/     # every project
cp -r opulumi/skills/opulumi .claude/skills/       # or just this one
```

</details>

**You'll need:** the Pulumi CLI, Docker, `gh`, your cloud's CLI (`aws`, `gcloud` or `az`) signed
in, and your platform's CLI (`railway`, `render`, `heroku` or `fly`) logged in.

## Why

PaaS developer experience is great until the platform has an outage and you can't do anything but
wait. A hyperscaler gives you an SLA and every knob, at the cost of a lot of setup that used to take days.
With an agent and infrastructure as code, that setup is a few hours of mostly unattended work,
and the result is a readable Pulumi program you own.

## Clouds

| | AWS | Google Cloud | Azure |
|---|---|---|---|
| Containers | ECS Fargate + ALB | Cloud Run | Container Apps |
| Database | RDS | Cloud SQL | Flexible Server |
| Secrets | Secrets Manager | Secret Manager | Key Vault |
| CI auth | IAM OIDC role | Workload Identity Federation | Federated credential |
| Pick it when | Default; biggest ecosystem | You want the closest thing to a PaaS | Your company runs on Microsoft |

All three have first-class Pulumi providers. If you don't name one, the skill recommends one and
asks.

## What it does

| Phase | What happens | You approve |
|---|---|---|
| 0. Inventory | Reads `railway.json`, `render.yaml`, `Procfile`, `fly.toml`, the platform CLI. Lists every service, env var name, database, volume, cron and domain, and maps each to the cloud. | **Gate A:** the plan, cloud, account, region, budget and alerts |
| 1. Preflight | Checks quotas, account status, enabled APIs and resource providers; builds the budget, alerts and instance caps you chose. | |
| 2. Pulumi | Writes `infra/` in your app's language: private network, HTTPS entry point, web and worker containers, managed database and Redis, secrets, registry, scheduled jobs. Runs `pulumi preview`. | **Gate B:** resources and monthly cost |
| 3. Images, secrets, data | Builds the image, copies secrets without printing them, rehearses the database copy and verifies it. | |
| 4. Verify | Runs the app on a side hostname, sets up GitHub Actions over OIDC with a deploy check that proves the new code is live. | **Gate C:** you click around |
| 5. Cutover | Stops writes, final copy and verify, DNS switch, 30 minutes of watching. Old platform stays paused for rollback. | **Gate D:** the DNS change |
| 6. Cleanup | Only when you ask. | each deletion |

It prints a plan and a ✓ line per phase as it goes, ends with a 🎉 summary, and keeps a
`MIGRATION.md` in your repo with what exists, what was verified and how, and what's
next, so a new session can pick up where the last one stopped.

## What's inside

```
skills/opulumi/
  SKILL.md                      the phases, gates and ground rules
  references/
    service-mapping.md          PaaS concept → AWS / GCP / Azure, which cloud, rough monthly cost
    pulumi.md                   providers, state, layout, rules for any cloud
    clouds/aws.md               preflight, program, secrets, CI, cutover, gotchas
    clouds/gcp.md               same, for Cloud Run + Cloud SQL
    clouds/azure.md             same, for Container Apps + Flexible Server
    secrets.md                  moving env vars without printing them
    data.md                     Postgres, MySQL, Redis and volumes
    cost-controls.md            budget, alerts, caps: the questions it asks and what each cloud can do
    ci.md                       GitHub Actions over OIDC, and proving a deploy
    cutover.md                  the runbook, with rollback
    gotchas.md                  failures every cloud hits
  scripts/
    pg_move.py                  Postgres preflight, dump, restore, verify (rows, migrations, sequences)
    rehearse_pg.sh              tests pg_move.py against two throwaway containers
```

## Safety

- It pins the AWS account, GCP project or Azure subscription (checked live and guarded in the
  program) and never relies on your defaults.
- Secret values never reach the terminal, a log, a commit or Pulumi state.
- The old platform is read-only to the agent until you approve a change to it.
- It asks for a monthly budget, alert emails and instance caps up front, and is honest that no
  cloud hard-stops billing at a dollar amount.
- Stateful resources get deletion protection, backups and final snapshots; a `pulumi preview`
  that would replace a database stops the run.

Read the preview anyway. It's your cloud bill.

## Cost

For a web service, a worker and an HA Postgres, expect roughly $80-100/month on AWS, $170-250 on
GCP and $110-350 on Azure, depending mostly on the database tier. That's in the range of a paid
PaaS tier. You're paying for control and reliability, not saving money.
See [service-mapping.md](skills/opulumi/references/service-mapping.md#rough-monthly-cost).

## License

MIT
