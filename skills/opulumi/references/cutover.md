# Cutover runbook

Copy this into `MIGRATION.md`, fill in the names, and walk the user through it. Every step that
changes what users see waits for the user.

## The day before

- [ ] New side verified on the side hostname (Gate C passed), CI deploys verified end to end.
- [ ] Rehearsal restore passed verify.
- [ ] DNS TTL for the app's records lowered to 60s (wait out the old TTL before cutover).
- [ ] OAuth providers, webhooks (Stripe, GitHub, Slack...), and CORS lists accept the final
      hostname on the new cloud. Usually the hostname doesn't change, so this is a check, not a change.
- [ ] The user has picked a window and told their users if there will be downtime.
- [ ] Rollback written down: the exact DNS record values that point back at the old platform.

## The window

1. Stop writes on the old platform: maintenance mode, or scale web and workers to 0 (keep the
   database running). Drain job queues first if they matter.
2. Final dump → restore → verify. **Stop if verify fails.** Store the dump in object storage,
   note the hash.
3. Copy any volume/file delta to object storage.
4. Make sure the new services are running the release you verified, with `DATABASE_URL` pointing
   at the new database.
5. **Ask the user.** Then change DNS to the new entry point (see "Cutover target" in
   [clouds/](clouds/): ALB, load balancer IP, or Container App FQDN + TXT). With Cloudflare
   proxying, set SSL to Full (strict).
6. Check from outside: `dig +short <host>`, `curl -sI https://<host>/<health>`, sign in, one
   write path, one background job.
7. Watch for 30 minutes: load balancer 5xx and health, container restarts, error logs, database
   connections and CPU.

## Rollback

Before any write on the new cloud matters: point DNS back, scale the old platform up. After writes on the new
cloud: rolling back loses them unless you copy them back; decide with the user, don't improvise.

## After

- Keep the old platform paused (not deleted) for at least a week.
- Raise the DNS TTL again.
- Run a point-in-time restore drill on the new database.
- Remove the old platform's config files from the repo only when the user asks for cleanup.
