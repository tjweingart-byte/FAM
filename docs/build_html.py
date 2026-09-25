"""Build the published HTML version of docs/ (FINANCIAL, BACKEND, DATA).

    pip install markdown mdx_truly_sane_lists
    python docs/build_html.py [out.html]      # default: docs/build/fam-docs.html

The markdown files are the source of truth; the page is generated from them
and published to the same artifact URL (see docs/README.md). Mermaid fences
become <pre class="mermaid"> blocks, which the artifact viewer renders.
"""
import re, sys, pathlib, html, markdown
ROOT = pathlib.Path(__file__).resolve().parent
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "fam-docs.html"
DOCS = [("financial","Financial","FINANCIAL.md"),("backend","Backend","BACKEND.md"),("data","Data","DATA.md")]
LINKS = {"FINANCIAL.md":"#financial","BACKEND.md":"#backend","DATA.md":"#data"}

def render(src):
    blocks = []
    def keep(m):
        blocks.append(m.group(1)); return f"\n\nMERMAIDBLOCK{len(blocks)-1}\n\n"
    src = re.sub(r"```mermaid\n(.*?)```", keep, src, flags=re.S)
    # drop the H1 and the metadata table: shown in the page header instead
    src = re.sub(r"\A# .*?\n", "", src)
    body = markdown.markdown(src, extensions=["tables","fenced_code","mdx_truly_sane_lists"])
    for i,b in enumerate(blocks):
        body = body.replace(f"<p>MERMAIDBLOCK{i}</p>", f'<div class="diagram"><pre class="mermaid">{html.escape(b)}</pre></div>')
    for k,v in LINKS.items():
        body = re.sub(rf'href="{re.escape(k)}(#[^"]*)?"', f'href="{v}"', body)
    body = re.sub(r"<thead>\s*<tr>\s*<th></th>\s*<th></th>\s*</tr>\s*</thead>", "", body)
    body = re.sub(r"<table>", '<div class="tw"><table>', body).replace("</table>","</table></div>")
    return body

def toc(body):
    items = re.findall(r"<h2>(.*?)</h2>", body)
    return items

sections, tabs = [], []
for slug,label,fn in DOCS:
    md = (ROOT/fn).read_text()
    title = re.match(r"# (.*)", md).group(1).split("—",1)[-1].strip()
    body = render(md)
    n = 0
    def anchor(m):
        global n
        n += 1
        return f'<h2 id="{slug}-{n}">{m.group(1)}</h2>'
    body = re.sub(r"<h2>(.*?)</h2>", anchor, body)
    heads = re.findall(r'<h2 id="([^"]+)">(.*?)</h2>', body)
    nav = "".join(f'<li><a href="#{i}">{re.sub("<.*?>","",t)}</a></li>' for i,t in heads)
    tabs.append(f'<a class="tab" href="#{slug}" data-tab="{slug}" role="tab">{label}</a>')
    sections.append(f'''<section class="doc" id="{slug}" data-doc="{slug}">
<header class="dochead"><p class="eyebrow">{label}</p><h1>{html.escape(title[0].upper()+title[1:])}</h1></header>
<div class="docgrid"><nav class="toc" aria-label="Contents"><p class="toc-label">Contents</p><ol>{nav}</ol></nav>
<article class="prose">{body}</article></div></section>''')

tpl = (ROOT / "_page_template.html").read_text()
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(tpl.replace("{{TABS}}","".join(tabs)).replace("{{SECTIONS}}","".join(sections)))
print("wrote", OUT, OUT.stat().st_size)
