#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"
TASK_PYTHON=""
for candidate in .venv/bin/python python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' 2>/dev/null; then
    TASK_PYTHON="$(command -v "$candidate")"
    break
  fi
done
TASK_BUNDLED_PYTHON="${CODEX_PYTHON:-}"
if [[ -z "$TASK_PYTHON" && -x "$TASK_BUNDLED_PYTHON" ]]; then
  TASK_PYTHON="$TASK_BUNDLED_PYTHON"
fi
if [[ -z "$TASK_PYTHON" ]]; then
  print 'Python 3.10 or newer is required. Install it from python.org, then reopen this file.'
  read '?Press Return to close. '
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  "$TASK_PYTHON" -m venv .venv
fi
if ! .venv/bin/python -c 'import youtube_transcript_api, yt_dlp' 2>/dev/null; then
  .venv/bin/python -m pip install -r requirements.txt
fi
if (( $# > 0 )); then
  exec .venv/bin/python collector.py "$@"
fi
print 'Liam Ottley local caption collector'
print '1 — Test three videos (default)'
print '2 — List channel only'
print '3 — Collect captions for the entire public inventory'
print '4 — Resume the latest collection slowly (keeps successful files)'
print '5 — Test Supadata on 3 remaining videos (requires your API key)'
print '6 — Resume through Supadata using available credits'
print '7 — Replace the Supadata API key, then resume using available credits'
read 'TASK_CHOICE?Choose 1, 2, 3, 4, 5, 6 or 7: '
case "$TASK_CHOICE" in
  2) TASK_ARGS=(--inventory-only) ;;
  3) TASK_ARGS=(--all --delay 20) ;;
  4|5|6|7)
    TASK_RESUME="$(.venv/bin/python - <<'PY'
import json
from pathlib import Path
for folder in sorted(Path('output').glob('run-*'), reverse=True):
    try:
        progress = json.loads((folder / 'progress.json').read_text(encoding='utf-8'))
        inventory = json.loads((folder / 'inventory.json').read_text(encoding='utf-8'))
        if progress.get('mode') == 'collect' and inventory.get('channel_url') == 'https://www.youtube.com/@LiamOttley':
            print(folder.resolve())
            break
    except (OSError, ValueError):
        continue
PY
)"
    if [[ -z "$TASK_RESUME" ]]; then
      print 'No saved Liam Ottley collection was found in this folder.'
      read '?Press Return to close. '
      exit 1
    fi
    print "Resuming: $TASK_RESUME"
    if [[ "$TASK_CHOICE" = 5 || "$TASK_CHOICE" = 6 || "$TASK_CHOICE" = 7 ]]; then
      if [[ "$TASK_CHOICE" = 7 ]]; then
        unset SUPADATA_API_KEY
        .venv/bin/python collector.py --configure-supadata
      elif ! .venv/bin/python -c 'from supadata_adapter import read_key; import sys; exec("try:\n read_key()\nexcept (ValueError, OSError):\n sys.exit(1)")' 2>/dev/null; then
        print 'Create a free account at https://dash.supadata.ai and copy its API key.'
        .venv/bin/python collector.py --configure-supadata
      fi
      if [[ "$TASK_CHOICE" = 6 || "$TASK_CHOICE" = 7 ]]; then
        print 'Checking your current credit balance, then resuming within that starting allowance.'
        print 'Existing files are kept. API credits are used; AI generation is disabled.'
        TASK_ARGS=(--resume "$TASK_RESUME" --all --provider supadata --use-available-credits --delay 1)
      else
        print 'Testing at most 3 remaining videos through Supadata. Uses API credits; AI generation is disabled.'
        TASK_ARGS=(--resume "$TASK_RESUME" --all --provider supadata --max-new 3 --delay 1)
      fi
    else
      print 'Successful files are retained. There is a 20-second pause between remaining videos.'
      TASK_ARGS=(--resume "$TASK_RESUME" --all --delay 20)
    fi
    ;;
  *) TASK_ARGS=(--pilot 3) ;;
esac
TASK_STATUS=0
.venv/bin/python collector.py "${TASK_ARGS[@]}" || TASK_STATUS=$?
if [[ "$TASK_STATUS" -ne 0 ]]; then
  print 'The run has gaps or an access block. Review the output reports before trying again.'
  print 'If local extraction is blocked repeatedly, stop local retries. Option 5 tests the hosted provider.'
  print 'Starting option 3 again creates a new collection.'
fi
read '?Press Return to close. '
exit "$TASK_STATUS"
