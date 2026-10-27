"""Conservative Persian normalization and data-only source readers."""
from __future__ import annotations

import csv
import gzip
import io
import json
import pickle
import re
import shutil
import sqlite3
import tarfile
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ZWNJ = '\u200c'
LETTERS = frozenset('آاأإٱبپتثجچحخدذرزژسشصضطظعغفقکگلمنوهیءؤئة')
TRANSLATE = str.maketrans({'ي': 'ی', 'ى': 'ی', 'ك': 'ک', 'ۀ': 'هٔ',
                          'ـ': '', '\ufeff': '', '\u200d': '', '\u200e': '',
                          '\u200f': '', '\u061c': '', '\u202a': '', '\u202b': '',
                          '\u202c': '', '\u202d': '', '\u202e': '', '\u2066': '',
                          '\u2067': '', '\u2068': '', '\u2069': ''})
VOWELS = re.compile('[\u064b-\u0652\u0670]')
WHITESPACE = re.compile(r'\s+')
JOINER_SPACE = re.compile(r' *\u200c *')
JOINERS = re.compile('\u200c+')


def normalize(raw: str) -> tuple[str | None, str | None]:
    """Return a search spelling and rejection reason; never guess missing letters."""
    text = unicodedata.normalize('NFKC', raw).translate(TRANSLATE)
    text = unicodedata.normalize('NFC', VOWELS.sub('', text))
    text = WHITESPACE.sub(' ', text).strip()
    text = JOINER_SPACE.sub(ZWNJ, text)
    text = JOINERS.sub(ZWNJ, text).strip(ZWNJ)
    if not text:
        return None, 'empty'
    if len(text) > 120 or text.count(' ') > 9:
        return None, 'too_long_or_sentence'
    if any(c.isdigit() for c in text):
        return None, 'contains_digits'
    if any(c not in LETTERS and c not in (ZWNJ, ' ', '\u0654', '\u0655') for c in text):
        return None, 'non_persian_character_or_punctuation'
    if not any(c in LETTERS for c in text):
        return None, 'no_letters'
    for i, c in enumerate(text):
        if c in ('\u0654', '\u0655') and (i == 0 or text[i - 1] not in LETTERS):
            return None, 'unattached_combining_mark'
    return text, None


def spacing_key(word: str) -> str:
    return word.replace(ZWNJ, '').replace(' ', '')


def decode_prezip(data: bytes):
    """Decode Aspell CWL v2; byte-prefix lengths precede escaped UTF-8 suffixes.

    Format reference: https://github.com/GNUAspell/aspell/blob/master/prog/prezip.c
    """
    if not data.startswith(b'\x02'):
        raise ValueError('Unsupported CWL version')
    previous = b''
    offset = 1
    while offset < len(data):
        prefix = data[offset]
        offset += 1
        if prefix > 30:
            raise ValueError('Invalid CWL prefix')
        if prefix == 30:
            while True:
                if offset >= len(data):
                    raise ValueError('Truncated CWL prefix')
                extension = data[offset]
                offset += 1
                prefix += extension
                if extension != 255:
                    break
        if prefix > len(previous):
            raise ValueError('CWL prefix exceeds previous word')
        start = offset
        while offset < len(data) and data[offset] > 30:
            offset += 1
        encoded = previous[:prefix] + data[start:offset]
        terminal = encoded.endswith(b'\x1f\xff')
        if terminal:
            encoded = encoded[:-2]
            if offset != len(data):
                raise ValueError('Trailing CWL data')
        decoded = bytearray()
        i = 0
        while i < len(encoded):
            byte = encoded[i]
            i += 1
            if byte == 31:
                if i >= len(encoded) or not 32 <= encoded[i] < 64:
                    raise ValueError('Invalid CWL escape')
                byte = encoded[i] - 32
                i += 1
            decoded.append(byte)
        yield decoded.decode('utf-8')
        previous = encoded
        if terminal:
            return
    raise ValueError('Missing CWL terminator')


@dataclass(frozen=True)
class Observation:
    word: str
    count: float = 0
    pos: str = ''
    origin: str = 'lexical'
    location: str = ''


def lines(path: Path):
    with path.open(encoding='utf-8-sig') as stream:
        for number, line in enumerate(stream, 1):
            value = line.strip()
            if value and not value.startswith('#'):
                yield number, value


def read_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    connection.execute('PRAGMA trusted_schema=OFF')
    connection.execute('PRAGMA query_only=ON')
    return connection


def extract_member(archive: zipfile.ZipFile, name: str, target: Path, limit=300_000_000) -> Path:
    info = archive.getinfo(name)
    if info.file_size > limit:
        raise ValueError(f'Archive member exceeds size limit: {name}')
    target.parent.mkdir(parents=True, exist_ok=True)
    # The output name is ours, never an untrusted archive path.
    with archive.open(info) as source, target.open('wb') as destination:
        shutil.copyfileobj(source, destination)
    return target


class RestrictedUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) == ('collections', 'Counter'):
            return Counter
        raise pickle.UnpicklingError(f'Forbidden global: {module}.{name}')

    def persistent_load(self, pid):
        raise pickle.UnpicklingError('Persistent references are forbidden')


def load_counter(stream) -> Counter:
    counter = RestrictedUnpickler(stream).load()
    # Parsivar stores [Counter, corpus_token_total].
    if isinstance(counter, list) and len(counter) == 2 and type(counter[1]) is int:
        counter = counter[0]
    if not isinstance(counter, (dict, Counter)):
        raise ValueError('Expected a unigram count mapping')
    if any(not isinstance(k, str) or type(v) is not int or v < 0 for k, v in counter.items()):
        raise ValueError('Invalid unigram key or count')
    return counter


def xml_text(node: ET.Element | None) -> str:
    if node is None:
        return ''
    result = node.text or ''
    for child in node:
        if child.tag == 'b':
            result += ' '
        elif child.tag in ('g', 'j', 'a'):
            result += xml_text(child)
        elif child.tag != 's':
            raise ValueError(f'Unsupported dictionary text element: {child.tag}')
        result += child.tail or ''
    return result


def apertium_records(path: Path, expand=True):
    raw = path.read_bytes()
    if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:
        raise ValueError('External XML definitions are forbidden')
    root = ET.fromstring(raw)
    paradigms = {p.attrib['n']: p for p in root.findall('pardefs/pardef')}

    def entry_surfaces(entry, stack=()):
        if entry.find('re') is not None:
            return ()  # Regex classes describe punctuation/open sets, not words.
        forms = ['']
        for child in entry:
            if child.tag == 'p':
                additions = [xml_text(child.find('l'))]
            elif child.tag == 'i':
                additions = [xml_text(child)]
            elif child.tag == 'par':
                additions = suffixes(child.attrib['n'], stack)
            else:
                raise ValueError(f'Unsupported Apertium element: {child.tag}')
            forms = [prefix + suffix for prefix in forms for suffix in additions]
            if len(forms) > 20000:
                raise ValueError('Apertium expansion limit exceeded')
        return forms

    @lru_cache(maxsize=None)
    def suffixes(name, stack=()):
        if name in stack:
            raise ValueError('Recursive Apertium paradigm')
        return tuple(form for entry in paradigms[name].findall('e')
                     for form in entry_surfaces(entry, stack + (name,)))

    for number, entry in enumerate(root.findall("section[@id='main']/e"), 1):
        location = f'{path.name}:entry:{number}'
        lemma = entry.get('lm')
        if lemma:
            yield Observation(lemma, location=location)
        if expand:
            for form in entry_surfaces(entry):
                if form != lemma:
                    yield Observation(form, origin='generated', location=location)


def hunspell_records(folder: Path, source: dict):
    dictionary = next(folder / f['path'] for f in source['files'] if f['path'].endswith('.dic'))
    affixes = next(folder / f['path'] for f in source['files'] if f['path'].endswith('.aff'))
    # These pinned Lilak builds use long flags, zero stripping, '.' conditions,
    # and non-crossing affixes. Fail on other grammars instead of approximating.
    rules = {}
    standalone_exclusions = set()
    saw_long = False
    for _, line in lines(affixes):
        fields = line.split()
        if fields[:2] == ['FLAG', 'long']:
            saw_long = True
        if fields[0] in ('NEEDAFFIX', 'ONLYINCOMPOUND', 'FORBIDDENWORD'):
            standalone_exclusions.add(fields[1])
        if fields[0] not in ('PFX', 'SFX'):
            continue
        if len(fields) == 4:
            if fields[2] != 'N':
                raise ValueError('Crossing Hunspell affixes need a different adapter')
            continue
        if len(fields) < 5 or fields[2] != '0' or fields[4] != '.':
            raise ValueError('Unsupported Hunspell strip or condition')
        addition, _, continuation = fields[3].partition('/')
        rules.setdefault(fields[1], []).append((fields[0], '' if addition == '0' else addition, continuation))
    if not saw_long:
        raise ValueError('Expected long Hunspell flags')

    def expand(word, flags, depth=0):
        if depth > 4:
            raise ValueError('Unexpected recursive Hunspell continuation')
        if len(flags) % 2:
            raise ValueError('Odd length Hunspell flag sequence')
        for flag in {flags[i:i+2] for i in range(0, len(flags), 2)}:
            for kind, addition, following in rules.get(flag, []):
                result = addition + word if kind == 'PFX' else word + addition
                if not {following[i:i+2] for i in range(0, len(following), 2)} & standalone_exclusions:
                    yield result
                yield from expand(result, following, depth + 1)

    for number, line in lines(dictionary):
        if number == 1 and line.isdigit():
            continue
        word, _, flags = line.split('\t')[0].partition('/')
        flag_set = {flags[i:i+2] for i in range(0, len(flags), 2)}
        if not flag_set & standalone_exclusions:
            yield Observation(word, location=f'{dictionary.name}:{number}')
        if source.get('expand'):
            for form in sorted(set(expand(word, flags))):
                yield Observation(form, origin='generated', location=f'{dictionary.name}:{number}:affix')


def records(source: dict):
    folder = ROOT / 'data/raw' / source['id']
    parser = source['parser']
    if parser == 'zaya_g2p':
        # This derivative filters Zaya for grapheme-to-phoneme training.
        # Pronunciation alternatives do not count as extra word evidence.
        data = json.loads((folder / 'resources/FA/Lexicon.json').read_text())
        if not isinstance(data, list):
            raise ValueError('Expected Zaya derivative word/pronunciation rows')
        for number, row in enumerate(data, 1):
            if not isinstance(row, list) or len(row) != 2 or not all(isinstance(v, str) for v in row):
                raise ValueError('Invalid Zaya word/pronunciation row')
            yield Observation(row[0], location=f'Lexicon.json:{number}')
        return
    if parser == 'hunspell':
        yield from hunspell_records(folder, source)
        return
    if parser == 'lilak':
        for number, line in lines(folder / 'src/data/lexicon'):
            columns = next(csv.reader([line]))
            yield Observation(columns[0], pos=columns[1], location=f'lexicon:{number}')
        for number, word in lines(folder / 'src/data/dic_users'):
            yield Observation(word, location=f'dic_users:{number}')
        return
    if parser in ('hazm', 'virastyar'):
        name = 'hazm/data/words.dat' if parser == 'hazm' else 'persian.dic'
        for number, line in lines(folder / name):
            columns = line.split('\t')
            if parser == 'virastyar':
                columns = [value for value in columns if value != '']
            yield Observation(columns[0], float(columns[1]), columns[2].strip() if len(columns) > 2 else '',
                              location=f'{name}:{number}')
        if parser == 'hazm':
            # Informal mappings are evidence of usage, not automatic replacement instructions.
            for number, line in lines(folder / 'hazm/data/iwords.dat'):
                columns = line.split(maxsplit=1)
                yield Observation(columns[0], origin='colloquial', location=f'iwords.dat:{number}')
                if len(columns) == 2:
                    yield Observation(columns[1], location=f'iwords.dat:{number}')
            for number, line in lines(folder / 'hazm/data/verbs.dat'):
                past, _, present = line.partition('#')
                if past:
                    yield Observation(past, pos='V:past-stem', location=f'verbs.dat:{number}')
                    yield Observation(past + 'ن', pos='V:infinitive', origin='generated', location=f'verbs.dat:{number}')
        return
    if parser == 'mnk':
        with zipfile.ZipFile(folder / 'mnk_persian_words/data/words.db.zip') as archive:
            path = extract_member(archive, 'words.db', ROOT / 'data/derived/mnk.db')
        with read_database(path) as connection:
            # Raw forms preserve variants that the upstream canonical table collapses.
            for ident, word in connection.execute('SELECT id, word FROM words ORDER BY id'):
                yield Observation(word, origin='collection', location=f'words:{ident}')
        return
    if parser == 'farsnet':
        with read_database(folder / 'farsnet/farsnet2.5.db3') as connection:
            for ident, value, pos in connection.execute('SELECT id, defaultValue, pos FROM word ORDER BY id'):
                yield Observation(value, pos=pos, location=f'word:{ident}')
            for ident, value in connection.execute('SELECT id, value FROM value ORDER BY id'):
                yield Observation(value, location=f'value:{ident}')
        return
    if parser == 'parsivar':
        with zipfile.ZipFile(folder / 'spell.zip') as archive:
            if archive.getinfo('onegram.pckl').file_size > 100_000_000:
                raise ValueError('Unigram pickle exceeds size limit')
            with archive.open('onegram.pckl') as stream:
                counter = load_counter(stream)
        for word, count in counter.items():
            yield Observation(word, count, origin='frequency', location='onegram.pckl')
        return
    if parser == 'aspell':
        with tarfile.open(folder / 'aspell-fa.tar.bz2') as archive:
            for member in archive.getmembers():
                if not member.isfile() or not member.name.endswith('.cwl'):
                    continue
                if member.size > 50_000_000:
                    raise ValueError('Aspell member exceeds size limit')
                with archive.extractfile(member) as stream:
                    data = stream.read()
                # These are expanded spelling lists, not a lemma inventory.
                for number, word in enumerate(decode_prezip(data), 1):
                    yield Observation(word, location=f'{Path(member.name).name}:{number}')
        return
    if parser == 'apertium':
        yield from apertium_records(folder / 'apertium-pes.pes.dix', source.get('expand', False))
        return
    if parser == 'wordfreq':
        import msgpack
        with gzip.open(folder / 'wordfreq/data/small_fa.msgpack.gz', 'rb') as stream:
            buckets = msgpack.unpack(stream, raw=False)
        if buckets[0] != {'format': 'cB', 'version': 1}:
            raise ValueError('Unknown wordfreq format')
        for index, words in enumerate(buckets[1:]):
            for word in words:
                yield Observation(word, 10 ** (9 - index / 100), origin='frequency',
                                  location=f'small_fa.msgpack.gz:bucket:{index}')
        return
    if parser == 'kaikki':
        excluded = {'character', 'punct', 'symbol', 'prefix', 'suffix', 'infix', 'interfix', 'proverb'}
        bad_tags = {'error', 'misspelling', 'nonstandard', 'romanization'}
        for number, line in lines(folder / 'persian.jsonl'):
            entry = json.loads(line)
            if entry.get('lang_code') != 'fa' or entry.get('pos') in excluded:
                continue
            tags = set(entry.get('tags', []))
            senses = entry.get('senses', [])
            # A mixed-sense headword remains usable if at least one sense is not marked erroneous.
            if tags & bad_tags or (senses and all(set(s.get('tags', [])) & bad_tags for s in senses)):
                continue
            origin = 'name' if entry.get('pos') == 'name' else 'lexical'
            yield Observation(entry['word'], pos=entry.get('pos', ''), origin=origin, location=f'jsonl:{number}')
            for form in entry.get('forms', []):
                if not set(form.get('tags', [])) & (bad_tags | {'romanization'}):
                    yield Observation(form['form'], pos=entry.get('pos', ''),
                                      origin='name' if origin == 'name' else 'generated',
                                      location=f'jsonl:{number}:form')
        return
    if parser == 'udlexicons':
        with zipfile.ZipFile(folder / 'udlexicons.zip') as archive:
            names = [n for n in archive.namelist() if ('persian' in n.lower() or 'perlex' in n.lower())
                     and n.endswith(('.conllul', '.conllu', '.lex'))]
            if not names:
                raise ValueError('No Persian lexicon found in UDLexicons archive')
            for name in names:
                with archive.open(name) as stream:
                    for number, line in enumerate(io.TextIOWrapper(stream, encoding='utf-8'), 1):
                        if not line.strip() or line.startswith('#'):
                            continue
                        columns = line.rstrip('\n').split('\t')
                        if len(columns) == 10 and '-' in columns[0]:
                            yield Observation(columns[1], origin='generated', location=f'{name}:{number}:multiword')
                            continue
                        if len(columns) == 7:
                            # Components of an analyzed token can be bound clitics.
                            # Keep its complete surface record above, not fragments.
                            continue
                        if len(columns) != 8:
                            raise ValueError('Unexpected CoNLL-UL columns')
                        if columns[4] in ('PUNCT', 'SYM'):
                            continue
                        origin = 'name' if columns[4] == 'PROPN' else 'generated'
                        yield Observation(columns[2], pos=columns[4], origin=origin, location=f'{name}:{number}')
                        if columns[3] != '_':
                            yield Observation(columns[3], pos=columns[4],
                                              origin='name' if origin == 'name' else 'lexical',
                                              location=f'{name}:{number}:lemma')
        return
    if parser == 'multext':
        with gzip.open(folder / 'wfl-fa.txt.gz', 'rt', encoding='utf-8-sig') as stream:
            for number, line in enumerate(stream, 1):
                if line.strip() and not line.startswith('#'):
                    columns = line.split('\t')
                    yield Observation(columns[0], pos=columns[-1].strip(), location=f'wfl-fa:{number}')
        return
    if parser == 'sql':
        pattern = re.compile(r"^INSERT INTO `word_list` VALUES \('((?:[^'\\]|\\.|'')*)'\);$")
        for number, line in lines(folder / 'words.sql'):
            if not line.startswith('INSERT '):
                continue
            match = pattern.fullmatch(line)
            if not match:
                raise ValueError(f'Unexpected SQL data row at {number}; SQL is never executed')
            word = re.sub(r'\\(.)', r'\1', match[1].replace("''", "'"))
            yield Observation(word, origin='collection', location=f'words.sql:{number}')
        return
    if parser == 'regex_resource':
        return
    if parser == 'perlex':
        for item in source['files']:
            path = folder / item['path']
            # AR is Arabic quotations; DELM is punctuation; MS contains foreign abbreviations.
            if path.stem in ('AR', 'DELM', 'MS', 'MORP'):
                continue
            for number, line in lines(path):
                columns = line.split('\t')
                word = columns[0].split('___')[0]
                origin = 'name' if path.stem in ('cities', 'countries', 'person_names') else 'lexical'
                yield Observation(word, pos=path.stem, origin=origin, location=f'{path.name}:{number}')
        return
    for item in source['files']:
        path = folder / item['path']
        if path.name.lower().startswith(('license', 'readme', 'copying')):
            continue
        if path.name == 'PersianWordsWithA.txt':
            for number, line in lines(path):
                match = re.fullmatch(r'[A-Za-z]+\s*=\s*\(([^()]*)\)', line)
                if match:
                    for word in match[1].split('|'):
                        yield Observation(word, location=f'{path.name}:{number}')
            continue
        for number, line in lines(path):
            location = f'{path.name}:{number}'
            if parser in ('frequency', 'behnam'):
                word, count = line.rsplit(None, 1)
                yield Observation(word, float(count), origin='frequency', location=location)
            elif parser == 'reza_corpus' and '\t' in line:
                columns = line.split('\t')
                yield Observation(columns[0], float(columns[1]), origin='frequency', location=location)
            else:
                origin = ('generated' if path.name in ('Word_collection_1', 'Persian Verbs List')
                          else source['role'])
                yield Observation(line, origin=origin, location=location)
