"""Prepare a company using the same service as the Streamlit workbench."""
import argparse
import json
from pathlib import Path


def main():
    from dotenv import load_dotenv
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--company', required=True, help='One SEC company name or ticker')
    parser.add_argument('--refresh', action='store_true', help='Check SEC for new filings')
    parser.add_argument('--download-only', action='store_true')
    parser.add_argument('--extract-latest', action='store_true', help='Prepare only latest annual narrative extraction for diagnostics')
    args = parser.parse_args()
    from .sec import FilingStore
    store = FilingStore()
    if args.download_only or args.extract_latest:
        manifest, reused = store.acquire(args.company, print, args.refresh)
        if args.extract_latest:
            from .extraction import EvidenceExtractor
            from pe_copilot.supply_chain.preparation import prepare_document
            filing = max((r for r in manifest['filings'] if r['form']=='10-K'),key=lambda r:r['reportDate'])
            directory = store.company_dir(manifest['company'])
            prepared = prepare_document(directory/filing['local_path'])
            output = directory/Path(filing['local_path']).parent/'extraction.json'
            result = EvidenceExtractor().extract(manifest['company'],filing,prepared,output,print)
            print(json.dumps({k:len(result[k]) for k in ('entities','claims','exposures','rejections')}))
        else:
            print(f"{manifest['company']['ticker']}: {len(manifest['filings'])} local filings; reused={reused}")
    else:
        from .service import CompanyPreparationService
        result = CompanyPreparationService(store=store).prepare(args.company, print, args.refresh)
        print(json.dumps({k:result[k] for k in ('company','status','filing_count','snapshot_count','reused','warnings')},indent=2))

if __name__ == '__main__':
    main()
