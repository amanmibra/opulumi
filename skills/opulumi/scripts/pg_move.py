#!/usr/bin/env python3
"""Move a Postgres database from one host to another: pre-flight, dump, restore, verify.

Written for leaving a PaaS (Railway, Render, Heroku, Fly) for RDS, but it only needs two connection URLs.

Nothing here writes to the source. Nothing destructive runs without --confirm. Read-only steps
(preflight, verify) run without it.

    scripts/pg_move.py --source "$SOURCE_URL" --target "$TARGET_URL" --dump-file ./app.dump
    migrate/pg_migrate.py --source ... --target ... --dump-file ... --confirm

Only the standard library, plus psql, pg_dump and pg_restore on PATH (or --pg-bin). The client tools
must be at least as new as the newest server; pg_dump refuses to read a server newer than itself.

The verify step is the point of this script. A dump and restore that "worked" still loses the
sequences' positions if anything went sideways, and a table whose sequence sits behind its own max id
raises a duplicate key error on the first insert after cutover, long after you stopped watching.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass, field

# Extensions the target must offer before a restore is worth trying. Empty means "copy whatever the
# source has" and pre-flight still lists them. Add names (postgis, vector, ...) your app depends on.
REQUIRED_EXTENSIONS: list[str] = []

SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")

# psql field separator and NULL marker: control characters no identifier or revision string contains.
FIELD_SEP = "\x1f"
NULL_MARK = "\x1e"


# ---------------------------------------------------------------- plumbing


class Fail(Exception):
    """Something the operator has to fix. Printed without a traceback."""


def redact(url: str) -> str:
    """The URL with its password replaced, for printing."""
    return re.sub(r"(?<=://)([^:/@]+):([^@]*)@", r"\1:***@", url)


def short(url: str) -> str:
    """host/dbname, for column headers."""
    p = urllib.parse.urlparse(url)
    return f"{p.hostname or '?'}/{(p.path or '/').lstrip('/') or '?'}"


class Pg:
    """psql, pg_dump and pg_restore, from --pg-bin or PATH."""

    def __init__(self, bindir: str | None):
        self.bindir = bindir

    def path(self, tool: str) -> str:
        return os.path.join(self.bindir, tool) if self.bindir else tool

    def run(self, tool: str, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
        cmd = [self.path(tool), *args]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError:
            raise Fail(f"{cmd[0]} is not there. Install the Postgres client tools, or fix --pg-bin.")
        if check and proc.returncode != 0:
            raise Fail(f"{tool} failed ({proc.returncode}):\n{proc.stderr.strip() or proc.stdout.strip()}")
        return proc

    def version(self, tool: str) -> tuple[int, str]:
        """(major, full version string) of a client tool."""
        out = self.run(tool, ["--version"]).stdout.strip()
        m = re.search(r"(\d+)(?:\.(\d+))?", out)
        if not m:
            raise Fail(f"cannot read a version out of `{tool} --version`: {out!r}")
        return int(m.group(1)), out

    def sql(self, url: str, query: str) -> list[list[str]]:
        """Rows as lists of strings. NULL comes back as NULL_MARK, so an empty string stays distinct."""
        cmd = [self.path("psql"), "-X", "-q", "-A", "-t", "-F", FIELD_SEP, "-P", f"null={NULL_MARK}",
               "-v", "ON_ERROR_STOP=1", "-d", url, "-c", query]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError:
            raise Fail(f"{cmd[0]} is not there. Install the Postgres client tools, or fix --pg-bin.")
        if proc.returncode != 0:
            raise Fail(f"psql failed against {redact(url)}:\n{proc.stderr.strip()}")
        return [line.split(FIELD_SEP) for line in proc.stdout.splitlines() if line != ""]

    def one(self, url: str, query: str) -> str | None:
        rows = self.sql(url, query)
        if not rows or not rows[0]:
            return None
        v = rows[0][0]
        return None if v == NULL_MARK else v


def nullable(v: str) -> str | None:
    return None if v == NULL_MARK else v


def table(headers: list[str], rows: list[list[str]]) -> str:
    """A plain text table. Left-aligned first column, right-aligned numbers, status last."""
    if not rows:
        return "  (nothing)"
    widths = [len(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))

    def line(cells: list[str]) -> str:
        out = []
        for i, c in enumerate(cells):
            out.append(c.ljust(widths[i]) if i == 0 or i == len(cells) - 1 else c.rjust(widths[i]))
        return "  " + "  ".join(out).rstrip()

    sep = "  " + "  ".join("-" * w for w in widths)
    return "\n".join([line(headers), sep, *(line(r) for r in rows)])


def human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def heading(text: str) -> None:
    print(f"\n{text}\n{'=' * len(text)}")


# ---------------------------------------------------------------- queries

TABLES_Q = f"""
select table_schema, table_name
from information_schema.tables
where table_type = 'BASE TABLE' and table_schema not in {SYSTEM_SCHEMAS}
order by 1, 2
"""

# pg_sequences.last_value is the last value handed out, or NULL if the sequence has never been used.
SEQUENCES_Q = f"""
select schemaname, sequencename, last_value
from pg_sequences
where schemaname not in {SYSTEM_SCHEMAS}
order by 1, 2
"""

# Every sequence that is a column default or an identity, with the column it feeds. This is what
# decides whether the next insert collides.
OWNED_SEQUENCES_Q = f"""
select n.nspname, c.relname, tn.nspname, t.relname, a.attname
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
join pg_depend d on d.objid = c.oid and d.classid = 'pg_class'::regclass and d.deptype in ('a', 'i')
join pg_class t on t.oid = d.refobjid
join pg_namespace tn on tn.oid = t.relnamespace
join pg_attribute a on a.attrelid = t.oid and a.attnum = d.refobjsubid
where c.relkind = 'S' and n.nspname not in {SYSTEM_SCHEMAS}
order by 1, 2
"""

OBJECTS_Q = f"""
select 'indexes', count(*)::text from pg_indexes where schemaname not in {SYSTEM_SCHEMAS}
union all
select 'constraints', count(*)::text from pg_constraint c
  join pg_namespace n on n.oid = c.connamespace where n.nspname not in {SYSTEM_SCHEMAS}
union all
select 'views', count(*)::text from information_schema.views where table_schema not in {SYSTEM_SCHEMAS}
union all
select 'extensions', count(*)::text from pg_extension where extname <> 'plpgsql'
order by 1
"""


def qname(schema: str, name: str) -> str:
    return f'"{schema}"."{name}"'


def label(schema: str, name: str) -> str:
    return name if schema == "public" else f"{schema}.{name}"


# ---------------------------------------------------------------- steps


# Tables that record which schema migrations have run, by framework. Compared row for row in verify.
MIGRATION_LEDGERS = (
    "alembic_version",          # Alembic / SQLAlchemy
    "schema_migrations",        # Rails, golang-migrate, Ecto, dbmate
    "_prisma_migrations",       # Prisma
    "django_migrations",        # Django
    "knex_migrations",          # Knex
    "flyway_schema_history",    # Flyway
    "goose_db_version",         # goose
    "__drizzle_migrations",     # Drizzle
    "SequelizeMeta",            # Sequelize
    "migrations",               # TypeORM, Laravel, MikroORM and others
)


def migration_ledgers(tables: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return sorted((s, t) for s, t in tables if t in MIGRATION_LEDGERS)


@dataclass
class Side:
    url: str
    name: str
    major: int = 0
    version: str = ""
    tables: list[tuple[str, str]] = field(default_factory=list)


def server(pg: Pg, url: str, name: str) -> Side:
    try:
        num = pg.one(url, "show server_version_num")
        ver = pg.one(url, "show server_version")
    except Fail as e:
        raise Fail(f"cannot reach the {name} database at {redact(url)}.\n{e}")
    side = Side(url=url, name=name, major=int(num or 0) // 10000, version=ver or "?")
    side.tables = [(r[0], r[1]) for r in pg.sql(url, TABLES_Q)]
    return side


def preflight(pg: Pg, args, restoring: bool) -> tuple[Side, Side]:
    """`restoring`: the run will write to the target, so the target has to be empty or overwritable."""
    heading("Pre-flight")

    if args.source == args.target:
        raise Fail("source and target are the same URL.")

    src = server(pg, args.source, "source")
    dst = server(pg, args.target, "target")
    print(f"  source   {redact(args.source)}")
    print(f"           Postgres {src.version}, {len(src.tables)} tables")
    print(f"  target   {redact(args.target)}")
    print(f"           Postgres {dst.version}, {len(dst.tables)} tables")

    s_id = pg.sql(args.source, "select inet_server_addr()::text, inet_server_port()::text, current_database(), system_identifier::text from pg_control_system()")
    d_id = pg.sql(args.target, "select inet_server_addr()::text, inet_server_port()::text, current_database(), system_identifier::text from pg_control_system()")
    if s_id and d_id and s_id[0][3] == d_id[0][3] and s_id[0][2] == d_id[0][2]:
        raise Fail("source and target are the same database on the same server.")

    problems: list[str] = []

    # Versions. Restoring into an older major is not supported; a newer major is the normal case.
    if dst.major < src.major:
        problems.append(f"the target is Postgres {dst.major} and the source is Postgres {src.major}. "
                        "A dump cannot be restored into an older major version.")
    elif dst.major > src.major:
        print(f"  note     target is Postgres {dst.major}, source is Postgres {src.major}. "
              "That is fine, but read the release notes for the versions in between.")
    else:
        print(f"  ok       both servers are Postgres {src.major}")

    # Client tools: must be at least as new as the newest server.
    need = max(src.major, dst.major)
    for tool in ("pg_dump", "pg_restore", "psql"):
        major, full = pg.version(tool)
        if major < need:
            problems.append(f"{full} is too old for a Postgres {need} server. Install the {need} client tools "
                            f"(`brew install postgresql@{need}`) and pass --pg-bin, or run this from a "
                            f"postgres:{need} container.")
        else:
            print(f"  ok       {full}")

    # Target empty, or you said to overwrite it. Only matters for a run that will write to it.
    if not restoring:
        print(f"  ok       read-only run; target has {len(dst.tables)} tables and will not be written to")
    elif dst.tables:
        listed = ", ".join(label(s, t) for s, t in dst.tables[:6])
        more = f" and {len(dst.tables) - 6} more" if len(dst.tables) > 6 else ""
        if args.overwrite_target:
            print(f"  note     target is not empty ({listed}{more}); --overwrite-target will drop its schemas first")
        else:
            problems.append(f"the target is not empty: {listed}{more}. Restoring on top of this would mix two "
                            "databases together. Empty it, or pass --overwrite-target to have this script drop "
                            "and recreate its schemas.")
    else:
        print("  ok       target has no tables")

    # Source size, so you know what you are about to copy and how long it will take.
    size = pg.one(args.source, "select pg_size_pretty(pg_database_size(current_database()))")
    ledgers = migration_ledgers(src.tables)
    print(f"  ok       source database is {size}")
    if ledgers:
        for key in ledgers:
            rows = pg.one(args.source, f"select count(*) from {qname(*key)}")
            print(f"  ok       migration ledger {label(*key)} has {rows} row(s)")
    else:
        print("  note     no known migration table on the source (alembic, Rails, Prisma, Django, Knex, "
              "Flyway, goose, Drizzle, ...). If your app migrates on startup, an empty-looking database "
              "is a sign you are pointed at the wrong one.")

    exts = [r[0] for r in pg.sql(args.source, "select extname from pg_extension where extname <> 'plpgsql' order by 1")]
    print(f"  ok       source extensions: {', '.join(exts) if exts else 'none beyond plpgsql'}")

    # Target has to let us create things.
    if restoring:
        user = pg.one(args.target, "select current_user")
        can = pg.one(args.target, "select has_database_privilege(current_user, current_database(), 'CREATE')")
        if can != "t":
            problems.append(f"{user} cannot CREATE in the target database.")
        else:
            print(f"  ok       {user} can create objects in the target")

    if problems:
        print()
        for p in problems:
            print(f"  BLOCKED  {p}")
        raise Fail(f"{len(problems)} pre-flight problem(s). Nothing was changed.")

    print("\n  Pre-flight passed.")
    return src, dst


def dump(pg: Pg, args, src: Side) -> None:
    heading("Dump")
    path = args.dump_file
    if os.path.exists(path):
        if not args.overwrite_dump:
            raise Fail(f"{path} already exists. Move it, or pass --overwrite-dump.")
        print(f"  replacing {path} ({human(os.path.getsize(path))})")

    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)

    print(f"  pg_dump --no-owner --no-acl -Fc -f {path}")
    pg.run("pg_dump", ["--no-owner", "--no-acl", "-Fc", "--verbose", "-f", path, "-d", src.url])

    size = os.path.getsize(path)
    if size == 0:
        raise Fail(f"{path} is empty.")
    entries = len(pg.run("pg_restore", ["-l", path]).stdout.splitlines())
    print(f"  wrote {path}")
    print(f"  {human(size)} ({size} bytes), {entries} lines in the table of contents")
    print("\n  Keep this file until the new database has been serving traffic for a while.")


def restore(pg: Pg, args, src: Side, dst: Side) -> None:
    heading("Restore")
    path = args.dump_file
    if not os.path.exists(path):
        raise Fail(f"{path} does not exist. Run the dump step first.")

    if args.overwrite_target and dst.tables:
        schemas = sorted({s for s, _ in dst.tables})
        print(f"  dropping and recreating {', '.join(schemas)} on the target")
        for s in schemas:
            pg.sql(args.target, f'drop schema "{s}" cascade; create schema "{s}";')

    # Extensions the source has that the dump may not carry (pg_dump skips extensions the restoring
    # role cannot create, and a managed Postgres often restricts them). Create them first so the
    # restore does not fail halfway on an unknown type or function.
    have = {r[0] for r in pg.sql(args.target, "select extname from pg_extension")}
    want = {r[0] for r in pg.sql(args.source, "select extname from pg_extension where extname <> 'plpgsql'")}
    want |= set(REQUIRED_EXTENSIONS)
    missing = sorted(want - have)
    if missing:
        print(f"  creating extensions: {', '.join(missing)}")
        for ext in missing:
            try:
                pg.sql(args.target, f'create extension if not exists "{ext}"')
            except Fail as e:
                raise Fail(f"cannot create extension {ext} on the target. On RDS, check it is in "
                           f"shared_preload_libraries or available to this role.\n{e}")
    else:
        print("  no extensions to create")

    print(f"  pg_restore --no-owner --no-acl --single-transaction --exit-on-error -d <target> {path}")
    proc = pg.run("pg_restore", ["--no-owner", "--no-acl", "--single-transaction", "--exit-on-error",
                                 "-d", args.target, path], check=False)
    if proc.returncode != 0:
        raise Fail("pg_restore failed. --single-transaction means nothing was committed; the target is as it "
                   f"was.\n{proc.stderr.strip()}")
    if proc.stderr.strip():
        print("  pg_restore said:")
        for line in proc.stderr.strip().splitlines():
            print(f"    {line}")

    # Planner statistics do not come across in the dump. Without them the first queries pick bad plans.
    print("  analyze")
    pg.sql(args.target, "analyze")
    print("\n  Restore finished.")


# ---------------------------------------------------------------- verify


def counts(pg: Pg, url: str, tables: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
    """An exact count(*) per table. Not reltuples: this is the one number that has to be right."""
    if not tables:
        return {}
    out: dict[tuple[str, str], int] = {}
    # One statement per batch of tables, so a 60 table schema is a few round trips, not sixty.
    for i in range(0, len(tables), 40):
        batch = tables[i:i + 40]
        q = " union all ".join(
            f"select '{s}' as s, '{t}' as t, count(*)::text as n from {qname(s, t)}" for s, t in batch
        )
        for row in pg.sql(url, q):
            out[(row[0], row[1])] = int(row[2])
    return out


def verify(pg: Pg, args) -> bool:
    """True when the two databases match. Prints everything it looked at either way."""
    heading("Verify")
    src_name, dst_name = short(args.source), short(args.target)
    ok = True

    src_tables = [(r[0], r[1]) for r in pg.sql(args.source, TABLES_Q)]
    dst_tables = [(r[0], r[1]) for r in pg.sql(args.target, TABLES_Q)]
    only_src = sorted(set(src_tables) - set(dst_tables))
    only_dst = sorted(set(dst_tables) - set(src_tables))

    # --- rows per table
    print("\nRows per table\n")
    src_counts = counts(pg, args.source, src_tables)
    dst_counts = counts(pg, args.target, dst_tables)
    rows: list[list[str]] = []
    bad_tables = 0
    for key in sorted(set(src_tables) | set(dst_tables)):
        s = src_counts.get(key)
        d = dst_counts.get(key)
        if s is None:
            rows.append([label(*key), "-", str(d), "-", "MISSING ON SOURCE"])
            bad_tables += 1
        elif d is None:
            rows.append([label(*key), str(s), "-", "-", "MISSING ON TARGET"])
            bad_tables += 1
        elif s != d:
            rows.append([label(*key), str(s), str(d), f"{d - s:+d}", "MISMATCH"])
            bad_tables += 1
        else:
            rows.append([label(*key), str(s), str(d), "0", "ok"])
    print(table(["table", src_name, dst_name, "diff", ""], rows))
    total_s, total_d = sum(src_counts.values()), sum(dst_counts.values())
    print(f"\n  {len(src_tables)} tables, {total_s} rows on the source; "
          f"{len(dst_tables)} tables, {total_d} rows on the target")
    if only_src:
        print(f"  tables only on the source: {', '.join(label(*k) for k in only_src)}")
    if only_dst:
        print(f"  tables only on the target: {', '.join(label(*k) for k in only_dst)}")
    if bad_tables:
        ok = False
        print(f"  {bad_tables} table(s) do not match.")
    else:
        print("  Every table matches.")

    # --- migration ledgers
    print("\nMigration ledgers\n")
    keys = sorted(set(migration_ledgers(list(src_tables))) | set(migration_ledgers(list(dst_tables))))
    if not keys:
        print("  none found on either side; skipped.")
    for key in keys:
        if key not in src_tables or key not in dst_tables:
            ok = False
            print(f"  MISMATCH  {label(*key)} is {'present' if key in src_tables else 'absent'} on the source and "
                  f"{'present' if key in dst_tables else 'absent'} on the target.")
            continue
        q = f"select t::text from {qname(*key)} t order by 1"
        s_rows = [r[0] for r in pg.sql(args.source, q)]
        d_rows = [r[0] for r in pg.sql(args.target, q)]
        if s_rows == d_rows:
            print(f"  ok        {label(*key)} matches ({len(s_rows)} row(s)).")
        else:
            ok = False
            print(f"  MISMATCH  {label(*key)} differs ({len(s_rows)} rows on the source, {len(d_rows)} on the target). "
                  "An app that migrates on startup would run the wrong migrations against this target.")

    # --- sequences
    print("\nSequences\n")
    s_seq = {(r[0], r[1]): nullable(r[2]) for r in pg.sql(args.source, SEQUENCES_Q)}
    d_seq = {(r[0], r[1]): nullable(r[2]) for r in pg.sql(args.target, SEQUENCES_Q)}
    owned = {(r[0], r[1]): (r[2], r[3], r[4]) for r in pg.sql(args.target, OWNED_SEQUENCES_Q)}

    seq_rows: list[list[str]] = []
    bad_seq = 0
    for key in sorted(set(s_seq) | set(d_seq)):
        here_s = key in s_seq
        here_d = key in d_seq
        s = s_seq.get(key)
        d = d_seq.get(key)
        feeds = owned.get(key)
        col = f"{label(feeds[0], feeds[1])}.{feeds[2]}" if feeds else "-"
        s_val = "-" if not here_s else (s or "unused")
        d_val = "-" if not here_d else (d or "unused")

        note = ""
        if not here_s or not here_d:
            note = "MISSING ON TARGET" if not here_d else "MISSING ON SOURCE"
        elif s != d:
            note = "MISMATCH"
        elif feeds:
            # The real test: can the next insert collide? Compare the sequence against the live max.
            fs, ft, fc = feeds
            top = pg.one(args.target, f'select max("{fc}")::text from {qname(fs, ft)}')
            if top is not None:
                try:
                    if d is None or int(d) < int(top):
                        note = f"BEHIND max {top}"
                except ValueError:
                    note = ""
        if note:
            bad_seq += 1
            seq_rows.append([label(*key), col, s_val, d_val, note])
        else:
            seq_rows.append([label(*key), col, s_val, d_val, "ok"])

    print(table(["sequence", "feeds", f"{src_name} last_value", f"{dst_name} last_value", ""], seq_rows))
    if bad_seq:
        ok = False
        print(f"\n  {bad_seq} sequence(s) are wrong. A sequence behind its column's max is the quiet failure: "
              "the restore looks fine and the first insert after cutover raises a duplicate key error. Fix with "
              "setval, or redo the restore.")
    elif seq_rows:
        print("\n  Every sequence carried over, and none is behind its column.")
    else:
        print("\n  No sequences in this database.")

    # --- everything else that should have come across
    print("\nOther objects\n")
    s_obj = {r[0]: r[1] for r in pg.sql(args.source, OBJECTS_Q)}
    d_obj = {r[0]: r[1] for r in pg.sql(args.target, OBJECTS_Q)}
    obj_rows = []
    bad_obj = 0
    for k in sorted(set(s_obj) | set(d_obj)):
        s, d = s_obj.get(k, "-"), d_obj.get(k, "-")
        if s == d:
            obj_rows.append([k, s, d, "ok"])
        else:
            bad_obj += 1
            obj_rows.append([k, s, d, "MISMATCH"])
    print(table(["", src_name, dst_name, ""], obj_rows))
    if bad_obj:
        ok = False
        print("\n  Object counts differ. Read the pg_restore output again; --no-owner and --no-acl drop "
              "ownership and grants on purpose, but not indexes, constraints or views.")

    print()
    print("  VERIFY PASSED" if ok else "  VERIFY FAILED")
    return ok


def next_steps(args, ok: bool) -> None:
    heading("Next")
    if not ok:
        print("""  The two databases do not match. Do not cut over.

  1. Read the mismatches above. Row counts off by a little usually mean something was still writing
     to the source during the dump: stop every service that writes and dump again.
  2. Sequences behind their column mean the restore did not carry them. Redo it from a fresh dump
     rather than patching with setval, unless you know exactly which one is wrong and why.
  3. Keep the dump file. Nothing on the source has been touched.""")
        return
    print(f"""  The target matches the source. This script does not do the cutover. You do, in this order:

  1. Keep {args.dump_file} somewhere you can find it.
  2. Point the deployment at the new database (DATABASE_URL) and start it.
  3. Check the health endpoint, then check the migration ledger above. An app that migrates on
     startup may move it forward once on a new deploy. It should not jump by several migrations,
     and it should never go backwards.
  4. Smoke test the paths that write: sign in, create something, run one background job.
  5. Leave the old database running, untouched, until you are sure. Anything ingested into the new
     database after cutover does not exist in the old one.""")


# ---------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="pg_move.py",
        description="Dump one Postgres and restore it into another, then prove they match.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Read-only without --confirm. Never writes to the source.",
    )
    p.add_argument("--source", default=os.environ.get("SOURCE_DATABASE_URL"),
                   help="the database to copy from (or SOURCE_DATABASE_URL)")
    p.add_argument("--target", default=os.environ.get("TARGET_DATABASE_URL"),
                   help="the database to copy into (or TARGET_DATABASE_URL)")
    p.add_argument("--dump-file", default="app.dump", help="where the custom-format dump goes")
    p.add_argument("--step", default="all", choices=("preflight", "dump", "restore", "verify", "all"))
    p.add_argument("--confirm", action="store_true",
                   help="actually dump and restore. Without it the script stops after pre-flight.")
    p.add_argument("--overwrite-target", action="store_true",
                   help="drop and recreate the target's schemas before restoring. Needs --confirm.")
    p.add_argument("--overwrite-dump", action="store_true", help="replace the dump file if it exists")
    p.add_argument("--pg-bin", default=os.environ.get("PG_BIN"),
                   help="directory holding psql, pg_dump and pg_restore (or PG_BIN)")
    args = p.parse_args(argv)

    if not args.source or not args.target:
        p.error("--source and --target are required (or SOURCE_DATABASE_URL and TARGET_DATABASE_URL)")

    pg = Pg(args.pg_bin)

    try:
        src, dst = preflight(pg, args, restoring=args.step in ("restore", "all"))

        if args.step == "preflight":
            print("\n  Pre-flight only. Nothing was changed.")
            return 0

        if args.step == "verify":
            ok = verify(pg, args)
            next_steps(args, ok)
            return 0 if ok else 1

        if not args.confirm:
            heading("Stopping here")
            print(f"""  Pre-flight passed. Nothing has been dumped or restored.

  This would have:
    - dumped {redact(args.source)} to {args.dump_file}
{"    - dropped and recreated the target's schemas" + chr(10) if args.overwrite_target else ""}\
    - restored it into {redact(args.target)}
    - compared every table, the schema migration version and every sequence

  Re-run the same command with --confirm.""")
            return 0

        if args.step in ("dump", "all"):
            dump(pg, args, src)
        if args.step in ("restore", "all"):
            restore(pg, args, src, dst)
        if args.step in ("restore", "all"):
            ok = verify(pg, args)
            next_steps(args, ok)
            return 0 if ok else 1
        return 0

    except Fail as e:
        sys.stdout.flush()
        print(f"\nerror: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        sys.stdout.flush()
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
