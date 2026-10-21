"""Offline integration checks using original, synthetic caption text only."""
import contextlib
import csv
import io
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import collector


def source(count=5):
    return {
        "channel_url": collector.DEFAULT_CHANNEL,
        "sections": [{"tab": tab, "state": "listed", "limited_to": None}
                     for tab in ("videos", "shorts", "streams")],
        "videos": [{"video_id": f"demo{i:07d}", "title": f"Synthetic example {i}",
                    "url": f"https://www.youtube.com/watch?v=demo{i:07d}",
                    "publication_date": None, "tabs": ["videos"]} for i in range(count)],
    }


def captions(*_):
    return {"language": "en", "method": "synthetic_test", "captured_at": collector.now(),
            "segments": [{"start": 0.125, "duration": 1.5, "text": "Original synthetic test sentence."},
                         {"start": 2.0, "duration": 1.0, "text": "End of synthetic example."}]}


class Scenarios(unittest.TestCase):
    def run_scenario(self, directory, options, fetch=captions):
        args = ["collector.py", "--output", str(directory)] + options
        with patch.object(sys, "argv", args), patch.object(collector, "inventory", return_value=source()), \
             patch.object(collector, "make_api", return_value=object()), \
             patch.object(collector, "fetch_english", side_effect=fetch), \
             patch.object(collector.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            return collector.main()

    def test_pilot_remains_partial_and_timestamped_zip_matches_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pilot"
            self.assertEqual(self.run_scenario(root, ["--pilot", "3"]), 0)
            summary = json.loads((root / "summary.json").read_text())
            self.assertEqual(summary["successful"], 3)
            self.assertFalse(summary["complete"])
            self.assertEqual(summary["gaps"], 2)
            self.assertEqual(len(list((root / "transcripts").glob("*.txt"))), 3)
            self.assertIn("00:00:00,125 --> 00:00:01,625", (root / "timestamped/demo0000000.srt").read_text())
            (root / ".env").write_text("SYNTHETIC_SECRET=not-a-real-key")
            (root / ".supadata-api-key").write_text("SYNTHETIC_NOT_A_REAL_KEY")
            archive = collector.package(root)
            with zipfile.ZipFile(archive) as zipped:
                self.assertIsNone(zipped.testzip())
                self.assertFalse(any(".env" in n for n in zipped.namelist()))
                self.assertFalse(any(".supadata-api-key" in n for n in zipped.namelist()))
                for name in zipped.namelist():
                    self.assertEqual(zipped.read(name), (root.parent / name).read_bytes())

    def test_block_preserves_first_success_and_does_not_request_remaining_items(self):
        calls = []
        from youtube_transcript_api._errors import RequestBlocked

        def blocked(_api, video_id):
            calls.append(video_id)
            if len(calls) == 2:
                raise RequestBlocked(video_id)
            return captions()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "blocked"
            self.assertEqual(self.run_scenario(root, ["--all"], blocked), 2)
            self.assertEqual(len(calls), 2)
            outcomes = json.loads((root / "progress.json").read_text())["outcomes"]
            self.assertEqual([x["status"] for x in outcomes],
                             ["success", "blocked"] + ["not_attempted_after_block"] * 3)
            self.assertTrue((root / "transcripts/demo0000000.txt").exists())
            self.assertFalse(json.loads((root / "summary.json").read_text())["complete"])

    def test_probe_leaves_no_caption_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "probe"
            self.assertEqual(self.run_scenario(root, ["--pilot", "3", "--probe-only"]), 0)
            self.assertFalse((root / "transcripts").exists())
            self.assertFalse((root / "timestamped").exists())
            for path in root.rglob("*"):
                if path.is_file():
                    self.assertNotIn("Original synthetic test sentence", path.read_text(encoding="utf-8-sig"))

    def test_failed_caption_does_not_silently_reduce_inventory(self):
        def missing(_api, video_id):
            if video_id == "demo0000002":
                raise ValueError("No English caption track")
            return captions()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "missing"
            self.assertEqual(self.run_scenario(root, ["--all"], missing), 2)
            summary = json.loads((root / "summary.json").read_text())
            self.assertEqual((summary["inventory_count"], summary["successful"], summary["gaps"]), (5, 4, 1))

    def test_resume_retries_gaps_without_refetching_successes(self):
        def missing(_api, video_id):
            if video_id == "demo0000002":
                raise ValueError("No English caption track")
            return captions()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "resume"
            self.assertEqual(self.run_scenario(root, ["--all"], missing), 2)
            called = []
            def recovered(_api, vid):
                called.append(vid)
                return captions()
            args = ["collector.py", "--resume", str(root), "--all"]
            with patch.object(sys, "argv", args), patch.object(collector, "make_api", return_value=object()), \
                 patch.object(collector, "fetch_english", side_effect=recovered), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(collector.main(), 0)
            self.assertEqual(called, ["demo0000002"])
            self.assertTrue(json.loads((root / "summary.json").read_text())["complete"])

    def test_external_csv_titles_cannot_become_spreadsheet_formulas(self):
        exported = collector.csv_text([{"title": "=1+1", "video_id": "-abcdefghij"}], ["title", "video_id"])
        row = next(csv.DictReader(io.StringIO(exported)))
        self.assertEqual(row["title"], "'=1+1")
        self.assertEqual(row["video_id"], "'-abcdefghij")

    @unittest.skipUnless(shutil.which("zsh"), "The macOS launcher requires zsh")
    def test_launcher_resume_ignores_newer_inventory_only_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "Run Collector.command"
            launcher.write_bytes(Path(__file__).with_name("Run Collector.command").read_bytes())
            launcher.chmod(0o755)
            runtime = root / ".venv/bin/python"
            runtime.parent.mkdir(parents=True)
            runtime.write_text(
                '#!/bin/sh\nif [ "$1" = collector.py ]; then\n'
                '  printf "COLLECTOR_ARGS:%s\\n" "$@"\n  exit 0\nfi\n'
                f'exec {shlex.quote(sys.executable)} "$@"\n')
            runtime.chmod(0o755)
            for name, mode in [("run-20260101", "collect"), ("run-20261231", "inventory")]:
                run = root / "output" / name
                run.mkdir(parents=True)
                (run / "progress.json").write_text(json.dumps({"mode": mode}))
                (run / "inventory.json").write_text(json.dumps({"channel_url": collector.DEFAULT_CHANNEL}))
            result = subprocess.run([shutil.which("zsh"), str(launcher)], input="4\n\n", text=True,
                                    capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("COLLECTOR_ARGS:--resume", result.stdout)
            self.assertIn("COLLECTOR_ARGS:" + str((root / "output/run-20260101").resolve()), result.stdout)
            self.assertIn("COLLECTOR_ARGS:--all", result.stdout)
            self.assertIn("COLLECTOR_ARGS:--delay\nCOLLECTOR_ARGS:20", result.stdout)
            self.assertNotIn("Resuming: " + str((root / "output/run-20261231").resolve()), result.stdout)
            (root / "supadata_adapter.py").write_text('def read_key():\n    return "synthetic-test-key"\n')
            hosted = subprocess.run([shutil.which("zsh"), str(launcher)], input="6\n\n", text=True,
                                    capture_output=True, timeout=15)
            self.assertEqual(hosted.returncode, 0, hosted.stderr)
            self.assertIn("COLLECTOR_ARGS:--use-available-credits", hosted.stdout)
            self.assertIn("COLLECTOR_ARGS:--provider\nCOLLECTOR_ARGS:supadata", hosted.stdout)
            self.assertIn("COLLECTOR_ARGS:" + str((root / "output/run-20260101").resolve()), hosted.stdout)
            replacement = subprocess.run([shutil.which("zsh"), str(launcher)], input="7\n\n", text=True,
                                         capture_output=True, timeout=15)
            self.assertEqual(replacement.returncode, 0, replacement.stderr)
            self.assertIn("COLLECTOR_ARGS:--configure-supadata", replacement.stdout)
            self.assertIn("COLLECTOR_ARGS:--use-available-credits", replacement.stdout)
            self.assertIn("COLLECTOR_ARGS:" + str((root / "output/run-20260101").resolve()), replacement.stdout)


if __name__ == "__main__":
    unittest.main()
