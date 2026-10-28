# Failures this migration hits on any cloud

Cloud-specific ones are at the end of each [clouds/](clouds/) file.

| Symptom | Likely cause | Fix |
|---|---|---|
| `exec format error` | ARM image on an x86 runtime or the reverse | `docker buildx build --platform linux/amd64`, or set the runtime to ARM. |
| Health checks fail, containers restart in a loop | Wrong health path or port, app binds `127.0.0.1`, slow boot or migrations on start | Bind `0.0.0.0`, set `PORT`, fix the path, longer startup grace. |
| App listens on the wrong port | It relied on the PaaS injecting `PORT` | Set `PORT` explicitly and match the container port. |
| Redirect loop or `http://` links | App doesn't trust `X-Forwarded-Proto` from the load balancer | Rails `config.assume_ssl`, Express `trust proxy`, Django `SECURE_PROXY_SSL_HEADER`, Laravel `TrustProxies`. |
| Cloudflare 525/526 | SSL mode vs. origin certificate | Full (strict) with a cert covering the hostname. |
| OAuth `redirect_uri_mismatch` on the side hostname | Provider only allows the old URL | Add the side hostname for verification. |
| Duplicate key on the first insert after cutover | Sequence/auto-increment restored behind its column's max | Verify (pg_move.py does); redo from a fresh dump. |
| Sessions logged out, stored data unreadable | An encryption or signing key changed | Copy `SECRET_KEY_BASE` / `APP_KEY` / Fernet / JWT keys exactly. |
| Uploads vanish after a deploy | App wrote to local disk | Object storage (S3 / GCS / Blob). |
| Webhooks fail after cutover | Signing secret differs, or the provider caches the old IP | Same secret; check the provider's delivery log. |
| Jobs run twice during deploys | Old and new worker revisions overlap | Idempotent jobs, or stop the old worker before the new one starts. |
| Cron at the wrong hour | Schedulers default to UTC | Set the timezone on the schedule. |
| CI deploy "succeeds" but old code is serving | Pipeline checked the build, not the live revision | Check the running revision's image and the health endpoint. |
| Surprise bill | NAT gateways, debug logs without retention, HA left on a test stack, a forgotten stack | Budget alerts, retention, `pulumi stack ls`, destroy unused stacks. |
