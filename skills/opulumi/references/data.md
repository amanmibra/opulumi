# Moving the data

Do it twice: a rehearsal into the new database during Phase 3 to prove the path, and the real copy during the
cutover window with writers stopped.

## Reaching the databases

- **Source:** most PaaS databases offer an external connection URL (Railway `DATABASE_PUBLIC_URL`,
  Render external URL, Heroku `DATABASE_URL` with SSL). Use it read-only.
- **Target:** the new database is private. Run the copy from a one-off container inside the
  network (image `postgres:<major>` or `mysql:<major>`): an ECS task, a Cloud Run job, or a
  Container Apps job. See "Reaching the private database" in [clouds/](clouds/). Don't give the
  database a public endpoint, even briefly.
- Client tools must be at least as new as the newer server. `pg_dump` refuses a newer server.

## Postgres

`scripts/pg_move.py` (standard library only; needs `psql`, `pg_dump`, `pg_restore`):

```sh
scripts/pg_move.py --source "$SOURCE_URL" --target "$TARGET_URL" --dump-file ./app.dump            # preflight only
scripts/pg_move.py --source "$SOURCE_URL" --target "$TARGET_URL" --dump-file ./app.dump --confirm  # dump, restore, verify
scripts/pg_move.py --source ... --target ... --step verify                                         # compare again later
```

It never writes to the source, refuses a non-empty target unless told, and verifies:

- every table exists on both sides with the same row count
- the migration ledger (`schema_migrations`, `_prisma_migrations`, `alembic_version`, ...) matches
- every sequence is at or past its column's max, so the first insert after cutover does not hit a
  duplicate key
- index, constraint and view counts match

`scripts/rehearse_pg.sh` runs it against two throwaway Docker containers, including a deliberately
broken target. Run it once after any edit to the script.

Extensions: all three managed Postgres services support the common ones (`pgcrypto`,
`uuid-ossp`, `postgis`, `vector`, `pg_trgm`), but check the source's list from preflight against
the target's list for that version. Azure needs each one allow-listed in `azure.extensions` first.

Ownership: the restore uses `--no-owner --no-acl`, so everything is owned by the admin user you
restore as. If the app used several roles, recreate them and grants by hand.

For large databases (tens of GB+) or near-zero downtime, use the cloud's migration service (AWS
DMS, GCP Database Migration Service, Azure Database Migration Service) or logical replication
instead of dump and restore, and tell the user it is a bigger project.

## MySQL / MariaDB

```sh
mysqldump --single-transaction --routines --triggers --set-gtid-purged=OFF \
  -h SRC -u USER -p DB > app.sql
mysql -h TARGET -u USER -p DB < app.sql
```

Then compare `select count(*)` per table and the `AUTO_INCREMENT` value per table
(`information_schema.tables`) on both sides, and the migration ledger.

## Redis

If it only held cache, queues that can be drained, or rate-limit counters, start empty. Drain
job queues on the old platform before cutover. If it holds data that matters (sessions you
don't want to drop, persistent sets), copy with `redis-cli --rdb` + restore, or `RIOT`, and say
it needs care.

## Files on a volume

Sync from inside the old service (or a one-off shell on it) to the new bucket (`aws s3 sync`,
`gcloud storage rsync`, `azcopy sync`), then
again during the cutover window for the delta. If the app wrote to local paths, it needs a code
change to use object storage; flag that in Gate A.

## After the copy

Store the final pre-cutover dump in object storage (private, encrypted, versioned) and record its SHA-256 in
`MIGRATION.md`. It is the rollback point if anything is wrong a week later.

Do a restore drill once things settle: restore the database to a point in time as a new instance, compare
it to production with the same verify, delete it. Write down how long it took.
