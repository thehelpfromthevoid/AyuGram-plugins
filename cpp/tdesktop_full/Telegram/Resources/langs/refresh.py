import os, sys, re
from os.path import expanduser

filename = ''
for arg in sys.argv:
    if re.match(r'.*\.strings$', arg):
        filename = expanduser(arg)

if not os.path.isfile(filename):
    print("File not found.")
    sys.exit(1)

# AyuGram: keep our own keys across an upstream refresh.
# lang.extra.strings holds every key that upstream does not ship, in the same
# format, one key per line. Every key that is already present in the target
# file is left in place (keeping its comment), the rest is appended. That
# makes this script idempotent: running it twice changes nothing.
here = os.path.dirname(os.path.abspath(__file__))
extra = os.path.join(here, 'lang.extra.strings')

extra_lines = []
if os.path.isfile(extra):
    with open(extra, encoding='utf-8') as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith('//'):
                continue
            m = re.match(r'^"([^"]+)"\s*=', s)
            if not m:
                print('Bad line in lang.extra.strings: ' + s)
                sys.exit(1)
            extra_lines.append((m.group(1), line.rstrip('\n')))
else:
    print('lang.extra.strings not found, no local keys re-appended.')

seen = set()
result = []
with open(filename, encoding='utf-8') as f:
    for line in f:
        if re.match(r'\s*\/\* new from [a-zA-Z0-9\.]+ \*\/\s*', line):
            continue
        if re.match(r'"lng_[a-z0-9_]+\#(zero|two|few|many)"', line):
            continue
        m = re.match(r'^"([^"]+)"\s*=', line)
        if m:
            if m.group(1) in seen:
                continue
            seen.add(m.group(1))
        result.append(line.rstrip('\n'))

added = 0
for key, line in extra_lines:
    if key in seen:
        continue
    seen.add(key)
    added = added + 1
    result.append(line)

while result and not result[-1].strip():
    result.pop()

with open(filename, 'w', encoding='utf-8', newline='\n') as out:
    out.write('\n'.join(result) + '\n')

print('Re-appended %d local keys from lang.extra.strings.' % added)
sys.exit()
