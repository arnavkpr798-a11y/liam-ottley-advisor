#!/usr/bin/env python3
"""YouTube caption collector: local extraction or an optional Supadata connection."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_CHANNEL = "https://www.youtube.com/@LiamOttley"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def channel_base(value):
    parsed = urlparse(value)
    parts = parsed.path.strip("/").split("/")
    if parsed.scheme != "https" or parsed.netloc not in {"youtube.com", "www.youtube.com"}:
        raise ValueError("Use an HTTPS youtube.com channel URL.")
    if parts[0].startswith("@") and re.fullmatch(r"@[\w.-]+", parts[0]):
        return "https://www.youtube.com/" + parts[0]
    if len(parts) >= 2 and parts[0] == "channel" and re.fullmatch(r"UC[\w-]+", parts[1]):
        return "https://www.youtube.com/channel/" + parts[1]
    raise ValueError("Use a channel handle or /channel/UC... URL.")


def inventory(channel, tabs, limit=None):
    from yt_dlp import YoutubeDL

    rows = {}
    sections = []
    identity = {}
    for tab in tabs:
        url = f"{channel}/{tab}"
        print(f"Listing {tab}...", flush=True)
        options = dict(extract_flat=True, skip_download=True, quiet=True,
                       retries=0, extractor_retries=0, socket_timeout=20,
                       ignoreerrors=False)
        if limit:
            options["playlistend"] = limit
        try:
            with YoutubeDL(options) as ydl:
                data = ydl.extract_info(url, download=False)
            if not data:
                raise ValueError("No channel listing returned")
            entries = list(data.get("entries") or [])
            if data.get("channel_id"):
                identity = {"channel_id": data["channel_id"], "name": data.get("channel")}
            accepted = 0
            for item in entries:
                if not item or not VIDEO_ID.fullmatch(item.get("id", "")):
                    continue
                vid = item["id"]
                if vid not in rows:
                    stamp = item.get("timestamp")
                    date = item.get("upload_date")
                    if not date and stamp:
                        date = datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
                    rows[vid] = dict(video_id=vid, title=item.get("title", ""),
                                     url=f"https://www.youtube.com/watch?v={vid}",
                                     publication_date=date, duration_seconds=item.get("duration"),
                                     tabs=[])
                if tab not in rows[vid]["tabs"]:
                    rows[vid]["tabs"].append(tab)
                accepted += 1
            sections.append(dict(tab=tab, url=url, state="listed", count=accepted,
                                 limited_to=limit))
        except Exception as exc:
            sections.append(dict(tab=tab, url=url, state="failed",
                                 error_type=type(exc).__name__, error=str(exc)[:1000]))
            print(f"  {tab}: failed; recorded in inventory.json", flush=True)
    return dict(channel_url=channel, identity=identity, captured_at=now(),
                sections=sections, videos=list(rows.values()),
                coverage="Public tab snapshot only; private/deleted/unlisted items are not enumerated.")


def validate_inventory(data):
    if not isinstance(data.get("videos"), list):
        raise ValueError("Inventory must contain a videos array")
    seen = set()
    for row in data["videos"]:
        vid = row.get("video_id", "")
        if not VIDEO_ID.fullmatch(vid) or vid in seen:
            raise ValueError("Invalid or duplicate video ID in inventory")
        seen.add(vid)
    return data


def select_pilot(rows, count):
    # Sample across the inventory instead of testing only adjacent new uploads.
    if len(rows) <= count:
        return rows[:]
    if count == 1:
        return rows[:1]
    return [rows[round(i * (len(rows) - 1) / (count - 1))] for i in range(count)]


def make_api():
    import requests
    from youtube_transcript_api import YouTubeTranscriptApi

    class BoundedSession(requests.Session):
        def request(self, *args, **kwargs):
            kwargs.setdefault("timeout", (10, 25))
            return super().request(*args, **kwargs)

    return YouTubeTranscriptApi(http_client=BoundedSession())


def fetch_english(api, video_id):
    tracks = [t for t in api.list(video_id)
              if t.language_code.lower().split("-")[0] == "en"]
    if not tracks:
        raise ValueError("No English caption track available; no automatic translation attempted")
    tracks.sort(key=lambda t: t.is_generated)
    result = tracks[0].fetch()
    segments = result.to_raw_data()
    if not segments or not any(s.get("text", "").strip() for s in segments):
        raise ValueError("Empty caption track")
    for segment in segments:
        for key in ("start", "duration"):
            value = segment.get(key)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("Invalid caption timestamp")
    return dict(language=result.language_code,
                method="youtube_auto_captions" if result.is_generated else "youtube_manual_captions",
                captured_at=now(), segments=segments)


def timestamp(seconds):
    ms = round(seconds * 1000)
    h, rest = divmod(ms, 3600000)
    m, rest = divmod(rest, 60000)
    s, ms = divmod(rest, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def save_transcript(root, row, result):
    vid = row["video_id"]
    write_json(root / "timestamped" / f"{vid}.json", {**row, **result})
    text_dir = root / "transcripts"
    text_dir.mkdir(exist_ok=True)
    header = (f"{row.get('title', '')}\n{row['url']}\n"
              f"Published: {row.get('publication_date') or 'unknown'}\n"
              f"Source: {result['method']} ({result['language']})\n"
              "Caption errors may remain. Original source wording is retained.\n\n")
    body = "\n".join(s["text"] for s in result["segments"])
    (text_dir / f"{vid}.txt").write_text(header + body + "\n", encoding="utf-8")
    srt = "\n\n".join(
        f"{i}\n{timestamp(s['start'])} --> {timestamp(s['start'] + s['duration'])}\n{s['text']}"
        for i, s in enumerate(result["segments"], 1))
    (root / "timestamped" / f"{vid}.srt").write_text(srt + "\n", encoding="utf-8")


def classify_error(exc):
    name = type(exc).__name__
    response = getattr(exc, "response", None)
    http_status = getattr(response, "status_code", None)
    stop = bool(getattr(exc, "stop_run", False)) or name in {"RequestBlocked", "IpBlocked", "TooManyRequests", "ConnectionError", "Timeout", "ReadTimeout", "ConnectTimeout"}
    stop = stop or http_status in {403, 429}
    return {"status": "blocked" if stop else "failed", "error_type": name,
            "error": str(exc)[:1500], "stop_run": stop}


def csv_text(rows, fields):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        safe = {}
        for field in fields:
            value = row.get(field)
            if isinstance(value, list):
                value = ";".join(value)
            # External titles/errors should remain text when opened in a spreadsheet.
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            safe[field] = value
        writer.writerow(safe)
    return stream.getvalue()


def export(root, data, outcomes, mode):
    write_json(root / "progress.json", dict(mode=mode, updated_at=now(), outcomes=outcomes))
    mapped = {r["video_id"]: r for r in outcomes}
    rows = [{**r, **mapped.get(r["video_id"], {"status": "not_selected"})} for r in data["videos"]]
    fields = ["video_id", "title", "url", "publication_date", "duration_seconds", "tabs",
              "status", "language", "method", "segment_count", "error_type", "error"]
    (root / "index.csv").write_text(csv_text(rows, fields), encoding="utf-8-sig")
    gaps = [r for r in rows if r["status"] not in {"success", "probe_success"}]
    (root / "gaps.csv").write_text(csv_text(gaps, fields), encoding="utf-8-sig")
    good = sum(r["status"] in {"success", "probe_success"} for r in rows)
    summary = dict(mode=mode, updated_at=now(), inventory_count=len(rows), successful=good,
                   selected_count=len(outcomes), gaps=len(gaps),
                   tab_errors=[s for s in data.get("sections", []) if s["state"] != "listed"],
                   complete=bool(rows) and good == len(rows) and not gaps
                   and all(s["state"] == "listed" and not s.get("limited_to")
                           for s in data.get("sections", [])) and mode == "collect")
    write_json(root / "summary.json", summary)
    (root / "README.txt").write_text(
        f"YouTube caption collection\nChannel: {data.get('channel_url', 'unknown')}\n"
        f"Mode: {mode}\nListed: {len(rows)}; successful: {good}; gaps/unselected: {len(gaps)}\n"
        f"Full inventory collected: {summary['complete']}\n\n"
        "Inventory is a public snapshot, not proof that every historical upload is accessible.\n"
        "Missing publication dates remain unknown. Captions may contain errors.\n"
        "probe_success means access/structure checked without saving transcript text.\n"
        "No audio/video downloads or speech-to-text fallback are performed.\n"
        "A configured Supadata run consumes that account's API credits.\n"
        "Retain attribution and distinguish the speaker's claims from verified facts.\n"
        "Read inventory.json for failed tabs and index.csv/gaps.csv for coverage.\n",
        encoding="utf-8")
    if mode == "collect":
        combined = []
        for row in rows:
            if row["status"] == "success":
                combined.append((root / "transcripts" / f"{row['video_id']}.txt").read_text(encoding="utf-8"))
        (root / "combined.txt").write_text("\n\n====================\n\n".join(combined), encoding="utf-8")
    return summary


def package(root):
    # Archive only collector outputs, never a sibling .env, cookies or runtime.
    names = ["inventory.json", "progress.json", "index.csv", "gaps.csv", "summary.json", "README.txt", "combined.txt"]
    paths = [root / n for n in names if (root / n).is_file()]
    for folder in ("transcripts", "timestamped"):
        paths.extend(p for p in (root / folder).glob("*") if p.is_file()
                     and p.suffix in {".txt", ".json", ".srt"} and VIDEO_ID.fullmatch(p.stem))
    destination = root.parent / (root.name + ".zip")
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            if path.is_symlink():
                raise ValueError("Refusing to archive symbolic links")
            archive.write(path, str(Path(root.name) / path.relative_to(root)))
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip():
            raise ValueError("ZIP verification failed")
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(".zip.sha256").write_text(f"{digest}  {destination.name}\n", encoding="utf-8")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", default=DEFAULT_CHANNEL)
    parser.add_argument("--tabs", nargs="+", choices=["videos", "shorts", "streams"], default=["videos", "shorts", "streams"])
    parser.add_argument("--inventory", type=Path, help="Use a previously saved inventory.json")
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--inventory-limit", type=int, help="Limit items PER TAB; incomplete inventory")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--pilot", type=int, default=3)
    scope.add_argument("--all", action="store_true", help="Explicitly collect every listed item")
    parser.add_argument("--probe-only", action="store_true", help="Test access without retaining caption text")
    parser.add_argument("--delay", type=float, default=4.0)
    parser.add_argument("--output", type=Path, help="New output directory (never overwrites an existing run)")
    parser.add_argument("--resume", type=Path, help="Resume an existing collection directory; keep successful files")
    parser.add_argument("--provider", choices=["local", "supadata"], default="local")
    parser.add_argument("--max-new", type=int, help="Limit newly attempted items; Supadata defaults to 3")
    parser.add_argument("--configure-supadata", action="store_true", help="Save an API key locally without making requests")
    parser.add_argument("--use-available-credits", action="store_true", help="Supadata: cap new attempts to the reported starting credit balance")
    args = parser.parse_args()
    if args.configure_supadata:
        from supadata_adapter import configure_key
        configure_key()
        return 0
    if args.max_new is not None and args.max_new < 1:
        parser.error("--max-new must be positive")
    if args.use_available_credits and args.provider != "supadata":
        parser.error("--use-available-credits requires --provider supadata")
    if args.pilot < 1 or args.delay < 1 or (args.inventory_limit is not None and args.inventory_limit < 1):
        parser.error("Pilot and inventory limits must be positive; delay must be at least one second")
    if args.resume and (args.output or args.inventory or args.inventory_only or args.probe_only):
        parser.error("--resume cannot be combined with output, inventory, inventory-only or probe-only")
    channel = channel_base(args.channel)
    api = None
    if args.provider == "supadata" and not args.inventory_only:
        from supadata_adapter import Client
        api = Client()
        if args.use_available_credits:
            allowance = api.allowance()
            print(f"Supadata credit balance at start: {allowance['remaining']}", flush=True)
            if allowance["remaining"] == 0:
                print("No available credits. Existing collection is unchanged; no caption request sent.")
                return 0
            args.max_new = min(args.max_new or allowance["remaining"], allowance["remaining"])
        elif args.max_new is None:
            args.max_new = 3
    root = args.resume or args.output or Path(__file__).resolve().parent / "output" / datetime.now().strftime("run-%Y%m%d-%H%M%S-%f")
    root = root.resolve()
    outcomes = []
    if args.resume:
        data = validate_inventory(json.loads((root / "inventory.json").read_text(encoding="utf-8")))
        progress = json.loads((root / "progress.json").read_text(encoding="utf-8"))
        if data.get("channel_url") != channel or progress.get("mode") != "collect":
            parser.error("Only a collection run for the same channel can be resumed")
        for record in progress["outcomes"]:
            vid = record["video_id"]
            if record["status"] == "success" and all((root / f).is_file() for f in
                (f"transcripts/{vid}.txt", f"timestamped/{vid}.json", f"timestamped/{vid}.srt")):
                outcomes.append(record)
    else:
        root.mkdir(parents=True, exist_ok=False)
    if args.resume:
        pass
    elif args.inventory:
        data = validate_inventory(json.loads(args.inventory.read_text(encoding="utf-8")))
        if data.get("channel_url") != channel:
            parser.error("Saved inventory channel does not match --channel")
    else:
        data = validate_inventory(inventory(channel, args.tabs, args.inventory_limit))
    write_json(root / "inventory.json", data)
    mode = "inventory" if args.inventory_only else "probe" if args.probe_only else "collect"
    stopped = False
    if not args.inventory_only and data["videos"]:
        selected = data["videos"] if args.all else select_pilot(data["videos"], args.pilot)
        completed_ids = {r["video_id"] for r in outcomes}
        selected = [r for r in selected if r["video_id"] not in completed_ids]
        if args.max_new is not None:
            selected = selected[:args.max_new]
        if api is None:
            api = make_api()
        try:
            for i, row in enumerate(selected):
                base = {"video_id": row["video_id"], "checked_at": now()}
                if stopped:
                    outcomes.append({**base, "status": "not_attempted_after_block"})
                    continue
                if i:
                    time.sleep(args.delay)
                print(f"[{i + 1}/{len(selected)}] {row['video_id']}", flush=True)
                try:
                    result = api.fetch(row["video_id"]) if args.provider == "supadata" else fetch_english(api, row["video_id"])
                    if not args.probe_only:
                        save_transcript(root, row, result)
                    outcomes.append({**base, "status": "probe_success" if args.probe_only else "success",
                                     "language": result["language"], "method": result["method"],
                                     "segment_count": len(result["segments"])})
                except Exception as exc:
                    failure = classify_error(exc)
                    stopped = failure.pop("stop_run")
                    outcomes.append({**base, **failure})
                    print(f"  {failure['status']}: {failure['error_type']}", flush=True)
                export(root, data, outcomes, mode)
        except KeyboardInterrupt:
            print("Interrupted. Saving current progress.", flush=True)
            stopped = True
    summary = export(root, data, outcomes, mode)
    archive = package(root)
    print(json.dumps({**summary, "output": str(root), "archive": str(archive)}, indent=2))
    if not data["videos"] or summary["tab_errors"] or stopped:
        return 2
    return 0 if args.inventory_only or summary["successful"] == summary["selected_count"] else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, ImportError, RuntimeError) as exc:
        print(f"Cannot continue: {exc}", file=sys.stderr)
        sys.exit(2)
