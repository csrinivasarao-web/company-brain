# Needletail Company Brain — Prototype

A governed, retrieval-grounded knowledge system for Needletail, piloted on the
GTM and Human-in-the-loop Operations teams. Full architecture and reasoning
live in the accompanying design document (`Needletail_Company_Brain_v2.docx`).

## Status

This repo is being built incrementally, one architecture layer at a time.
See the checklist on the app's live page for current progress.

## Running locally (optional — this app is meant to run on Streamlit Community Cloud)

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Secrets

Copy `.streamlit/secrets.toml.example` for the expected key names. The real
values go into Streamlit Community Cloud's Secrets manager (App settings →
Secrets) — never into a committed file.
