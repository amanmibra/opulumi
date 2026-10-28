# Failures this migration hits most

| Symptom | Likely cause | Fix |
|---|---|---|
| `RunTask` fails, or the service never starts tasks; `You've reached the limit on the number of vCPUs` | Fargate vCPU quota is 0 or tiny in this account/region | Request `L-3032A538`; try another region; check for an account verification email. |
| `BlockedException: Your account is currently blocked` | New account not activated/verified | Account and Billing support (free); nothing in code will fix it. |
| `CannotPullContainerError` | Task can't reach ECR: no public IP in a public subnet, or no NAT/endpoints in a private one; or wrong architecture | `assignPublicIp: true`, or add NAT / ECR+S3 VPC endpoints. Build for the task's CPU architecture. |
| `ResourceInitializationError: unable to pull secrets` | Execution role can't read the secret or its KMS key, or the ARN is wrong | Grant `secretsmanager:GetSecretValue` on `<app>/<stack>/*` to the execution role. |
| `exec format error` | ARM image on x86 task or the reverse | `docker buildx build --platform linux/amd64` or set `runtimePlatform`. |
| Targets unhealthy, tasks cycle | Health check path/port wrong, app binds to `127.0.0.1`, app slow to boot | Bind `0.0.0.0`, set `PORT`, fix the path, raise the grace period. |
| App listens on the wrong port | It read the PaaS's injected `PORT` | Set `PORT` in the task definition and match the container port. |
| Redirect loop or `http://` links behind the ALB | App doesn't trust `X-Forwarded-Proto` | Enable proxy trust (Rails `config.assume_ssl`, Express `trust proxy`, Django `SECURE_PROXY_SSL_HEADER`). |
| Cloudflare 525/526 | Cloudflare SSL mode vs. ALB cert mismatch | Full (strict) with the ACM cert covering the hostname. |
| OAuth `redirect_uri_mismatch` on the side hostname | Provider only allows the old URL | Add the side hostname to the provider for verification. |
| Duplicate key on first insert after cutover | Sequence restored behind its column's max | Verify sequences (pg_move.py does); redo from a fresh dump. |
| Websockets drop after 60s | ALB idle timeout default 60s | Raise idle timeout; send keepalives. |
| Uploads vanish after a deploy | App wrote to local disk | Move to S3 (EFS if a path is unavoidable). |
| Deploys take 10+ minutes | 300s deregistration delay, slow health checks | Deregistration ~30s, health interval 10-15s. |
| `pulumi up` in CI can't decrypt state | KMS secrets provider URL includes `profile=` | Remove `profile=` from the `awskms://` URL and re-encrypt with `pulumi stack change-secrets-provider`. |
| ALB/target group name too long | 32-char limit plus Pulumi suffix | Shorter prefix, or set `namePrefix`. |
| Surprise bill | NAT gateway, debug logs with no retention, forgotten test stack | Budget alerts, log retention, `pulumi stack ls` and destroy what isn't used. |
| Cron runs at the wrong hour | Scheduler is UTC by default | Set `scheduleExpressionTimezone`. |
| Workers double-process jobs during deploy | Two worker revisions overlap | Make jobs idempotent, or `minimumHealthyPercent: 0` for the worker. |
