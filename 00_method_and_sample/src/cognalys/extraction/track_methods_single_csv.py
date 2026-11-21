#!/usr/bin/env python3
"""
Track and extract methods from repositories already cloned by extract_methods_single_csv.py.

This script complements extract_methods_single_csv.py when cloning and extraction need to
be split into two steps:

1. extract_methods_single_csv.py clones repositories into a clone directory.
2. This script reads the same CSV, maps those existing clones, extracts methods,
   writes per-method JSON files, and writes tracking/summary reports.
"""

import argparse
import csv
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from cognalys.sources import read_rows
from cognalys.extraction.extract_methods_single_csv import MethodExtractor, RepositoryIndexer
from cognalys.utils.corpus_rows import INPUT_INVALID, invalid_row_record, row_problems


class SingleCsvMethodTracker:
    def __init__(
        self,
        csv_file: str,
        clone_dir: str,
        output_dir: str,
        methods_output_dir: str,
        log_level: str = "INFO",
        limit: int = 0,
    ):
        self.csv_file = Path(csv_file)
        self.clone_dir = Path(clone_dir)
        self.output_dir = Path(output_dir)
        self.methods_output_dir = Path(methods_output_dir)
        self.log_level = getattr(logging, log_level.upper(), logging.INFO)
        self.limit = limit

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.methods_output_dir.mkdir(parents=True, exist_ok=True)

        self.logger = self._setup_logging()

        self.rows: List[Dict] = []
        self.apk_urls: Dict[str, str] = {}
        self.cloned_repos: Dict[str, Path] = {}
        self.results: Dict[str, Dict] = {}
        self.start_time = datetime.now()

    def _setup_logging(self) -> logging.Logger:
        log_dir = self.output_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        logger = logging.getLogger("SingleCsvMethodTracker")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()

        formatter = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        file_handler = logging.FileHandler(
            log_dir / "single_csv_tracking.log",
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(self.log_level)
        console_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

        return logger

    def read_csv(self) -> bool:
        try:
            # utf-8-sig for the same reason as extract_methods_single_csv:
            # a BOM turns the first header into "﻿apk", which silently
            # empties row.get("apk") for every row.
            raw_rows = read_rows(self.csv_file)

            if not raw_rows:
                self.logger.error(f"CSV is empty: {self.csv_file}")
                return False

            self.rows.clear()
            self.apk_urls.clear()

            if self.limit > 0:
                raw_rows = raw_rows[: self.limit]

            for row_idx, row in enumerate(raw_rows):
                problems = row_problems(row)
                if problems:
                    self.results[f"row_{row_idx}"] = invalid_row_record(
                        row_idx, row, problems,
                    )
                    self.logger.warning(
                        "CSV row %s skipped (%s): %s",
                        row_idx, INPUT_INVALID, "; ".join(problems),
                    )
                    continue
                repo_url = (row.get("repo_url") or "").strip()
                apk_url = (row.get("apk_url") or "").strip()
                apk = (row.get("apk") or "").strip()
                repo = (row.get("repo") or "").strip()

                if apk:
                    apk_name = apk
                elif apk_url:
                    apk_name = apk_url.split("/")[-1].strip()
                elif repo:
                    apk_name = self._safe_dir_name(repo) + ".apk"
                else:
                    apk_name = f"row_{row_idx}.apk"

                adapted = dict(row)
                adapted["row_index"] = row_idx
                adapted["apk"] = apk_name
                adapted["repo_url"] = repo_url
                adapted["apk_url"] = apk_url
                adapted["repo"] = repo
                adapted["class"] = row.get("class", "").strip()
                adapted["method"] = row.get("method", "").strip()
                adapted["message"] = row.get("message", row.get("pr_title", "")).strip()
                adapted["unique_msg"] = row.get("unique_msg", row.get("author", "")).strip()

                self.rows.append(adapted)

                if repo_url:
                    self.apk_urls[apk_name] = repo_url

            self.logger.info(f"Loaded {len(self.rows)} CSV rows from {self.csv_file}")
            self.logger.info(f"Mapped {len(set(row['apk'] for row in self.rows))} unique APK names")
            return True

        except Exception as e:
            self.logger.error(f"Error reading CSV {self.csv_file}: {e}")
            return False

    def map_existing_clones(self) -> bool:
        if not self.clone_dir.exists():
            self.logger.error(f"Clone directory does not exist: {self.clone_dir}")
            return False

        self.cloned_repos.clear()

        for row in self.rows:
            apk_name = row["apk"]

            if apk_name in self.cloned_repos:
                continue

            repo_dir = self._find_clone_for_row(row)

            if repo_dir:
                self.cloned_repos[apk_name] = repo_dir
                self.logger.info(f"Found clone for {apk_name}: {repo_dir}")
            else:
                self.logger.warning(f"Missing clone for {apk_name}")

        self.logger.info(f"Mapped {len(self.cloned_repos)} cloned repositories")
        return bool(self.cloned_repos)

    def _find_clone_for_row(self, row: Dict) -> Optional[Path]:
        candidates = [
            self.clone_dir / self._safe_dir_name(row["apk"]),
            self.clone_dir / row["apk"],
        ]

        repo = row.get("repo", "").strip()
        if repo:
            candidates.append(self.clone_dir / self._safe_dir_name(repo))
            candidates.append(self.clone_dir / f"{self._safe_dir_name(repo)}.apk")

        for candidate in candidates:
            if candidate.exists() and (candidate / ".git").exists():
                return candidate

        return None

    def extract_methods(self) -> bool:
        if not self.cloned_repos:
            self.logger.error("No cloned repositories mapped")
            return False

        success_count = 0
        failed_count = 0
        skipped_count = 0

        rows_by_apk: Dict[str, List[Dict]] = {}
        for row in self.rows:
            rows_by_apk.setdefault(row["apk"], []).append(row)

        for apk_name, apk_rows in rows_by_apk.items():
            repo_path = self.cloned_repos.get(apk_name)

            if not repo_path:
                for row in apk_rows:
                    self._record_result(
                        row,
                        {
                            "status": "failed",
                            "reason": "repository_not_cloned",
                        },
                    )
                    failed_count += 1
                continue

            indexer = RepositoryIndexer(repo_path, self.logger)
            indexer.build_index()
            extractor = MethodExtractor(self.logger)

            apk_output_dir = self.methods_output_dir / self._safe_dir_name(apk_name)
            apk_output_dir.mkdir(parents=True, exist_ok=True)

            for row in apk_rows:
                class_name = row.get("class", "").strip()
                method_name = row.get("method", "").strip()

                if not class_name or not method_name:
                    self._record_result(
                        row,
                        {
                            "status": "skipped",
                            "reason": "missing_class_or_method",
                        },
                    )
                    skipped_count += 1
                    continue

                file_path = indexer.find_file_for_class(class_name)

                if not file_path:
                    self._record_result(
                        row,
                        {
                            "status": "failed",
                            "class": class_name,
                            "method": method_name,
                            "reason": "class_not_found",
                        },
                    )
                    failed_count += 1
                    continue

                content = indexer.get_file_content(file_path)

                if not content:
                    self._record_result(
                        row,
                        {
                            "status": "failed",
                            "class": class_name,
                            "method": method_name,
                            "source_file": str(file_path),
                            "reason": "source_file_unreadable",
                        },
                    )
                    failed_count += 1
                    continue

                extracted = extractor.extract_method_from_java(content, method_name)

                if not extracted:
                    self._record_result(
                        row,
                        {
                            "status": "failed",
                            "class": class_name,
                            "method": method_name,
                            "source_file": str(file_path),
                            "reason": "method_not_found",
                        },
                    )
                    failed_count += 1
                    continue

                method_body, start_line, end_line = extracted
                json_path = self._write_method_json(
                    row=row,
                    repo_path=repo_path,
                    file_path=file_path,
                    method_body=method_body,
                    start_line=start_line,
                    end_line=end_line,
                    output_dir=apk_output_dir,
                )

                self._record_result(
                    row,
                    {
                        "status": "success",
                        "class": class_name,
                        "method": method_name,
                        "source_file": str(file_path),
                        "json_output": str(json_path),
                    },
                )
                success_count += 1

        self.logger.info(
            f"Extraction complete: {success_count} success, "
            f"{failed_count} failed, {skipped_count} skipped"
        )

        return success_count > 0 or skipped_count > 0

    def _write_method_json(
        self,
        row: Dict,
        repo_path: Path,
        file_path: Path,
        method_body: str,
        start_line: int,
        end_line: int,
        output_dir: Path,
    ) -> Path:
        row_index = row["row_index"]
        apk_name = row["apk"]
        class_name = row.get("class", "").strip()
        method_name = row.get("method", "").strip()

        json_data = {
            "extraction_metadata": {
                "csv_index": row_index,
                "apk_name": apk_name,
                "repository_url": row.get("repo_url", ""),
                "repository_path": str(repo_path),
                "extraction_status": "success",
                "extraction_timestamp": datetime.now().isoformat(),
            },
            "error": {
                "csv_class": class_name,
                "csv_method": method_name,
                "error_message": row.get("message", ""),
                "unique_error_id": row.get("unique_msg", ""),
            },
            "class_info": {
                "full_name": class_name,
                "relative_method_file": self._safe_relative_path(file_path, Path.cwd()),
                "source_file_path": self._safe_relative_path(file_path, repo_path),
            },
            "method_info": {
                "name": method_name,
            },
            "source_code": {
                "method_body": method_body,
                "start_line": start_line,
                "end_line": end_line,
            },
        }

        json_filename = f"{row_index}_{self._safe_dir_name(method_name)}.json"
        json_path = output_dir / json_filename

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)

        return json_path

    def _record_result(self, row: Dict, result: Dict) -> None:
        key = f"row_{row['row_index']}"
        merged = {
            "apk": row["apk"],
            "repo_url": row.get("repo_url", ""),
            "row_index": row["row_index"],
        }
        merged.update(result)
        self.results[key] = merged

    def write_tracking_json(self) -> None:
        duration = (datetime.now() - self.start_time).total_seconds()

        success = sum(1 for r in self.results.values() if r.get("status") == "success")
        failed = sum(1 for r in self.results.values() if r.get("status") == "failed")
        skipped = sum(1 for r in self.results.values() if r.get("status") == "skipped")
        total_rows = len(self.rows)

        tracking_data = {
            "extraction_summary": {
                "total_rows": total_rows,
                "successfully_extracted": success,
                "failed_to_extract": failed,
                "skipped": skipped,
                "extraction_rate_percent": round(100 * success / total_rows, 2)
                if total_rows
                else 0,
                "timestamp": datetime.now().isoformat(),
                "duration_seconds": round(duration, 2),
            },
            "repository_summary": {
                "clone_dir": str(self.clone_dir),
                "repositories_expected": len(set(row["apk"] for row in self.rows)),
                "repositories_mapped": len(self.cloned_repos),
            },
            "output": {
                "output_dir": str(self.output_dir),
                "methods_output_dir": str(self.methods_output_dir),
            },
            "extraction_results": self.results,
        }

        tracking_path = self.output_dir / "extraction_tracking.json"

        with open(tracking_path, "w", encoding="utf-8") as f:
            json.dump(tracking_data, f, indent=2, ensure_ascii=False)

        self.logger.info(f"Tracking JSON written to {tracking_path}")

    def write_summary(self) -> None:
        summary_path = self.output_dir / "logs" / "summary_tracked.txt"

        success = sum(1 for r in self.results.values() if r.get("status") == "success")
        failed = sum(1 for r in self.results.values() if r.get("status") == "failed")
        skipped = sum(1 for r in self.results.values() if r.get("status") == "skipped")

        by_apk: Dict[str, Dict[str, int]] = {}
        for result in self.results.values():
            apk = result.get("apk", "unknown")
            by_apk.setdefault(apk, {"success": 0, "failed": 0, "skipped": 0})
            status = result.get("status")
            if status in by_apk[apk]:
                by_apk[apk][status] += 1

        with open(summary_path, "w", encoding="utf-8") as f:
            f.write("=" * 70 + "\n")
            f.write("SINGLE CSV METHOD TRACKING SUMMARY\n")
            f.write("=" * 70 + "\n\n")
            f.write(f"CSV file: {self.csv_file}\n")
            f.write(f"Clone dir: {self.clone_dir}\n")
            f.write(f"Output dir: {self.output_dir}\n")
            f.write(f"Methods output dir: {self.methods_output_dir}\n\n")
            f.write(f"CSV rows loaded: {len(self.rows)}\n")
            f.write(f"Repositories mapped: {len(self.cloned_repos)}\n")
            f.write(f"Methods extracted: {success}\n")
            f.write(f"Failed: {failed}\n")
            f.write(f"Skipped: {skipped}\n\n")
            f.write("Results by APK:\n")
            for apk, counts in sorted(by_apk.items()):
                f.write(
                    f"  {apk}: {counts['success']} success, "
                    f"{counts['failed']} failed, {counts['skipped']} skipped\n"
                )

        self.logger.info(f"Summary written to {summary_path}")

    def run(self) -> bool:
        self.logger.info("Starting tracked extraction from existing clones")
        self.logger.info(f"CSV file: {self.csv_file}")
        self.logger.info(f"Clone dir: {self.clone_dir}")
        self.logger.info(f"Output dir: {self.output_dir}")
        self.logger.info(f"Methods output dir: {self.methods_output_dir}")

        if not self.read_csv():
            return False

        self.map_existing_clones()
        self.extract_methods()
        self.write_tracking_json()
        self.write_summary()

        return True

    @staticmethod
    def _safe_dir_name(name: str) -> str:
        name = name.strip()
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
        return name or "unknown"

    @staticmethod
    def _safe_relative_path(path: Path, base: Path) -> str:
        try:
            return str(path.resolve().relative_to(base.resolve()))
        except Exception:
            return str(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Track/extract methods from a single CSV using already cloned repositories."
    )
    parser.add_argument(
        "--csv-file",
        required=True,
        help="CSV with at least class/method and repository identity columns.",
    )
    parser.add_argument(
        "--clone-dir",
        required=True,
        help="Directory containing repositories already cloned by extract_methods_single_csv.py.",
    )
    parser.add_argument(
        "--output-dir",
        default="single_csv_tracking",
        help="Directory for logs, tracking JSON and summary.",
    )
    parser.add_argument(
        "--methods-output-dir",
        default="single_csv_methods_tracked",
        help="Directory where per-method JSON files will be written.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Console log level.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process only the first N CSV rows. 0 means no limit.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    tracker = SingleCsvMethodTracker(
        csv_file=args.csv_file,
        clone_dir=args.clone_dir,
        output_dir=args.output_dir,
        methods_output_dir=args.methods_output_dir,
        log_level=args.log_level,
        limit=args.limit,
    )

    return 0 if tracker.run() else 1


if __name__ == "__main__":
    sys.exit(main())
