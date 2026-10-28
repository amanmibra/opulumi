# Writing the Pulumi program

## Layout

Keep the entry point short enough to read as the whole deployment, with one module per concern.
Each module starts with a comment saying why it looks the way it does; the next person (or agent)
reading it has no other context.

```
infra/
  Pulumi.yaml                  project, runtime
  Pulumi.<stack>.yaml          config: account, region, sizes, image tags, domain
  index.ts | __main__.py | main.go
  config        read and validate config once; refuse the wrong account
  network       VPC, subnets, security groups
  data          database, cache, Secrets Manager references
  services      ECR, ALB, ECS cluster, task definitions, services, schedules
  dns           ACM certificate, and records if Route 53 holds the zone
  budget        monthly budget with email alerts
```

Use `@pulumi/aws` (classic) for most resources. `@pulumi/awsx` can shorten the VPC and Fargate
service code, but its VPC defaults to NAT gateways per AZ; set `natGateways: { strategy: "None" }`
if you use it with public-subnet tasks.

## Decisions that matter

**Account guard.** Read the expected account ID from config and compare it with
`aws.getCallerIdentity()` before registering resources, and set `aws:allowedAccountIds`. Two
guards, because a wrong `AWS_PROFILE` is the most expensive typo in this whole job.

**Stack-scoped names.** Prefix physical names with a short stack slug (`app-prod-`). ALB and target
group names are limited to 32 characters and Pulumi appends a 7-character suffix, so keep the
prefix under ~20.

**Network without NAT.**
- 2 AZs. Public subnets for tasks (`assignPublicIp: true`), private subnets for RDS/ElastiCache.
- Task security group: inbound only from the ALB security group on the container port.
- Database security group: inbound only from the task security group on 5432/3306.
- Private subnets have no route to the internet gateway.
- If the user needs tasks unreachable by routing (compliance), use private subnets plus a NAT
  gateway or VPC endpoints instead, and tell them the cost.

**Load balancer.**
- Listener 80 redirects to 443. Listener 443 uses the ACM certificate.
- Target group type `ip`, health check on the app's health path, matcher `200-399`.
- Lower `deregistration_delay` to ~30s and set the ALB idle timeout above the app's longest
  request (and above websocket keepalive) so deploys are fast and long requests survive.
- Enable access logs to an S3 bucket with a lifecycle rule if the user wants request history.

**Task definitions.**
- `secrets` entries with `valueFrom` set to the Secrets Manager ARN; plain `environment` only for
  non-sensitive values.
- Execution role: pull from ECR, write logs, read exactly `arn:...:secret:<app>/<stack>/*`.
- Task role: only what the app calls at runtime (S3 bucket, SES, etc.). Separate from execution.
- Image tag from config (set by CI per deploy), never `latest`.
- Set `PORT` explicitly; many apps were written against the PaaS's injected value.

**Services.**
- `deploymentCircuitBreaker: { enable: true, rollback: true }`.
- Web: `minimumHealthyPercent: 100`, `maximumPercent: 200`. Worker: keep one running through
  deploys unless the app can't run two workers at once, then `minimumHealthyPercent: 0`.
- `healthCheckGracePeriodSeconds` long enough for the app to boot and migrate.

**Database.**
- `storageEncrypted: true`, `deletionProtection: true`, `backupRetentionPeriod: 7`,
  `skipFinalSnapshot: false` with a `finalSnapshotIdentifier`, `multiAz: true` for production.
- `publiclyAccessible: false`. To reach it from a laptop, use a one-off Fargate task or SSM
  Session Manager port forwarding, never a public endpoint.
- Generate the password with `@pulumi/random`, store it in Secrets Manager, and compose
  `DATABASE_URL` there so the app reads one value as it did on the PaaS.
- Set `protect: true` on the database resource in Pulumi too.

**DNS.** If the zone is not in Route 53 (often Cloudflare), have the program create the ACM
certificate and export the validation record; the human (or the agent with the DNS provider's
CLI) adds it. Don't make the listener wait on validation in that mode, or the first `up` blocks.
If Cloudflare proxies the record, set SSL mode to Full (strict).

**Outputs.** Export the ALB DNS name, the ECR repository URLs, the cluster and service names, the
certificate validation record and the database endpoint. CI and the runbook read them.

## Reading the preview

Look for `replace` or `delete` on anything stateful (database, bucket, EFS, secrets, ECR repo
with images). Changing an RDS identifier, subnet group or engine major version replaces the
instance. Stop and explain if you see one.
