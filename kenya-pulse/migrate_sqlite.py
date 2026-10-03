"""Import a consistent SQLite snapshot into EMPTY PostgreSQL tables, without deleting data.

Default is a rollback-only rehearsal. Set DATABASE_URL securely outside shell history.
Run with --source /secure/backup.db, then --apply after checking the counts.
Never run against a live destination accepting participation during cutover.
"""
import argparse
import sqlite3
from pathlib import Path

TABLES = ('candidates', 'candidate_aliases', 'pulse_votes', 'pulse_visits', 'ad_orders', 'support_contributions', 'ground_issues', 'ground_confirmations')
BOOLEAN_COLUMNS = {('candidates', 'active'), ('candidates', 'identity_verified'), ('candidate_aliases', 'verified')}


def migrate(source_path, conn, apply=False):
    source = sqlite3.connect(Path(source_path).resolve().as_uri() + '?mode=ro', uri=True)
    snapshot = sqlite3.connect(':memory:')
    try:
        source.backup(snapshot)
    finally:
        source.close()
    snapshot.row_factory = sqlite3.Row
    counts = {}
    try:
        with conn() as target:
            if not target.pg:
                raise ValueError('Destination must be PostgreSQL')
            target.execute('SELECT pg_advisory_xact_lock(706857421)')
            for table in TABLES:
                target.execute('LOCK TABLE ' + table + ' IN EXCLUSIVE MODE')
                if target.execute('SELECT count(*) n FROM ' + table).fetchone()['n']:
                    raise ValueError('Destination contains records; refusing to overwrite or merge: ' + table)
            for table in TABLES:
                if not snapshot.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    counts[table] = 0
                    continue
                columns = [r['name'] for r in snapshot.execute('PRAGMA table_info(' + table + ')')]
                allowed = {r['column_name'] for r in target.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?", (table,)).fetchall()}
                if set(columns) - allowed:
                    raise ValueError('Unknown source columns; manual review required: ' + table)
                # Column names are checked against the actual fixed destination schema.
                query = 'INSERT INTO ' + table + '(' + ','.join(columns) + ') VALUES(' + ','.join('?' for _ in columns) + ')'
                count = 0
                for row in snapshot.execute('SELECT * FROM ' + table):
                    values = [bool(row[col]) if (table, col) in BOOLEAN_COLUMNS else row[col] for col in columns]
                    target.execute(query, values)
                    count += 1
                if target.execute('SELECT count(*) n FROM ' + table).fetchone()['n'] != count:
                    raise ValueError('Row count mismatch: ' + table)
                counts[table] = count
            if apply:
                for table in TABLES:
                    target.execute("SELECT setval(pg_get_serial_sequence(?, 'id'),COALESCE((SELECT MAX(id) FROM " + table + "),1),(SELECT count(*)>0 FROM " + table + "))", (table,))
                target.execute('INSERT INTO participation_slots(fp,county,race) SELECT DISTINCT fp,county,race FROM pulse_votes WHERE TRUE ON CONFLICT(fp,county,race) DO NOTHING')
            else:
                target.c.rollback()
        return counts
    finally:
        snapshot.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        from app import conn, init, SCHEMA_OK
        if not SCHEMA_OK:
            raise ValueError('Destination schema unavailable')
        counts = migrate(args.source, conn, args.apply)
        if args.apply:
            init()  # Populate normalized names for historical registry rows, without verifying identities.
        print({'applied': args.apply, 'row_counts': counts})
    except Exception:
        # Database exceptions can contain private preference data or connection credentials.
        print('Migration not completed. Destination must be PostgreSQL with an empty, current schema. Review backup and schema privately; no records are overwritten.')
        raise SystemExit(1)
