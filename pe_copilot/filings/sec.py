"""SEC submissions/archive client with resumable, hash-checked local storage."""
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import quote, urlparse

import requests

FORMS = ('10-K', '10-Q', '10-K/A', '10-Q/A')
_LOCK = threading.Lock()
_LAST_REQUEST = 0.0


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.{os.getpid()}.{threading.get_ident()}.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False), encoding='utf-8')
    os.replace(temporary, path)


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def rows(columns):
    keys = ('accessionNumber', 'filingDate', 'reportDate', 'form', 'primaryDocument')
    return [dict(zip(keys, values)) for values in zip(*(columns.get(k, []) for k in keys))]


def select_filings(records, today=None, years=3):
    """Three completed annual reporting periods plus their quarters and newer quarters.

    Amendments remain independent documents. Only filings available by today count.
    """
    today = today or date.today()
    available = {r['accessionNumber']: r for r in records if r.get('form') in FORMS
                 and r.get('reportDate') and r.get('primaryDocument') and r.get('filingDate', '') <= today.isoformat()}
    annual = sorted({r['reportDate'] for r in available.values() if r['form'] == '10-K'}, reverse=True)[:years]
    if not annual:
        raise ValueError('No 10-K is available for this company; foreign/private issuers without 10-K/10-Q are not supported.')
    oldest = date.fromisoformat(annual[-1])
    start = oldest.replace(year=oldest.year-1, day=min(oldest.day, 28)) + timedelta(days=1) if oldest.month == 2 else oldest.replace(year=oldest.year-1) + timedelta(days=1)
    selected = [r for r in available.values() if
                (r['form'].startswith('10-K') and r['reportDate'] in annual) or
                (r['form'].startswith('10-Q') and start.isoformat() <= r['reportDate'] <= today.isoformat())]
    return sorted(selected, key=lambda r: (r['reportDate'], r['filingDate'], r['accessionNumber'])), start.isoformat(), len(annual)


class SECClient:
    def __init__(self, session=None, user_agent=None):
        self.session = session or requests.Session()
        proxy = os.getenv('PROXY')
        if proxy and session is None:
            self.session.proxies.update({'http': proxy, 'https': proxy})
        contact = os.getenv('SEC_CONTACT_EMAIL', '')
        local_config = Path('artifacts/sec_client.json')
        configured = json.loads(local_config.read_text()).get('user_agent') if local_config.exists() else None
        self.user_agent = user_agent or os.getenv('SEC_USER_AGENT') or configured or ('AgenticMarginCopilot/0.2 local-research ' + contact).strip()

    def get(self, url, as_json=True):
        global _LAST_REQUEST
        if urlparse(url).hostname not in ('www.sec.gov', 'data.sec.gov') or not url.startswith('https://'):
            raise ValueError('Only official HTTPS SEC endpoints are allowed')
        for attempt in range(3):
            with _LOCK:
                time.sleep(max(0, .15 - (time.monotonic() - _LAST_REQUEST)))
                _LAST_REQUEST = time.monotonic()
            try:
                response = self.session.get(url, headers={'User-Agent': self.user_agent, 'Accept-Encoding': 'gzip, deflate'}, timeout=(15, 60))
            except requests.RequestException as error:
                if attempt == 2:
                    raise RuntimeError('SEC connection failed. Local completed data is preserved; retry preparation when connectivity returns.') from error
                time.sleep(1 + attempt)
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                retry = response.headers.get('Retry-After', '')
                time.sleep(min(30, float(retry)) if retry.isdigit() else 2 ** (attempt+1))
                continue
            if not response.ok:
                raise RuntimeError(f'SEC request returned HTTP {response.status_code}. Preparation is incomplete; downloaded files are preserved.')
            return response.json() if as_json else response.content
        raise RuntimeError('SEC request did not complete')

    def resolve(self, query, root):
        if not isinstance(query, str):
            raise ValueError('Enter one listed company name, ticker or CIK (up to 160 characters).')
        query = query.strip()
        if not query or len(query) > 160:
            raise ValueError('Enter one listed company name, ticker or CIK (up to 160 characters).')
        # A CIK is an issuer identifier, not a partial company-name search. Accept
        # SEC's padded representation and human input such as "CIK: 123456".
        cik = None
        if query.isdecimal() or re.match(r'^CIK(?:\b|[0-9])', query, re.I):
            match = re.fullmatch(r'(?:CIK\s*:?\s*)?([0-9]{1,10})', query, re.I)
            if not match or int(match[1]) == 0:
                raise ValueError('Enter a valid CIK: 1–10 digits, optionally prefixed with CIK.')
            cik = match[1].zfill(10)
        root = Path(root)
        # Reuse known identities without a network request. Validate the manifest CIK.
        for path in root.glob('*/manifest.json'):
            item = json.loads(path.read_text())
            company = item['company']
            if (cik == company['cik'] if cik else
                    query.casefold() in (company['ticker'].casefold(), company['name'].casefold())):
                if re.fullmatch(r'\d{10}', company['cik']):
                    return company
        path = root/'company_tickers.json'
        fresh = path.exists() and time.time()-path.stat().st_mtime < 86400
        catalog = json.loads(path.read_text()) if fresh else self.get('https://www.sec.gov/files/company_tickers.json')
        if not fresh:
            atomic_json(path, catalog)
        items = list(catalog.values())
        norm = lambda value: re.sub(r'[^a-z0-9]', '', value.casefold())
        if cik:
            matches = [r for r in items if str(r['cik_str']).zfill(10) == cik]
        else:
            matches = [r for r in items if r['ticker'].casefold() == query.casefold()]
            if not matches:
                matches = [r for r in items if norm(r['title']) == norm(query)]
            if not matches:
                matches = [r for r in items if norm(query) in norm(r['title'])]
        if len(matches) != 1:
            suggestions = ', '.join(f"{r['ticker']} ({r['title']})" for r in matches[:8])
            if matches:
                label = 'CIK matches multiple listed tickers' if cik else 'Company name is ambiguous'
                raise ValueError(label + '; enter its ticker. Matches: ' + suggestions)
            raise ValueError('No SEC-listed company matched this CIK.' if cik else 'No SEC-listed company matched this name or ticker.')
        item = matches[0]
        return {'cik': str(item['cik_str']).zfill(10), 'ticker': item['ticker'].upper(), 'name': item['title']}

    def submissions(self, company):
        data = self.get(f"https://data.sec.gov/submissions/CIK{company['cik']}.json")
        if str(data['cik']).zfill(10) != company['cik']:
            raise ValueError('SEC company identity mismatch')
        records = rows(data['filings']['recent'])
        # The recent response can be only a year for frequent filers. Follow history files.
        oldest_needed = f'{date.today().year-4}-01-01'
        for archive in data['filings'].get('files', []):
            if archive.get('filingTo', '') >= oldest_needed:
                name = archive['name']
                if not re.fullmatch(r'CIK\d+-submissions-\d+\.json', name):
                    raise ValueError('Unexpected SEC submissions archive name')
                records.extend(rows(self.get('https://data.sec.gov/submissions/' + name)))
        return records


class FilingStore:
    def __init__(self, root='sec_filings/companies', client=None):
        self.root = Path(root)
        self.client = client or SECClient()

    def company_dir(self, company):
        cik = company['cik']
        if not re.fullmatch(r'\d{10}', cik):
            raise ValueError('Invalid CIK')
        return self.root/cik

    def load(self, ticker):
        for path in self.root.glob('*/manifest.json'):
            item = json.loads(path.read_text())
            if item['company']['ticker'].upper() == ticker.upper():
                return item
        return None

    def verified(self, manifest):
        directory = self.company_dir(manifest['company'])
        return bool(manifest.get('filings')) and all(
            (directory/r['local_path']).is_file() and digest(directory/r['local_path']) == r.get('sha256')
            for r in manifest['filings']) and (directory/'companyfacts.json').is_file() and digest(directory/'companyfacts.json') == manifest.get('companyfacts_sha256')

    def acquire(self, query, progress=lambda message: None, refresh=False):
        company = self.client.resolve(query, self.root)
        directory = self.company_dir(company)
        path = directory/'manifest.json'
        old = json.loads(path.read_text()) if path.exists() else None
        if old and not refresh and old.get('checked_on') == date.today().isoformat() and old.get('download_complete') and self.verified(old):
            progress(f"{company['ticker']}: verified local filings already exist; reusing the repository.")
            return old, True
        progress(f"Resolving three years of 10-K and 10-Q filings for {company['ticker']}…")
        records, window_start, annual_count = select_filings(self.client.submissions(company))
        previous = {r['accessionNumber']: r for r in (old or {}).get('filings', [])}
        manifest = {'version': 1, 'company': company, 'window_start': window_start, 'annual_period_count': annual_count,
                    'checked_on': date.today().isoformat(), 'download_complete': False, 'filings': [], 'warnings': []}
        if annual_count < 3:
            manifest['warnings'].append(f'Only {annual_count} annual reporting periods are available from SEC; all available 10-K/10-Q filings in the requested window were collected.')
        for i, record in enumerate(records):
            accession = record['accessionNumber']
            if not re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession):
                raise ValueError('Invalid SEC accession')
            primary = record['primaryDocument']
            if '/' in primary or '\\' in primary or primary in ('.', '..'):
                raise ValueError('Invalid primary document path')
            form_folder = record['form'].replace('/', '-')
            relative = f'{form_folder}/{accession}/{primary}'
            destination = directory/relative
            url = f"https://www.sec.gov/Archives/edgar/data/{int(company['cik'])}/{accession.replace('-', '')}/{quote(primary)}"
            progress(f"{company['ticker']}: filing {i+1}/{len(records)} · {record['form']} · {record['reportDate']}")
            prior = previous.get(accession, {})
            if not destination.exists() or digest(destination) != prior.get('sha256'):
                content = self.client.get(url, as_json=False)
                if len(content) < 1000 or b'<html' not in content[:5000].lower():
                    raise ValueError('SEC returned an unexpected filing document; it was not accepted')
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_suffix(destination.suffix + '.tmp')
                temporary.write_bytes(content)
                os.replace(temporary, destination)
            manifest['filings'].append({**record, 'url': url, 'local_path': relative, 'sha256': digest(destination)})
            # Preserve outstanding entries for resuming; only a full pass may set complete.
            saved = {**manifest, 'filings': manifest['filings'] + [r for r in previous.values() if r['accessionNumber'] not in {x['accessionNumber'] for x in manifest['filings']} ]}
            atomic_json(path, saved)
        progress(f"{company['ticker']}: collecting SEC XBRL company facts…")
        facts = self.client.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{company['cik']}.json")
        if str(facts['cik']).zfill(10) != company['cik']:
            raise ValueError('SEC financial-fact company mismatch')
        atomic_json(directory/'companyfacts.json', facts)
        manifest['companyfacts_sha256'] = digest(directory/'companyfacts.json')
        manifest['download_complete'] = True
        atomic_json(path, manifest)
        return manifest, False
