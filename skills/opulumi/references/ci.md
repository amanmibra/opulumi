# Deploying from CI

The PaaS deployed on push. Keep that, with GitHub Actions and no long-lived AWS keys.

## Access

1. An IAM OIDC provider for `token.actions.githubusercontent.com` (one per account).
2. A deploy role whose trust policy pins the repository and a GitHub environment:
   `"token.actions.githubusercontent.com:sub": "repo:<owner>/<repo>:environment:production"`.
   Restrict the `production` environment to the `main` branch in GitHub settings.
3. Scope the role to what a routine deploy does: push to the app's ECR repositories, register
   task definitions, update the app's ECS services, `iam:PassRole` for exactly the two task roles,
   read and write the Pulumi state (and its KMS key, if self-managed).
4. Infrastructure changes that create networks, databases or IAM run from a human's SSO session,
   not CI. Don't attach `AdministratorAccess` to the CI role to make a failing deploy pass.

## Workflow shape

```yaml
on:
  push: { branches: [main] }
  workflow_dispatch:
concurrency: { group: production-deploy, cancel-in-progress: false }
permissions: { contents: read, id-token: write }
jobs:
  deploy:
    environment: production
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with: { role-to-assume: <deploy role ARN>, aws-region: <region> }
      - uses: aws-actions/amazon-ecr-login@v2
      - build and push with a unique tag (git SHA + run id), using ECR or GHA layer cache
      - pulumi up --yes --stack <stack> with the new image tag as config
        (or: aws ecs update-service with a new task definition revision)
      - wait for the rollout, then check it
```

Use `pulumi/actions` or install the CLI; set `PULUMI_BACKEND_URL` for a self-managed backend or
`PULUMI_ACCESS_TOKEN` for Pulumi Cloud (as a GitHub secret).

## A green build is not a deploy

The last step must prove the new code is serving:

- `aws ecs wait services-stable` is not enough on its own (it can pass on the old revision);
  check that each service's `PRIMARY` deployment uses the new task definition and has
  `rolloutState: COMPLETED`.
- `curl` the health endpoint over HTTPS and, if the app exposes it, check the version/SHA.
- Fail the job if either check fails. ECS's circuit breaker rolls back; the job should say so.

## Speed

- Build images in parallel; cache layers.
- Tag images that don't change (a sidecar, a gateway) by a hash of their inputs, so a deploy that
  doesn't touch them doesn't restart them.
- Lower the ALB deregistration delay and the health check interval so rollouts take minutes,
  not ten.
