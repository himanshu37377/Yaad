import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from yaad.memory.ledger import LEDGER_SCHEMA
from yaad.memory.records import MEMORY_SCHEMA, backfill_amount_keys

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS folders(id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, cloud_consent INTEGER NOT NULL DEFAULT 0, added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE, path TEXT UNIQUE NOT NULL, sha256 TEXT NOT NULL, kind TEXT NOT NULL, size INTEGER NOT NULL, mtime REAL NOT NULL, status TEXT NOT NULL, error TEXT, partial INTEGER NOT NULL DEFAULT 0, cloud_consent INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS units(id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE, evidence_id TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, locator TEXT NOT NULL, text TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}');
CREATE VIRTUAL TABLE IF NOT EXISTS units_fts USING fts5(text, content='units', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS units_ai AFTER INSERT ON units BEGIN INSERT INTO units_fts(rowid,text) VALUES(new.id,new.text); END;
CREATE TRIGGER IF NOT EXISTS units_ad AFTER DELETE ON units BEGIN INSERT INTO units_fts(units_fts,rowid,text) VALUES('delete',old.id,old.text); END;
CREATE TRIGGER IF NOT EXISTS units_au AFTER UPDATE ON units BEGIN INSERT INTO units_fts(units_fts,rowid,text) VALUES('delete',old.id,old.text); INSERT INTO units_fts(rowid,text) VALUES(new.id,new.text); END;
CREATE TABLE IF NOT EXISTS photo_meta(file_id INTEGER REFERENCES files(id) ON DELETE CASCADE, source TEXT CHECK(source IN('exif','model')), key TEXT, value TEXT, confidence REAL);
CREATE TABLE IF NOT EXISTS photo_descriptions(file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE, schema_version INTEGER NOT NULL, model TEXT, caption TEXT NOT NULL DEFAULT '', people TEXT NOT NULL DEFAULT '[]', objects TEXT NOT NULL DEFAULT '[]', actions TEXT NOT NULL DEFAULT '[]', scene TEXT NOT NULL DEFAULT '{}', visible_text TEXT NOT NULL DEFAULT '', visual_attributes TEXT NOT NULL DEFAULT '[]', uncertainties TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, response TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS unit_embeddings(evidence_id TEXT PRIMARY KEY REFERENCES units(evidence_id) ON DELETE CASCADE, model TEXT NOT NULL, text_hash TEXT NOT NULL, dim INTEGER NOT NULL, vector BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS egress_log(id INTEGER PRIMARY KEY, ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, file_id INTEGER REFERENCES files(id), unit_id INTEGER REFERENCES units(id), purpose TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, bytes INTEGER NOT NULL, content_kind TEXT NOT NULL, status_code INTEGER, success INTEGER, duration_ms INTEGER, input_tokens INTEGER, output_tokens INTEGER, cached_tokens INTEGER, estimated_cost_usd REAL, error_code TEXT);
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, file_id INTEGER REFERENCES files(id) ON DELETE CASCADE, step TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, error TEXT);
CREATE TABLE IF NOT EXISTS index_runs(id INTEGER PRIMARY KEY, folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE, state TEXT NOT NULL, started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, finished_at TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY, evidence_id TEXT, helpful INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_files_folder_status ON files(folder_id, status);
CREATE INDEX IF NOT EXISTS idx_units_file_kind ON units(file_id, kind);
CREATE INDEX IF NOT EXISTS idx_photo_meta_file ON photo_meta(file_id, source);
CREATE INDEX IF NOT EXISTS idx_egress_file ON egress_log(file_id);
"""

class _ClosingConnection(sqlite3.Connection):
    """Connection whose ``with`` block also closes: sqlite3's own
    __exit__ commits/rolls back but leaves the handle (and its file
    descriptors) open, which leaked one fd per connect() call."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._memory_uri: str | None = None
        self._anchor: sqlite3.Connection | None = None
        if self.path == ":memory:":
            # A plain ":memory:" connect() would hand every caller a fresh
            # empty database (schema created in initialize() would vanish).
            # Use a per-instance shared-cache memory DB and hold an anchor
            # connection so it survives between connect() calls.
            import uuid

            self._memory_uri = f"file:yaad-mem-{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._anchor = sqlite3.connect(self._memory_uri, uri=True, timeout=30)
        else:
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        if self._memory_uri is not None:
            db = sqlite3.connect(self._memory_uri, uri=True, timeout=30,
                                 factory=_ClosingConnection)
        else:
            db = sqlite3.connect(self.path, timeout=30,
                                 factory=_ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        if self._memory_uri is None:
            # WAL lets the API and CLI processes read/write concurrently
            # instead of failing with "database is locked". Read-only
            # opens cannot switch journal mode; they keep the default.
            try:
                db.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass
        return db

    def _write_protected(self, db: sqlite3.Connection) -> bool:
        """True when this database file cannot take writes (read-only
        contexts such as tests importing against the home database).
        Probes with a self-cleaning temp table; leaves no trace."""
        if self.path == ":memory:":
            return False
        try:
            db.execute("CREATE TABLE IF NOT EXISTS _yaad_write_probe(x)")
            db.execute("DROP TABLE _yaad_write_probe")
            return False
        except sqlite3.OperationalError:
            return True

    def initialize(self) -> None:
        with self.connect() as db:
            if self._write_protected(db):
                # Read-only context: use the schema as-is. The app path
                # always migrates; anything genuinely missing fails loudly
                # at first use rather than at import.
                return
            db.executescript(SCHEMA)
            # Memory tables are additive (IF NOT EXISTS): existing databases
            # gain them on next open with no data migration; rollback is DROP.
            db.executescript(MEMORY_SCHEMA)
            db.executescript(LEDGER_SCHEMA)
            memory_columns = {row["name"] for row in db.execute("PRAGMA table_info(memory_records)")}
            if "amount_value" not in memory_columns:
                db.execute("ALTER TABLE memory_records ADD COLUMN amount_value TEXT")
            if "amount_raw" not in memory_columns:
                db.execute("ALTER TABLE memory_records ADD COLUMN amount_raw TEXT")
            if "origin" not in memory_columns:
                db.execute("ALTER TABLE memory_records ADD COLUMN origin TEXT NOT NULL DEFAULT 'document'")
            if "event_time" not in memory_columns:
                db.execute("ALTER TABLE memory_records ADD COLUMN event_time TEXT")
            if "effective_time" not in memory_columns:
                db.execute("ALTER TABLE memory_records ADD COLUMN effective_time TEXT")
            backfill_amount_keys(db)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(files)")}
            if "cloud_consent" not in columns:
                db.execute("ALTER TABLE files ADD COLUMN cloud_consent INTEGER NOT NULL DEFAULT 0")
            egress_columns = {row["name"] for row in db.execute("PRAGMA table_info(egress_log)")}
            telemetry_columns = {
                "status_code": "INTEGER", "success": "INTEGER", "duration_ms": "INTEGER",
                "input_tokens": "INTEGER", "output_tokens": "INTEGER", "cached_tokens": "INTEGER",
                "estimated_cost_usd": "REAL", "error_code": "TEXT",
            }
            for name, kind in telemetry_columns.items():
                if name not in egress_columns:
                    db.execute(f"ALTER TABLE egress_log ADD COLUMN {name} {kind}")
            # Reap runs left behind by a crash/kill so questions are not gated
            # on indexing forever. Only old runs are reaped: a recently
            # started run may belong to another live process.
            db.execute("""UPDATE index_runs SET state='complete', finished_at=CURRENT_TIMESTAMP,
                        error='Indexing was interrupted before it finished.'
                        WHERE state='running' AND datetime(started_at) < datetime('now', '-30 minutes')""")
            db.execute("""UPDATE files SET status='failed',
                        error='Indexing was interrupted before this file finished.'
                        WHERE status='discovered'
                        AND folder_id IN (SELECT folder_id FROM index_runs WHERE state != 'running')
                        AND folder_id NOT IN
                        (SELECT folder_id FROM index_runs WHERE state='running'
                         AND datetime(started_at) >= datetime('now', '-30 minutes'))""")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def rebuild_fts(self) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO units_fts(units_fts) VALUES('rebuild')")
