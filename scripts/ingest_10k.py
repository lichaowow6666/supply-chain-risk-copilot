"""SEC company onboarding CLI (10-K and 10-Q, three years, resumable local storage).

Usage: venv/bin/python -m scripts.ingest_10k --company TICKER
Uses the same preparation, evidence extraction and indexing as the application.
"""
from pe_copilot.filings.__main__ import main

if __name__ == '__main__':
    main()
