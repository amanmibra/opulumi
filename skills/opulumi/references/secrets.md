# Moving secrets without seeing them

The rule: values go from the old platform to the cloud's secret store (Secrets Manager, Secret
Manager, Key Vault) without passing through the terminal output, a log, a commit, or Pulumi state.

## Sort the variables first

From the inventory, put every env var name in one of four groups:

| Group | Examples | What to do |
|---|---|---|
| Platform-provided | `PORT`, `RAILWAY_*`, `RENDER_*`, `DYNO`, `FLY_*` | Don't copy. Find where the app reads them and give it an equivalent or nothing. |
| Points at platform resources | `DATABASE_URL`, `REDIS_URL`, internal hostnames | Don't copy. Pulumi builds the new values. |
| Human-entered secrets | API keys, OAuth client secrets, `SECRET_KEY_BASE`, webhook signing secrets | Copy, by value, without printing. |
| Plain config | `NODE_ENV`, feature flags, public URLs | Plain env on the service definition, or Pulumi config. Update URLs that name the old host. |

## Copying one value

Write the value to a temp file only you can read, create the secret from it, and delete it. Don't
pass values on the command line as arguments, since process listings and shell
history can catch them.

```sh
umask 077
tmp="$(mktemp)"
# Railway
railway variables --service web --json | jq -r '.STRIPE_SECRET_KEY' | tr -d '\n' > "$tmp"
# Render:   render env-vars list <service-id> -o json | jq -r '...'   (or the API)
# Heroku:   heroku config:get STRIPE_SECRET_KEY -a <app> | tr -d '\n' > "$tmp"
# Fly:      secrets can't be read back; ask the user or read them from inside a machine
# then the cloud's create command, reading from "$tmp" (see clouds/<cloud>.md, "Secrets"):
#   aws secretsmanager create-secret --name myapp/prod/STRIPE_SECRET_KEY --secret-string "file://$tmp"
#   gcloud secrets create STRIPE_SECRET_KEY --data-file="$tmp"
#   az keyvault secret set --vault-name <vault> --name stripe-secret-key --file "$tmp"
rm -f "$tmp"
echo "copied STRIPE_SECRET_KEY"
```

Strip trailing newlines. A key with `\n` at the end fails in confusing ways (bad signatures,
401s).

If the platform won't give the value back (Fly secrets, some dashboards), ask the user to paste
it into the temp file themselves, or to rotate it and set the new value in both places.

## Missing or empty values

If a variable is referenced in code but empty or absent on the platform, record that in
`MIGRATION.md` and ask. Never invent a value, and never assume an empty secret in the cloud has
been filled in.

## Wiring into the runtime

The service definition refers to each secret by ID (ECS `secrets[].valueFrom`, Cloud Run
`secretKeyRef`, Container Apps `secretRef` + `keyVaultUrl`). On all three, values are read when a
container starts, so a changed secret takes effect on the next deployment or restart, not
immediately.

## Keys that must not change

Anything used to encrypt or sign stored data (Rails `SECRET_KEY_BASE`, Laravel `APP_KEY`, Django
`SECRET_KEY` for signed data, Fernet keys, JWT signing keys, field-level encryption keys) must be
copied exactly. A new value silently breaks sessions at best and makes stored ciphertext
unreadable at worst.
