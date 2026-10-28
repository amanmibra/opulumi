#!/usr/bin/env bash
# Rehearse pg_move.py against two throwaway Postgres containers. Nothing here touches a hosted
# database or needs AWS. Run it before trusting the script with your data, and again after editing it.
#
#   scripts/rehearse_pg.sh
#
# It builds a small schema with serial ids and a migration ledger in the first container, moves it
# into the second, then breaks the second on purpose to prove verify catches it. Containers are
# removed at the end, pass or fail.
#
#   PG_IMAGE   the Postgres image for both sides (default postgres:17; match your source's major)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
image="${PG_IMAGE:-postgres:17}"
net="opulumi-rehearse-net"
src_c="opulumi-rehearse-src"
dst_c="opulumi-rehearse-dst"
pw="rehearse"
work="$(mktemp -d "${TMPDIR:-/tmp}/opulumi-rehearse.XXXXXX")"
bin="$work/pgbin"

SRC_URL="postgresql://postgres:$pw@$src_c:5432/app"
DST_URL="postgresql://postgres:$pw@$dst_c:5432/app"

cleanup() {
  echo
  echo "--- cleaning up"
  docker rm -f "$src_c" "$dst_c" >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

echo "--- starting two $image containers"
docker rm -f "$src_c" "$dst_c" >/dev/null 2>&1 || true
docker network rm "$net" >/dev/null 2>&1 || true
docker network create "$net" >/dev/null
for c in "$src_c" "$dst_c"; do
  # Data in memory: fast, needs no Docker disk, and nothing outlives the run.
  docker run -d --name "$c" --network "$net" --tmpfs /var/lib/postgresql/data:rw,size=256m \
    -e POSTGRES_PASSWORD="$pw" -e POSTGRES_DB=app "$image" >/dev/null
done

# -h 127.0.0.1 on purpose: during initdb the entrypoint runs a server on the unix socket only, so a
# socket check passes and then the server restarts under you.
for c in "$src_c" "$dst_c"; do
  ready=
  for _ in $(seq 1 90); do
    if docker exec "$c" psql -h 127.0.0.1 -U postgres -d app -c 'select 1' >/dev/null 2>&1; then
      ready=yes
      break
    fi
    sleep 1
  done
  [ -n "$ready" ] || { echo "$c never came up" >&2; docker logs "$c" | tail -20 >&2; exit 1; }
done
echo "    both up"

# Client tools from the same image, so the rehearsal does not depend on what is installed locally.
mkdir -p "$bin"
for tool in psql pg_dump pg_restore; do
  cat >"$bin/$tool" <<EOF
#!/bin/sh
exec docker run --rm -i --network $net -v "$work:$work" -w "$work" --entrypoint $tool $image "\$@"
EOF
  chmod +x "$bin/$tool"
done

echo "--- building a schema and loading rows in the source"
"$bin/psql" -X -q -v ON_ERROR_STOP=1 -d "$SRC_URL" <<'SQL'
create table schema_migrations (version text primary key);
insert into schema_migrations values ('20260101000000'), ('20260201000000'), ('20260301000000');

create table users (id serial primary key, email text unique not null, created_at timestamptz default now());
create table orders (id bigserial primary key, user_id int references users(id), total numeric(10,2), meta jsonb);
create index orders_user_idx on orders(user_id);
create view big_orders as select * from orders where total > 100;

insert into users (email) select 'user' || g || '@example.test' from generate_series(1, 250) g;
insert into orders (user_id, total, meta)
select (g % 250) + 1, (g % 500)::numeric, jsonb_build_object('n', g) from generate_series(1, 2000) g;

-- Leave a gap at the top so a sequence that restores behind its column would show.
delete from orders where id > 1990;
SQL

run() { PG_BIN="$bin" python3 "$here/pg_move.py" --source "$SRC_URL" --target "$DST_URL" \
  --dump-file "$work/app.dump" "$@"; }

echo
echo "########## 1. pre-flight only, no --confirm (must not change anything)"
run

echo
echo "########## 2. the real thing, with --confirm (must pass)"
run --confirm
echo "    exit 0, as expected"

echo
echo "########## 3. break the target on purpose (must fail)"
"$bin/psql" -X -q -v ON_ERROR_STOP=1 -d "$DST_URL" <<'SQL'
delete from orders where id in (select id from orders order by id desc limit 17);
delete from schema_migrations where version = '20260301000000';
select setval('users_id_seq', 90, true);
SQL
echo "    dropped 17 orders and a migration row, wound users_id_seq back to 90"
if run --step verify; then
  echo
  echo "REHEARSAL FAILED: verify passed on a broken target" >&2
  exit 1
fi
echo
echo "    verify exited non-zero, as expected"

echo
echo "########## 4. restore over the broken target with --overwrite-target (must pass again)"
run --confirm --overwrite-target --overwrite-dump
echo "    exit 0, as expected"

echo
echo "REHEARSAL PASSED"
