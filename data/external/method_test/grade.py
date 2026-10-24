import json, glob, math, os, collections
D = os.path.dirname(os.path.abspath(__file__))
key = {k['pid']: k for k in json.load(open(f'{D}/key/key.json'))}
pairs = {p['pid']: p for f in ('half0', 'half1') for p in json.load(open(f'{D}/pairs/{f}.json'))}
import re
PLACEHOLDER = re.compile(r'\[(trailer|teaser)\]|\bintro v\d|^(untitled|test|tbd|draft)\b|^\s*v\d+\s*$|^[A-Z0-9_\-]{1,12}$', re.I)
junk = {pid for pid, p in pairs.items() if PLACEHOLDER.search(p['A'].strip()) or PLACEHOLDER.search(p['B'].strip())}
print('placeholder pairs dropped:', len(junk))
for pid in junk:
    key.pop(pid, None)
    pairs.pop(pid, None)


def p_two_sided(k, n):
    # binomial test vs 50%
    if n == 0:
        return 1.0
    m = max(k, n - k)
    tail = sum(math.comb(n, i) for i in range(m, n + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def report(name, picks):
    groups = collections.defaultdict(lambda: [0, 0])
    for pid, pick in picks.items():
        k = key[pid]
        hit = pick == k['answer']
        for g in ('all', k['src'], k['src'] == 'archive' and 'ch:' + k['channel']):
            if g:
                groups[g][0] += hit
                groups[g][1] += 1
    print(f'\n== {name} ({len(picks)} picks)')
    for g in ['all', 'creatorhooks', 'archive'] + sorted(x for x in groups if x.startswith('ch:')):
        if g in groups:
            h, n = groups[g]
            print(f'  {g:32s} {h:4d}/{n:<4d} {100*h/n:5.1f}%  p={p_two_sided(h, n):.3f}')


methods = collections.defaultdict(dict)
for f in glob.glob(f'{D}/out/*.json'):
    m = os.path.basename(f).rsplit('_half', 1)[0]
    for r in json.load(open(f)):
        if r.get('pid') in key:
            methods[m][r['pid']] = r
for m, rows in sorted(methods.items()):
    report(m, {pid: r['pick'] for pid, r in rows.items()})
    if m == 'clarity':
        report('clarity (no ties)', {pid: r['pick'] for pid, r in rows.items() if not r.get('tie')})
    if m == 'anatomy':
        report('anatomy (rules decided)', {pid: r['pick'] for pid, r in rows.items() if not r.get('rules_tie')})
    if m == 'gut':
        report('gut (confidence 3)', {pid: r['pick'] for pid, r in rows.items() if r.get('confidence') == 3})

# dumb baselines
report('longer title', {pid: 'A' if len(p['A']) > len(p['B']) else 'B' for pid, p in pairs.items() if len(p['A']) != len(p['B'])})
report('more CAPS words', {pid: 'A' if sum(w.isupper() and len(w) > 1 for w in p['A'].split()) > sum(w.isupper() and len(w) > 1 for w in p['B'].split()) else 'B'
                           for pid, p in pairs.items()
                           if sum(w.isupper() and len(w) > 1 for w in p['A'].split()) != sum(w.isupper() and len(w) > 1 for w in p['B'].split())})

# substantive pairs only: drop reorders, year swaps, one-word edits
def words(s):
    return set(re.findall(r"[a-z']+", s.lower())) - {'the', 'a', 'an', 'and', 'of', 'to', 'in', 'with', 'podcast', 'ep', 'pbd'}
sub = {pid for pid, p in pairs.items() if (lambda a, b: len(a ^ b) >= 4 and len(a & b) / max(1, len(a | b)) < 0.6)(words(p['A']), words(p['B']))}
print('\n\n######## SUBSTANTIVE REWRITES ONLY:', len(sub), 'pairs')
for m, rows in sorted(methods.items()):
    report(m + ' [rewrites]', {pid: r['pick'] for pid, r in rows.items() if pid in sub})
report('longer title [rewrites]', {pid: 'A' if len(p['A']) > len(p['B']) else 'B' for pid, p in pairs.items() if pid in sub and len(p['A']) != len(p['B'])})
# agreement: all three methods agree
agree = {pid: methods['gut'][pid]['pick'] for pid in key if all(pid in methods[m] for m in ('gut', 'clarity', 'anatomy')) and len({methods[m][pid]['pick'] for m in ('gut', 'clarity', 'anatomy')}) == 1}
report('all 3 agree', agree)
