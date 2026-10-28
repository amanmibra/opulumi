# Deploying from CI

The PaaS deployed on push. Keep that, with GitHub Actions and no long-lived cloud keys. Each cloud
lets GitHub's OIDC token stand in for a password; the exact setup is under "CI" in
[clouds/](clouds/).

## Access, on any cloud

1. Trust GitHub's OIDC issuer (`https://token.actions.githubusercontent.com`) once per
   account/project/tenant.
2. A deploy identity that trusts only this repository and a GitHub environment
   (`repo:<owner>/<repo>:environment:production`). Restrict that environment to `main` in
   GitHub settings.
3. Scope it to a routine deploy: push to the app's registry, update the app's services and jobs,
   act as their runtime identities, read and write Pulumi state.
4. Infrastructure changes that create networks, databases or identities run from a human's
   session, not CI. Don't grant the CI identity owner/admin to make a failing deploy pass.

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
      - cloud login over OIDC:
          AWS    aws-actions/configure-aws-credentials@v4 + aws-actions/amazon-ecr-login@v2
          GCP    google-github-actions/auth@v2 + gcloud auth configure-docker
          Azure  azure/login@v2 + az acr login
      - build and push with a unique tag (git SHA + run id), with layer cache
      - run the release/migration job if the app has one, and wait for it
      - pulumi up --yes --stack <stack> with the new image tag as config
      - prove the new revision is live (below)
```

Use `pulumi/actions` or install the CLI. Pulumi Cloud needs `PULUMI_ACCESS_TOKEN` as a GitHub
secret; a self-managed backend needs `PULUMI_BACKEND_URL` and access to its bucket and key.

## A green build is not a deploy

The last step must prove the new code is serving:

- the running revision uses the image tag you just built (ECS primary deployment, Cloud Run
  latest ready revision at 100% traffic, Container Apps active revision at 100%)
- `curl` the health endpoint over HTTPS, and the version/SHA endpoint if the app has one
- fail the job if either check fails, and say whether the platform rolled back on its own

## Speed

- Build images in parallel; cache layers.
- Tag images that rarely change (a sidecar, a gateway) by a hash of their inputs, so a deploy
  that doesn't touch them doesn't restart them.
- Short health-check intervals and connection draining so rollouts take minutes, not ten.
