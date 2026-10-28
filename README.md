# opulumi

An agent skill that moves your app off a PaaS (Railway, Render, Heroku, Fly.io, DigitalOcean App
Platform) and onto your own AWS account, defined in [Pulumi](https://www.pulumi.com).

Your coding agent does the work, often for hours in the background: inventory, infrastructure,
secrets, data, CI, cutover. You approve the four steps that matter. It works with any stack the
PaaS could run (Node, Python, Ruby, Go, Elixir, Java, PHP), Postgres or MySQL, and writes Pulumi in
your app's language.

```
/opulumi move this app from Railway to AWS
```

## Why

PaaS developer experience is great until the platform has an outage and you can't do anything but
wait. AWS gives you an SLA and every knob, at the cost of a lot of setup that used to take days.
With an agent and infrastructure as code, that setup is a few hours of mostly unattended work,
and the result is a readable Pulumi program you own.

## What it does

| Phase | What happens | You approve |
|---|---|---|
| 0. Inventory | Reads `railway.json`, `render.yaml`, `Procfile`, `fly.toml`, the platform CLI. Lists every service, env var name, database, volume, cron and domain, and maps each to AWS. | **Gate A:** the plan, account, region |
| 1. Preflight | Checks Fargate quota and account status, sets a cost budget with email alerts. | |
| 2. Pulumi | Writes `infra/`: VPC without NAT gateways, ALB + ACM, ECS Fargate services, RDS, Secrets Manager, ECR, scheduled tasks. Runs `pulumi preview`. | **Gate B:** resources and monthly cost |
| 3. Images, secrets, data | Builds the image, copies secrets without printing them, rehearses the database copy and verifies it. | |
| 4. Verify | Runs the app on a side hostname, sets up GitHub Actions over OIDC with a deploy check that proves the new code is live. | **Gate C:** you click around |
| 5. Cutover | Stops writes, final copy and verify, DNS switch, 30 minutes of watching. Old platform stays paused for rollback. | **Gate D:** the DNS change |
| 6. Cleanup | Only when you ask. | each deletion |

It keeps a `MIGRATION.md` in your repo with what exists, what was verified and how, and what's
next, so a new session can pick up where the last one stopped.

## Install

**Claude Code plugin**

```
/plugin marketplace add amanmibra/opulumi
/plugin install opulumi@opulumi
```

**Copy the skill** (gives you `/opulumi`)

```sh
git clone https://github.com/amanmibra/opulumi
cp -r opulumi/skills/opulumi ~/.claude/skills/            # all projects
# or: cp -r opulumi/skills/opulumi .claude/skills/        # one project
```

**Other agents** (Codex, Cursor, etc. via [skills](https://github.com/vercel-labs/skills))

```sh
npx skills add amanmibra/opulumi
```

You'll need the AWS CLI with an SSO or IAM profile, the Pulumi CLI, Docker, `gh`, and your
platform's CLI (`railway`, `render`, `heroku` or `fly`) logged in.

## What's inside

```
skills/opulumi/
  SKILL.md                      the phases, gates and ground rules
  references/
    service-mapping.md          PaaS concept → AWS resource, and rough monthly cost
    pulumi-program.md           layout and the decisions that matter (network, ALB, ECS, RDS)
    secrets.md                  moving env vars without printing them
    data.md                     Postgres, MySQL, Redis and volumes
    ci.md                       GitHub Actions over OIDC, and proving a deploy
    cutover.md                  the runbook, with rollback
    gotchas.md                  the failures this migration hits most
  scripts/
    pg_move.py                  Postgres preflight, dump, restore, verify (rows, migrations, sequences)
    rehearse_pg.sh              tests pg_move.py against two throwaway containers
```

## Safety

- It pins the AWS account (`aws:allowedAccountIds` plus an identity check) and never uses your
  default profile.
- Secret values never reach the terminal, a log, a commit or Pulumi state.
- The old platform is read-only to the agent until you approve a change to it.
- Stateful resources get deletion protection, backups and final snapshots; a `pulumi preview`
  that would replace a database stops the run.

Read the preview anyway. It's your AWS bill.

## Cost

A web service, a worker and a small Multi-AZ Postgres come to roughly $80-100/month on AWS. That's
about the same as a paid PaaS tier. You're paying for control and reliability, not saving money.
See [service-mapping.md](skills/opulumi/references/service-mapping.md#rough-monthly-cost).

## License

MIT
