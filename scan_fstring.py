"""Find backslashes inside f-string EXPRESSIONS — a SyntaxError on Python < 3.12."""
import ast, sys, io, tokenize

def scan(path):
    src = open(path, encoding="utf-8").read()
    bad = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except Exception:
        return bad
    for tok in toks:
        if tok.type != tokenize.STRING:
            continue
        text = tok.string
        # is it an f-string?
        prefix = text[:text.find(text.lstrip('rRbBuUfF')[0])].lower() if text.lstrip('rRbBuUfF') else ''
        if 'f' not in prefix:
            continue
        # walk the literal char by char, tracking brace depth (skip {{ }})
        i, depth = 0, 0
        while i < len(text):
            c = text[i]
            if c == '{':
                if i + 1 < len(text) and text[i+1] == '{':
                    i += 2; continue
                depth += 1
            elif c == '}':
                if depth: depth -= 1
            elif c == '\\' and depth > 0:
                bad.append((tok.start[0], text[max(0,i-30):i+30].replace('\n',' ')))
                break
            i += 1
    return bad

files = sys.argv[1:]
found = 0
for f in files:
    for line, snippet in scan(f):
        print(f"❌ {f}:{line}  …{snippet}…")
        found += 1
print(f"\n{'❌ ' + str(found) + ' problem(s)' if found else '✅ 0 backslash-in-f-string-expression issues'}")
sys.exit(1 if found else 0)
