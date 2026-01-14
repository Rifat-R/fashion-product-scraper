from __future__ import annotations

import base64
import csv
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class ScanSession:
    created_at: float
    query: str
    results: List[dict] = field(default_factory=list)
    sites_total: int = 0
    sites_done: int = 0
    status: str = "running"
    logs: List[str] = field(default_factory=list)
    export_path: Optional[str] = None
    exported_count: int = 0
    estimated_total: int = 0

    def add_log(self, message: str) -> None:
        self.logs.append(message)
        if len(self.logs) > 200:
            self.logs = self.logs[-200:]


@dataclass
class ScanCache:
    ttl_seconds: int = 900
    _entries: Dict[str, ScanSession] = field(default_factory=dict)

    def create(self, query: str, sites_total: int) -> str:
        scan_id = uuid.uuid4().hex
        self._entries[scan_id] = ScanSession(
            time.time(), query, sites_total=sites_total
        )
        return scan_id

    def get(self, scan_id: str) -> Optional[ScanSession]:
        entry = self._entries.get(scan_id)
        if not entry:
            return None
        if time.time() - entry.created_at > self.ttl_seconds:
            self._entries.pop(scan_id, None)
            return None
        return entry

    def add_results(self, scan_id: str, results: List[dict]) -> None:
        entry = self._entries.get(scan_id)
        if not entry:
            return
        entry.results.extend(results)

    def add_log(self, scan_id: str, message: str) -> None:
        entry = self._entries.get(scan_id)
        if not entry:
            return
        entry.add_log(message)

    def mark_site_done(
        self,
        scan_id: str,
        site_name: str,
        error: Optional[Exception],
        estimated_count: int = 0,
    ) -> None:
        entry = self._entries.get(scan_id)
        if not entry:
            return
        entry.sites_done += 1
        entry.estimated_total += estimated_count
        if error:
            entry.add_log(f"{site_name}: failed ({type(error).__name__})")
        else:
            entry.add_log(f"{site_name}: completed")

    def mark_complete(self, scan_id: str) -> None:
        entry = self._entries.get(scan_id)
        if not entry:
            return
        entry.status = "complete"
        entry.add_log("scan: complete")

    def export_csv(self, scan_id: str, export_dir: str) -> Optional[str]:
        entry = self._entries.get(scan_id)
        if not entry:
            return None

        os.makedirs(export_dir, exist_ok=True)
        if entry.export_path is None:
            entry.export_path = os.path.join(export_dir, f"scan_{scan_id}.csv")

        write_header = not os.path.exists(entry.export_path)
        new_rows = entry.results[entry.exported_count :]
        if not new_rows and os.path.exists(entry.export_path):
            return entry.export_path

        with open(entry.export_path, "a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if write_header:
                writer.writerow(
                    [
                        "site",
                        "name",
                        "price",
                        "url",
                        "sizes",
                        "availability",
                        "description",
                    ]
                )
            for row in new_rows:
                writer.writerow(
                    [
                        row.get("site") or "",
                        row.get("name") or "",
                        row.get("price") or "",
                        row.get("url") or "",
                        ", ".join(row.get("sizes") or []),
                        row.get("availability") or "",
                        row.get("description") or "",
                    ]
                )
        entry.exported_count = len(entry.results)
        return entry.export_path


def encode_cursor(scan_id: str, offset: int) -> str:
    payload = f"{scan_id}:{offset}".encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("utf-8")


def decode_cursor(cursor: str) -> Tuple[str, int]:
    raw = base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8")
    scan_id, offset_text = raw.split(":", 1)
    return scan_id, int(offset_text)
