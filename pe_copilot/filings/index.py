"""Local document-scoped full-text index. Chunking/indexing needs no embedding model."""
import json
from pathlib import Path
import re
import sqlite3
from contextlib import contextmanager


class FilingIndex:
    def __init__(self, path='artifacts/filings_index.sqlite'):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, cik TEXT, ticker TEXT, accession TEXT, form TEXT, period_end TEXT, filed TEXT, source_url TEXT)')
            db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(document_id UNINDEXED, chunk_id UNINDEXED, locator UNINDEXED, section UNINDEXED, text)')

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def contains(self, company, filings):
        with self.connect() as db:
            for filing in filings:
                expected = company['cik']+':'+filing['accessionNumber']+':'+filing['sha256']
                if not db.execute('SELECT 1 FROM documents WHERE id=? AND cik=?',(expected,company['cik'])).fetchone():
                    return False
                if not db.execute('SELECT 1 FROM chunks WHERE document_id=? LIMIT 1',(expected,)).fetchone():
                    return False
        return True

    def put(self, company, filing, prepared):
        doc = company['cik']+':'+filing['accessionNumber']+':'+prepared.content_sha256
        with self.connect() as db:
            existing = [r[0] for r in db.execute('SELECT id FROM documents WHERE cik=? AND accession=?',(company['cik'],filing['accessionNumber']))]
            for previous in existing:
                db.execute('DELETE FROM chunks WHERE document_id=?',(previous,))
                db.execute('DELETE FROM documents WHERE id=?',(previous,))
            db.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)',(doc,company['cik'],company['ticker'],filing['accessionNumber'],filing['form'],filing['reportDate'],filing['filingDate'],filing['url']))
            db.executemany('INSERT INTO chunks VALUES (?,?,?,?,?)',[
                (doc,c.chunk_id, f'{c.block_id}:row:{c.row}' if c.row >= 0 else f'{c.block_id}:{c.start}:{c.end}',c.section,c.text)
                for c in prepared.chunks])
        return doc

    def has_report(self, ticker, accession):
        with self.connect() as db:
            return db.execute('''SELECT 1 FROM documents d JOIN chunks c ON d.id=c.document_id
                WHERE d.ticker=? AND d.accession=? LIMIT 1''',
                (ticker.strip().upper(), accession)).fetchone() is not None

    def report_chunks(self, ticker, accession):
        """Dense retrieval candidates are scoped in SQL, before vector ranking."""
        if not ticker or not accession:
            raise ValueError('Both ticker and report accession are required.')
        with self.connect() as db:
            rows = db.execute('''SELECT c.chunk_id,c.text,c.locator,c.section,d.form,d.period_end,
                d.filed,d.source_url,d.accession,d.id,d.cik,d.ticker FROM chunks c
                JOIN documents d ON d.id=c.document_id WHERE d.ticker=? AND d.accession=?
                ORDER BY d.id,c.chunk_id''', (ticker.strip().upper(), accession)).fetchall()
        return [dict(zip(('chunk_id','text','locator','section','document_type','period_end',
                         'filing_date','source_url','accession','document_id','cik','ticker'), r)) for r in rows]

    def query(self, query, ticker, accession, limit=8):
        if not isinstance(ticker, str) or not ticker.strip() or not isinstance(accession, str) or not accession.strip():
            raise ValueError('Both ticker and report accession are required.')
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError('limit must be a positive integer.')
        terms = re.findall(r'[A-Za-z0-9]+',query)
        if not terms:
            return []
        expression = ' OR '.join('"'+term+'"' for term in terms)
        with self.connect() as db:
            rows = db.execute('''SELECT c.chunk_id,c.text,c.locator,c.section,d.form,d.period_end,d.filed,d.source_url,d.accession,d.id,d.cik,d.ticker
                FROM chunks c JOIN documents d ON d.id=c.document_id
                WHERE chunks MATCH ? AND d.ticker=? AND d.accession=? ORDER BY bm25(chunks),d.id,c.chunk_id LIMIT ?''',
                (expression,ticker.strip().upper(),accession.strip(),limit)).fetchall()
        return [dict(zip(('chunk_id','text','locator','section','document_type','period_end','filing_date','source_url','accession','document_id','cik','ticker'),r)) for r in rows]
