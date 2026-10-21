# Liam Ottley Advisor — unofficial starter

A local YouTube caption collector and a source-grounded advisor prompt. Collect your own accessible sources, keep their timestamps, and use an AI assistant to explore the evidence in the context of your own goals.

**This is a collector and advisor starter, not a trained Liam model or a finished chatbot.** It is not affiliated with or endorsed by Liam Ottley. It cannot establish his private opinions or guarantee what he would advise.

## What is included

- Channel inventory across videos, Shorts and livestream archives, deduplicated by video ID.
- English caption collection using local extraction or an optional Supadata account.
- TXT, timestamped JSON and SRT exports, a combined reading file, index/gap reports and a verified ZIP/checksum.
- Resume support that keeps successful items, stops on detected request blocks and can cap hosted requests to the reported starting credit balance.
- A portable advisor prompt, personal-context template and evaluation checklist.
- Offline tests using synthetic captions; no real API key is needed to run them.

Full third-party transcripts, collection ZIPs, private business records and credentials are not distributed in this repository. The MIT license covers this project's code and original documentation; it does not grant rights to third-party content you collect.

## Quick start

Requires Python 3.10+ and internet access for collection. Commands below are for macOS/Linux:

```sh
git clone https://github.com/arnavkpr798-a11y/liam-ottley-advisor.git
cd liam-ottley-advisor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python collector.py --pilot 3
```

On Windows, use `py -3 -m venv .venv`, activate with `.venv\Scripts\Activate.ps1`, then run the same pip/Python commands. The `.command` launcher is for macOS with zsh. Double-click it after installing Python 3.10+; it creates its own virtual environment. `CODEX_PYTHON` may optionally identify another compatible Python executable.

The default channel is `https://www.youtube.com/@LiamOttley`. For another channel:

```sh
python collector.py --channel https://www.youtube.com/@HANDLE --pilot 3
```

## Collect and resume

```sh
# List the public catalogue without fetching captions
python collector.py --inventory-only

# Collect available captions for the public snapshot
python collector.py --all --delay 20

# Resume a saved collection; substitute the printed run directory
python collector.py --resume output/run-YOUR-RUN --all --delay 20
```

Every new run prints its output folder and a sibling ZIP path. New runs do not replace earlier directories. Resume updates the selected run. Check `summary.json`, `index.csv` and `gaps.csv`: a partial ZIP is not a complete collection. `complete: true` means all items in the enumerated snapshot succeeded, not that every historical upload exists or is accessible.

Local extraction may be blocked by YouTube even after a successful pilot. The collector stops on detected blocking; avoid repeated immediate retries. Private/deleted/restricted uploads and ongoing streams may be unavailable. Missing dates remain unknown. Captions may contain errors; no human accuracy review is implied.

## Optional Supadata connection

Supadata is an external service with its own account, allowance and pricing. Use your own key and check its dashboard before a larger run. This project neither creates accounts nor purchases plans or credits.

```sh
# Hidden local key entry; no request is made by this command
python collector.py --configure-supadata

# Resume, attempting at most three new items through the hosted provider
python collector.py --resume output/run-YOUR-RUN --all --provider supadata --max-new 3 --delay 1

# Resume within the credit balance reported at launch
python collector.py --resume output/run-YOUR-RUN --all --provider supadata --use-available-credits --delay 1
```

The key is saved in `.supadata-api-key` with owner-only permissions on supported systems; `SUPADATA_API_KEY` is also supported. Never commit or share real keys. The collector ZIP includes only an explicit output-file list, excluding credentials.

Hosted requests use `mode=native`: AI transcription/translation fallback is disabled. A request may consume a credit even if captions are unavailable. With no explicit cap, this provider defaults to **three new attempts**. `--use-available-credits` deliberately increases that cap to the starting balance, or less if `--max-new` is also supplied. This is a snapshot cap, not a reservation across other tools sharing the account. Run one collector at a time.

If replacing a key in the macOS launcher, choose option 7. It prompts privately and resumes within the new key's reported allowance. API keys can share an account's allowance; a different key does not necessarily mean additional credits. Follow your provider's account and usage terms.

Provider references: [API](https://docs.supadata.ai/get-transcript), [account allowance](https://docs.supadata.ai/api-reference/endpoint/account/me), [current pricing](https://supadata.ai/pricing).

## Turn your sources into an advisor

1. Open this project with an AI assistant that can read local files.
2. Provide [the advisor prompt](prompts/advisor.md) and the path to your own collected run.
3. Copy [the personal-context template](examples/personal-context.example.md) to a private `personal-context.md` and add only information you want that assistant to use.
4. Start with a concrete decision. Require supporting passages, video/timecode links and a distinction between source advice and the AI's application.
5. Follow [the evaluation guide](docs/EVALUATION.md) before relying on the result. A source-derived decision playbook still needs to be extracted and checked; this repository does not supply an unverified personality profile.

Example question:

> Use prompts/advisor.md, my local collection at [PATH], and my private personal-context.md. What should I prioritise during the next seven days? Cite the supporting video timestamps and separate source evidence from your interpretation.

Do not upload the Supadata key, private context, or source archive to a public issue. This project does not implement an AI API client, server, voice clone, autonomous agent or persistent cross-chat memory.

## Development

```sh
python -m unittest -v test_collector.py test_supadata.py
python -m py_compile collector.py supadata_adapter.py
```

Tests use original synthetic content and mocked provider responses. The macOS menu test is skipped if zsh is unavailable. GitHub Actions runs the test suite without API credentials or real collection requests.

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) and [LICENSE](LICENSE).
