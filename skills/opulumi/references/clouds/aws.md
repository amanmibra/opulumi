# AWS

Target: ECS Fargate behind an Application Load Balancer, RDS, ElastiCache, Secrets Manager, ECR,
EventBridge Scheduler. Provider: `@pulumi/aws` (classic), optionally `@pulumi/awsx`.

## Preflight

- Pin the account: `aws sts get-caller-identity` with an explicit `AWS_PROFILE`; put the ID in
  `aws:allowedAccountIds`. Never use the default profile.
- Fargate On-Demand vCPU quota (`L-3032A538`) in the region:
  `aws service-quotas get-service-quota --service-code fargate --quota-code L-3032A538`.
  It can be `0` on a new account; request an increase now and consider another region.
- A new account can be blocked from launching anything (`BlockedException`) until verified.
  Check the account email; Account and Billing support is free.
- `us-east-1` has every service and the fewest surprises.
- Budget: `aws.budgets.Budget` with email subscribers at 80% actual, 100% actual, 100% forecast.

## Program

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

Preview red flags: changing an RDS identifier, subnet group or engine major version replaces the
instance.

## Secrets

```sh
aws secretsmanager create-secret --name "myapp/prod/NAME" --secret-string "file://$tmp" >/dev/null
```

Task definitions list each under `secrets` with `valueFrom` = the ARN. The execution role needs
`secretsmanager:GetSecretValue` on `arn:...:secret:myapp/prod/*`. Values load at task start; force
a new deployment after changing one.

## Reaching the private database

A one-off Fargate task in the VPC (task security group, public subnet, public IP), or SSM Session
Manager port forwarding through a small task. Never make RDS public.

## CI

IAM OIDC provider for `token.actions.githubusercontent.com`; a role trusting
`repo:<owner>/<repo>:environment:production`; `aws-actions/configure-aws-credentials@v4` +
`aws-actions/amazon-ecr-login@v2`. Scope: push to the app's ECR repos, register task definitions,
update the app's services, `iam:PassRole` on the two task roles, Pulumi state access.

Proving the deploy: each service's `PRIMARY` deployment uses the new task definition with
`rolloutState: COMPLETED`; `aws ecs wait services-stable` alone can pass on the old revision.

## Cutover target

CNAME (or ALIAS / flattened CNAME at the apex) to the ALB DNS name. Behind Cloudflare, SSL mode
Full (strict) with the ACM certificate covering the hostname.

## Gotchas

| Symptom | Likely cause | Fix |
|---|---|---|
| Service never starts tasks; vCPU limit error | Fargate quota 0 or tiny | Request `L-3032A538`; try another region. |
| `BlockedException: Your account is currently blocked` | New account not verified | Account and Billing support; nothing in code fixes it. |
| `CannotPullContainerError` | No public IP in a public subnet, or no NAT/endpoints in a private one; wrong architecture | `assignPublicIp: true`, or NAT / ECR+S3 VPC endpoints; build for the task's architecture. |
| `ResourceInitializationError: unable to pull secrets` | Execution role can't read the secret or KMS key | Grant `GetSecretValue` on `myapp/prod/*`. |
| Targets unhealthy, tasks cycle | Health path/port wrong, app binds 127.0.0.1, slow boot | Bind `0.0.0.0`, set `PORT`, fix path, raise grace period. |
| Websockets drop after 60s | ALB idle timeout | Raise it; send keepalives. |
| Deploys take 10+ minutes | 300s deregistration delay | ~30s; health interval 10-15s. |
| CI can't decrypt Pulumi state | `profile=` in the `awskms://` URL | Remove it; `pulumi stack change-secrets-provider`. |
| ALB/target group name too long | 32-char limit plus Pulumi suffix | Shorter prefix. |
| Surprise bill | NAT gateway, logs without retention, forgotten stack | Budget alerts, retention, `pulumi stack ls`. |
| Cron at the wrong hour | Scheduler is UTC | `scheduleExpressionTimezone`. |
