# Contributing

Open an issue describing a reproducible problem or proposed change. Use synthetic caption fixtures and redact private paths and account information.

Before proposing a change:

```sh
python -m unittest -v test_collector.py test_supadata.py
python -m py_compile collector.py supadata_adapter.py
```

Keep source attribution, gap reporting, credit caps, credential exclusion and resume behaviour intact. Do not add automatic paid transcription fallback or account/plan changes. Use your own permitted sources for manual collection tests; CI must not need an API key or fetch real video transcripts.

Contributions are provided under the project's MIT license. Do not contribute third-party transcript corpora, secrets, private context or generated archives.
