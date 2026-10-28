# The Pulumi program, on any cloud

## Language and provider

Use the app's language when Pulumi supports it (TypeScript, Python, Go, C#, Java); otherwise
TypeScript. Providers:

| Cloud | Provider | Notes |
|---|---|---|
| AWS | `@pulumi/aws` (+ optional `@pulumi/awsx`) | See [clouds/aws.md](clouds/aws.md). |
| GCP | `@pulumi/gcp` | See [clouds/gcp.md](clouds/gcp.md). |
| Azure | `@pulumi/azure-native` | Not the older `@pulumi/azure` (classic). See [clouds/azure.md](clouds/azure.md). |
| DNS | `@pulumi/cloudflare` if the zone is in Cloudflare | Optional; the cert validation and app records can also be added by hand at cutover. |

Python names are `pulumi_aws`, `pulumi_gcp`, `pulumi_azure_native`; Go and .NET follow the same
pattern.

## State

- **Pulumi Cloud** (free for individuals): simplest, and CI needs only `PULUMI_ACCESS_TOKEN`.
- **Self-managed** in the same cloud: S3 + KMS, GCS + Cloud KMS, or Azure Blob + Key Vault. Make
  the bucket private and versioned. Keep the secrets-provider URL free of local profile names so
  CI can decrypt it too.

## Layout

Keep the entry point short enough to read as the whole deployment, with one module per concern.
Each module starts with a comment saying why it looks the way it does; the next person (or agent)
reading it has no other context.

```
infra/
  Pulumi.yaml                  project, runtime
  Pulumi.<stack>.yaml          config: account/project/subscription, region, sizes, image tags, domain
  index.ts | __main__.py | main.go
  config        read and validate config once; refuse the wrong account/project/subscription
  network       VPC/VNet, subnets, firewall rules or security groups
  data          database, cache, secret references
  services      registry, load balancing, web/worker services, scheduled jobs
  dns           certificate and records
  budget        monthly budget with email alerts
```

## Rules on every cloud

- **Identity guard, twice.** Compare the live caller identity (account / project / subscription)
  with the value in stack config before registering resources, and use the provider's own
  allow-list where it has one.
- **Stack-scoped names** with a short prefix. Every cloud has length and character limits on some
  names (ALB 32 chars, Azure storage accounts 3-24 lowercase alphanumerics, ACR alphanumeric).
- **Stateful resources protected:** `protect: true` in Pulumi, plus the cloud's deletion
  protection, backups (7+ days), and a final snapshot or backup on delete.
- **Database private.** No public IP on the database; reach it from inside the network.
- **Secrets by reference.** The program refers to human-entered secrets by ID and never holds
  their values. Values it generates (DB password) land in state encrypted; say so in a comment.
- **Image tags from config,** set by CI per deploy. Never `latest`.
- **Logs with retention** (14-30 days).
- **Outputs** for everything CI and the runbook need: service URLs, registry URLs, load balancer
  address, cert validation records, database host.

## Reading the preview

Look for `replace` or `delete` on anything stateful (database, bucket, volume, secret, registry
with images, Key Vault). Renaming a database, changing its network or its engine major version
replaces it on every cloud. Stop and explain if you see one.
