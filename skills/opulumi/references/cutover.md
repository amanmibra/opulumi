# Cutover runbook

Copy this into `MIGRATION.md`, fill in the names, and walk the user through it. Every step that
changes what users see waits for the user.

## The day before

- [ ] AWS side verified on the side hostname (Gate C passed), CI deploys verified end to end.
- [ ] Rehearsal restore passed verify.
- [ ] DNS TTL for the app's records lowered to 60s (wait out the old TTL before cutover).
- [ ] OAuth providers, webhooks (Stripe, GitHub, Slack...), and CORS lists accept the final
      hostname on AWS. Usually the hostname doesn't change, so this is a check, not a change.
- [ ] The user has picked a window and told their users if there will be downtime.
- [ ] Rollback written down: the exact DNS record values that point back at the old platform.

## The window

1. Stop writes on the old platform: maintenance mode, or scale web and workers to 0 (keep the
   database running). Drain job queues first if they matter.
2. Final dump → restore → verify. **Stop if verify fails.** Store the dump in S3, note the hash.
3. Copy any volume/file delta to S3.
4. Make sure the AWS services are running the release you verified, with `DATABASE_URL` pointing
   at RDS.
5. **Ask the user.** Then change DNS to the ALB (CNAME, or ALIAS/flattened CNAME at the apex).
   With Cloudflare proxying, set SSL to Full (strict).
6. Check from outside: `dig +short <host>`, `curl -sI https://<host>/<health>`, sign in, one
   write path, one background job.
7. Watch for 30 minutes: ALB 5xx count and target health, ECS task restarts, CloudWatch errors,
   RDS connections and CPU.

## Rollback

Before any write on AWS matters: point DNS back, scale the old platform up. After writes on AWS:
rolling back loses them unless you copy them back; decide with the user, don't improvise.

## After

- Keep the old platform paused (not deleted) for at least a week.
- Raise the DNS TTL again.
- Run a point-in-time restore drill on RDS.
- Remove the old platform's config files from the repo only when the user asks for cleanup.
