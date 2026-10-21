"""Optional hosted caption provider; configured and executed by the local user."""
import getpass
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

KEY_PATH = Path(__file__).resolve().parent / ".supadata-api-key"


class ProviderStopped(RuntimeError):
    stop_run = True


def read_key():
    key = os.environ.get("SUPADATA_API_KEY", "").strip()
    if not key and KEY_PATH.is_file():
        key = KEY_PATH.read_text(encoding="utf-8").strip()
    if not key:
        raise ValueError("Supadata key is missing. Configure it locally with --configure-supadata.")
    return key


def configure_key():
    key = getpass.getpass("Paste your Supadata API key (hidden; never paste it into chat): ").strip()
    if not key or any(ch.isspace() for ch in key):
        raise ValueError("No valid key entered; nothing saved")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(KEY_PATH, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(key + "\n")
    print("Saved the key locally with owner-only file permissions. No request was sent.")


class Client:
    def __init__(self, key=None, session=None):
        import requests
        self.key = key or read_key()
        self.session = session or requests.Session()

    def allowance(self):
        import math
        payload = self.request("me")
        maximum, used = payload.get("maxCredits"), payload.get("usedCredits")
        if any(isinstance(v, bool) or not isinstance(v, (int, float))
               or not math.isfinite(v) or v < 0 for v in (maximum, used)):
            raise ValueError("Could not verify the credit allowance; no collection started")
        return {"plan": payload.get("plan", "unknown"), "max_credits": maximum,
                "used_credits": used, "remaining": max(0, math.floor(maximum - used))}

    def request(self, endpoint, params=None):
        response = self.session.get("https://api.supadata.ai/v1/" + endpoint,
                                    params=params, headers={"x-api-key": self.key},
                                    timeout=(10, 45), allow_redirects=False)
        if response.status_code in {401, 402, 403, 429} or response.status_code >= 500:
            raise ProviderStopped(f"Supadata HTTP {response.status_code}. Check access, credits or service status before retrying.")
        if response.status_code == 206:
            raise ValueError("No existing caption track. AI generation is disabled; this request may still use one credit.")
        if response.status_code not in {200, 202}:
            raise ValueError(f"Supadata HTTP {response.status_code}; no automatic retry")
        try:
            payload = response.json()
        except ValueError:
            raise ProviderStopped("Supadata returned an invalid JSON response") from None
        if not isinstance(payload, dict):
            raise ProviderStopped("Supadata returned an unexpected response shape")
        return payload

    def fetch(self, video_id):
        payload = self.request("transcript", {"url": f"https://www.youtube.com/watch?v={video_id}",
                               "lang": "en", "text": "false", "mode": "native"})
        if "jobId" in payload:
            job_id = payload["jobId"]
            if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", job_id):
                raise ProviderStopped("Supadata returned an invalid job ID")
            for _ in range(30):
                time.sleep(2)
                status = self.request("transcript/" + job_id)
                if status.get("status") == "completed":
                    payload = status.get("result", status)
                    break
                if status.get("status") == "failed":
                    raise ValueError("Supadata caption job failed; check its dashboard")
            else:
                raise ProviderStopped(f"Caption job {job_id} is still pending; check its dashboard before another request.")
        if not isinstance(payload, dict) or str(payload.get("lang", "")).lower().split("-")[0] != "en":
            raise ValueError("English captions were not returned; no automatic translation attempted")
        chunks = payload.get("content")
        if not isinstance(chunks, list) or not chunks:
            raise ValueError("No timestamped captions returned")
        try:
            segments = [{"text": c["text"], "start": float(c["offset"]) / 1000,
                         "duration": float(c["duration"]) / 1000} for c in chunks]
        except (KeyError, TypeError, ValueError):
            raise ValueError("Invalid Supadata caption segment") from None
        import math
        if any(not isinstance(s["text"], str) or not all(math.isfinite(s[k]) and s[k] >= 0
               for k in ("start", "duration")) for s in segments):
            raise ValueError("Invalid Supadata caption text or timing")
        if not any(s["text"].strip() for s in segments):
            raise ValueError("Empty Supadata caption text")
        return {"language": payload["lang"], "method": "supadata_native_captions",
                "captured_at": datetime.now(timezone.utc).isoformat(), "segments": segments,
                "provider_response": payload}
