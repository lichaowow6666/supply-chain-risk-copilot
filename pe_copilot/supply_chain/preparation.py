"""Deterministic SEC HTML preparation. No LLM, embeddings, or network calls."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Tuple

from bs4 import BeautifulSoup

PARSER_VERSION = 'sec-visible-blocks-v1'
BLOCK_TAGS = ('p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table')


def normalized(value):
    return ' '.join(value.split())


@dataclass(frozen=True)
class Block:
    block_id: str
    kind: str
    section: str
    text: str
    rows: Tuple[Tuple[str, ...], ...] = ()
    anchor: str = ''


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    block_id: str
    section: str
    text: str
    start: int
    end: int
    row: int = -1


@dataclass(frozen=True)
class PreparedDocument:
    content_sha256: str
    parsing_version: str
    encoding: str
    metadata: dict
    blocks: Tuple[Block, ...]
    chunks: Tuple[Chunk, ...]

    def to_json(self):
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, indent=2)

    @classmethod
    def from_json(cls, payload):
        data = json.loads(payload)
        if data['parsing_version'] not in (PARSER_VERSION, 'inline-xbrl-facts-v1'):
            raise ValueError('unsupported parser version')
        data['blocks'] = tuple(Block(**{**b, 'rows': tuple(tuple(r) for r in b['rows'])}) for b in data['blocks'])
        data['chunks'] = tuple(Chunk(**c) for c in data['chunks'])
        return cls(**data)

    def block(self, block_id):
        for block in self.blocks:
            if block.block_id == block_id:
                return block
        raise KeyError(block_id)

    def resolve(self, locator):
        """Resolve exact normalized paragraph offsets or a complete table row."""
        parts = locator.split(':')
        block = self.block(parts[0])
        if len(parts) == 3 and parts[1] == 'row':
            row = int(parts[2])
            if row < 0 or row >= len(block.rows):
                raise ValueError('invalid row locator')
            return ' | '.join(block.rows[row]).strip()
        if len(parts) == 3:
            start, end = map(int, parts[1:])
            if not 0 <= start < end <= len(block.text):
                raise ValueError('invalid text locator')
            return block.text[start:end]
        raise ValueError('invalid evidence locator')

    def locate(self, phrase, section=None):
        matches = []
        for block in self.blocks:
            if section is not None and block.section != section:
                continue
            if phrase in block.text:
                start = block.text.index(phrase)
                matches.append(f'{block.block_id}:{start}:{start + len(phrase)}')
        if len(matches) != 1:
            raise ValueError(f'evidence selector expected one match, found {len(matches)}: {phrase[:80]}')
        return matches[0]


def prepare_document(path, max_chunk_chars=1600):
    if not isinstance(max_chunk_chars, int) or isinstance(max_chunk_chars, bool) or max_chunk_chars < 64:
        raise ValueError('max_chunk_chars must be an integer >= 64')
    raw = Path(path).read_bytes()
    content_hash = sha256(raw).hexdigest()
    # Complete SEC submission text may contain many exhibits. Select its primary annual or quarterly filing.
    document_bytes = raw
    if re.search(br'<DOCUMENT>', raw, re.I):
        documents = re.findall(br'<DOCUMENT>(.*?)</DOCUMENT>', raw, re.I | re.S)
        primary = [d for d in documents if re.search(br'<TYPE>\s*10-[KQ](?:/A)?\s*(?:\r?\n|<)', d, re.I)]
        if len(primary) != 1:
            raise ValueError('submission must have exactly one primary 10-K or 10-Q document')
        document_bytes = primary[0]
    soup = BeautifulSoup(document_bytes, 'html.parser')
    encoding = soup.original_encoding or 'unknown'
    metadata = {}
    fields = {'EntityCentralIndexKey', 'EntityRegistrantName', 'DocumentPeriodEndDate', 'DocumentType', 'TradingSymbol'}
    for tag in soup.find_all(attrs={'name': True}):
        key = tag['name'].split(':')[-1]
        if key in fields:
            metadata.setdefault(key, set()).add(normalized(tag.get_text(' ', strip=True)))
    metadata = {key: sorted(values) for key, values in metadata.items()}
    for tag in list(soup.find_all(['script', 'style', 'noscript', 'ix:header', 'ix:hidden'])):
        if tag.parent is not None:
            tag.decompose()
    for tag in list(soup.find_all(True)):
        if tag.attrs is None:
            continue
        style = tag.get('style', '').replace(' ', '').lower()
        if tag.has_attr('hidden') or 'display:none' in style or 'visibility:hidden' in style:
            tag.decompose()
    blocks = []
    section = 'cover'
    for tag in soup.find_all(BLOCK_TAGS):
        if tag.find_parent('table') is not None:
            continue
        if tag.name != 'table' and tag.find(BLOCK_TAGS) is not None:
            continue
        value = normalized(tag.get_text(' ', strip=True))
        if not value:
            continue
        heading = re.match(r'^Item\s+(\d+[A-C]?)\.\s+', value, re.I)
        if heading and len(value) < 240:
            section = f'Item {heading.group(1).upper()}'
        rows = ()
        if tag.name == 'table':
            rows = tuple(tuple(normalized(cell.get_text(' ', strip=True))
                               for cell in row.find_all(['td', 'th'], recursive=False))
                         for row in tag.find_all('tr') if row.find_parent('table') is tag)
            rows = tuple(row for row in rows if any(row))
        blocks.append(Block(f'b{len(blocks):05d}', 'table' if tag.name == 'table' else 'text', section, value, rows, tag.get('id', '')))
    if not blocks:
        raise ValueError('no visible HTML blocks found')
    chunks = []
    for block in blocks:
        # Tables stay as complete rows, even if a row exceeds the paragraph limit.
        # Context/header rows remain available through block_id.
        if block.kind == 'table':
            for row, cells in enumerate(block.rows):
                value = ' | '.join(cells).strip()
                chunks.append(Chunk(f'{content_hash}:{PARSER_VERSION}:{block.block_id}:r{row}', block.block_id, block.section, value, 0, len(value), row))
            continue
        start = 0
        while start < len(block.text):
            end = min(start + max_chunk_chars, len(block.text))
            if end < len(block.text):
                sentence = list(re.finditer(r'[.!?]\s+', block.text[start:end]))
                if sentence:
                    end = start + sentence[-1].end()
                else:
                    space = block.text.rfind(' ', start + 1, end)
                    if space > start:
                        end = space + 1
            chunks.append(Chunk(f'{content_hash}:{PARSER_VERSION}:{block.block_id}:{start}-{end}', block.block_id, block.section, block.text[start:end], start, end))
            start = end
    return PreparedDocument(content_hash, PARSER_VERSION, encoding, metadata, tuple(blocks), tuple(chunks))
