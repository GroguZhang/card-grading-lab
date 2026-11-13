"""Tiny SQLite persistence layer.

One table is enough: a report is an immutable document plus a few file paths.
Keeping it this small means the whole store can be inspected with a text editor
or copied to another machine without a migration step.

Images live on disk, not in the database. A 3 MB JPEG in a BLOB makes every
backup and every ``SELECT *`` painful for no benefit.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

__all__ = ["Database", "default_db_path"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    report_id      TEXT PRIMARY KEY,
    card_name      TEXT NOT NULL,
    overall        REAL NOT NULL,
    tier           TEXT NOT NULL,
    limiting       TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    payload        TEXT NOT NULL,
    front_image    TEXT,
    back_image     TEXT,
    evidence_image TEXT,
    pdf_path       TEXT
);
CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at DESC);
"""


def default_db_path(root: str | Path = ".") -> Path:
    return Path(root) / "data" / "cards.db"


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save_report(
        self,
        payload: dict[str, Any],
        *,
        front_image: str | None = None,
        back_image: str | None = None,
        evidence_image: str | None = None,
        pdf_path: str | None = None,
    ) -> str:
        rid = payload["report_id"]
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO reports
                    (report_id, card_name, overall, tier, limiting, payload,
                     front_image, back_image, evidence_image, pdf_path)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(report_id) DO UPDATE SET
                    payload=excluded.payload,
                    pdf_path=excluded.pdf_path,
                    evidence_image=excluded.evidence_image
                """,
                (
                    rid,
                    payload.get("card_name", ""),
                    float(payload.get("overall", 0)),
                    payload.get("tier", ""),
                    payload.get("limiting_factor"),
                    json.dumps(payload, ensure_ascii=False),
                    front_image,
                    back_image,
                    evidence_image,
                    pdf_path,
                ),
            )
        return rid

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM reports WHERE report_id = ?", (report_id,)
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["payload"] = json.loads(data["payload"])
        return data

    def list_reports(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT report_id, card_name, overall, tier, limiting, created_at,
                       front_image, pdf_path
                FROM reports ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_pdf(self, report_id: str, pdf_path: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE reports SET pdf_path = ? WHERE report_id = ?",
                (pdf_path, report_id),
            )
