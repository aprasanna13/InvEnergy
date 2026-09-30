# Changelog

## [0.1.0] - 2026-09-30
- Initialized Gemini-First Hierarchical Contract Parsing & Context Preservation Platform (Session 1) in GCP project `pr-tftest` per `Design/DESIGN_DOC.md`.
- Provisioned Cloud Storage bucket `gs://pr-tftest-contract-intelligence` and BigQuery dataset `pr-tftest:contract_intelligence`.
- Added 10-file thin-glue implementation (`pyproject.toml`, `Changelog.md`, `contract_parser/__init__.py`, `contract_parser/config.py`, `contract_parser/schemas.py`, `contract_parser/gemini_parser.py`, `contract_parser/storage.py`, `contract_parser/app.py`, `contract_parser/static/index.html`, `tests/test_pipeline.py`).
- Executed live ingestion of `Synthetic_Accommodation_Agreement.pdf` (`doc_70fc075850990305`) into `gs://pr-tftest-contract-intelligence` and BigQuery `pr-tftest.contract_intelligence`, and hardened broken cross-reference flagging, authoritative BigQuery reads, lazy SQLite initialization, and suffix byte-range PDF streaming.
