"""Build the tabbed page of docs/FAM_AGENTS.md: one tab per agent, sub-tabs per section.

    pip install markdown mdx_truly_sane_lists
    python docs/build_agents_html.py [out.html]   # default: docs/build/fam-agents.html

FAM_AGENTS.md is the source of truth. Each `## ` heading is a tab, the text
before its first `### ` is the Overview sub-tab, and each `### ` is a sub-tab.
A `<!-- stages: ... -->` line under the `## ` names the pipeline stages the
agent works in; the diagram at the top of its tab lights those up.
"""
import html, pathlib, re, sys
import markdown

ROOT = pathlib.Path(__file__).resolve().parent
SRC = ROOT / "FAM_AGENTS.md"
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "fam-agents.html"

# (id, label, lane). The listener path runs left to right; the archive's facts
# reach it only through research's recall.
STAGES = [
    ("ask", "Tap or search", "listener"), ("brief", "EI brief", "listener"),
    ("research", "Research + archive recall", "listener"), ("write", "Writer", "listener"),
    ("voice", "Voice", "listener"), ("cache", "Episode kept", "listener"),
    ("orchestrate", "Overlord", "control"),
    ("intake", "Archive intake", "background"), ("digest", "Facts: digest + dedupe", "background"),
]
LANES = [("listener", "Listener path - no agent here"), ("control", "Orchestration"),
         ("background", "Background, after the episode is kept")]


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def render(md):
    body = markdown.markdown(md, extensions=["tables", "fenced_code", "mdx_truly_sane_lists"])
    body = re.sub(r"<thead>\s*<tr>\s*<th></th>\s*<th></th>\s*</tr>\s*</thead>", "", body)
    return body.replace("<table>", '<div class="tw"><table>').replace("</table>", "</table></div>")


def split(src):
    """[(title, stages, [(subtitle, markdown)])] - the H1 and meta table go to the masthead."""
    head, *parts = re.split(r"^## ", src, flags=re.M)
    tabs = []
    for part in parts:
        title, _, rest = part.partition("\n")
        m = re.search(r"<!-- stages:(.*?)-->", rest)
        stages = m.group(1).split() if m else []
        rest = re.sub(r"<!-- stages:.*?-->\n?", "", rest)
        overview, *subs = re.split(r"^### ", rest, flags=re.M)
        sections = [("Overview", overview)] if overview.strip() else []
        sections += [tuple(s.split("\n", 1)) for s in subs]
        tabs.append((title.strip(), stages, sections))
    return head, tabs


def pipeline(stages):
    on = set(stages)
    rows = []
    for lane, label in LANES:
        steps = []
        for sid, name, l in STAGES:
            if l != lane:
                continue
            cls = "on" if sid in on else ("feeds" if sid == "research" and "digest" in on else "")
            steps.append(f'<li class="{cls}" data-stage="{sid}">{html.escape(name)}</li>')
        rows.append(f'<div class="lane lane-{lane}"><span class="lane-label">{label}</span><ol>{"".join(steps)}</ol></div>')
    note = '<p class="pipe-note">Dashed: where this agent\'s work reaches a listener.</p>' if "digest" in on else ""
    return f'<figure class="pipe" aria-label="Where it works in the pipeline">{"".join(rows)}{note}</figure>'


def build():
    head, tabs = split(SRC.read_text())
    intro = render(re.sub(r"\A# .*?\n", "", head))
    nav, panels = [], []
    for title, stages, sections in tabs:
        t = slug(title)
        label = "Shared rules" if t == "shared-rules" else title.replace("Database inventory specialist", "Inventory specialist")
        nav.append(f'<a class="tab" role="tab" href="#{t}" data-tab="{t}">{html.escape(label)}</a>')
        subnav, subs = [], []
        for name, md in sections:
            s = f"{t}--{slug(name)}"
            subnav.append(f'<a class="subtab" role="tab" href="#{s}" data-sub="{s}">{html.escape(name)}</a>')
            subs.append(f'<section class="sub" data-sub="{s}"><h2 class="subhead">{html.escape(name)}</h2>{render(md)}</section>')
        eyebrow = "Agent" if t not in ("shared-rules", "template") else "Every agent"
        panels.append(f'''<section class="panel" data-tab="{t}">
<header class="panelhead"><p class="eyebrow">{eyebrow}</p><h1>{html.escape(title)}</h1></header>
{pipeline(stages)}
<nav class="subtabs" role="tablist" aria-label="{html.escape(title)} sections">{"".join(subnav)}</nav>
{"".join(subs)}</section>''')
    page = TEMPLATE.replace("{{INTRO}}", intro).replace("{{TABS}}", "".join(nav)).replace("{{PANELS}}", "".join(panels))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page)
    return OUT


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FAM Agents</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400..700&family=Geist:wght@400..700&family=Geist+Mono:wght@400..700&display=swap" rel="stylesheet">
<style>
:root{color-scheme:light;
  --bg:#F4EFE4;--surface:#FBF8F1;--surface-2:#ECE5D6;--line:#DCD3C2;--text:#18151F;--muted:#5B5468;--faint:#857E93;
  --accent:#8C5E12;--accent-soft:#F1E3C6;--code-bg:#EDE6D8;
  --display:"Bricolage Grotesque",sans-serif;--body:"Geist",system-ui,-apple-system,"Segoe UI",sans-serif;--mono:"Geist Mono",ui-monospace,Menlo,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;
  --bg:#1E1B27;--surface:#2A2733;--surface-2:#38334A;--line:#433D55;--text:#F4EFE4;--muted:#ABA3C4;--faint:#8A83A0;
  --accent:#E0B563;--accent-soft:#3A3226;--code-bg:#25222F}}
:root[data-theme="dark"]{color-scheme:dark;
  --bg:#1E1B27;--surface:#2A2733;--surface-2:#38334A;--line:#433D55;--text:#F4EFE4;--muted:#ABA3C4;--faint:#8A83A0;
  --accent:#E0B563;--accent-soft:#3A3226;--code-bg:#25222F}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 var(--body);padding:0 16px 64px}
a{color:var(--accent)}
a:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
.wrap{max-width:1040px;margin:0 auto}
.masthead{padding:28px 0 0}
.mark{font:600 13px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--accent)}
.name{font:600 clamp(26px,4vw,36px)/1.1 var(--display);margin:6px 0 8px}
.intro{color:var(--muted);font-size:14px}
.intro p{max-width:78ch;margin:0 0 10px}
.intro .tw{max-width:760px}
.tabs{position:sticky;top:0;z-index:5;background:var(--bg);display:flex;gap:4px;border-bottom:1px solid var(--line);overflow-x:auto;margin-top:12px}
.tab{font:500 15px/1 var(--body);color:var(--muted);text-decoration:none;padding:14px 16px 12px;border-bottom:2px solid transparent;white-space:nowrap}
.tab[aria-selected="true"]{color:var(--text);border-bottom-color:var(--accent)}
.panelhead{padding:26px 0 6px}
.eyebrow{font:500 12px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--accent);margin:0 0 8px}
.panelhead h1{font:600 clamp(24px,3.4vw,32px)/1.2 var(--display);margin:0}
.pipe{margin:14px 0 18px;padding:14px;background:var(--surface);border:1px solid var(--line);border-radius:10px;display:grid;gap:10px}
.lane{display:grid;grid-template-columns:150px minmax(0,1fr);gap:10px;align-items:center}
.lane-label{font:500 11px/1.3 var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--faint)}
.lane ol{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:6px}
.lane li{font-size:12.5px;line-height:1.2;padding:6px 10px;border:1px solid var(--line);border-radius:999px;color:var(--faint);background:var(--bg);position:relative}
.lane li + li::before{content:"\2192";position:absolute;left:-11px;top:50%;transform:translateY(-50%);font-size:10px;color:var(--faint)}
.lane li.on{background:var(--accent);border-color:var(--accent);color:var(--bg);font-weight:600}
.lane li.feeds{border:1.5px dashed var(--accent);color:var(--text)}
.pipe-note{margin:0;font-size:12px;color:var(--faint)}
.subtabs{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 6px}
.subtab{font:500 13px/1 var(--body);color:var(--muted);text-decoration:none;padding:8px 12px;border:1px solid var(--line);border-radius:999px}
.subtab[aria-selected="true"]{background:var(--accent-soft);border-color:var(--accent);color:var(--text)}
.subhead{font:600 20px/1.3 var(--display);margin:22px 0 10px}
.sub p,.sub li{max-width:76ch}
.sub ul,.sub ol{padding-left:22px}
.sub li{margin:3px 0}
.sub h4{font:600 15px/1.3 var(--body);margin:20px 0 6px}
code{font:12.5px/1.4 var(--mono);background:var(--code-bg);padding:1px 5px;border-radius:4px}
.tw{overflow-x:auto;margin:12px 0 18px;border:1px solid var(--line);border-radius:8px;background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th{font:500 11px/1.3 var(--mono);letter-spacing:.05em;text-transform:uppercase;color:var(--muted);text-align:left;background:var(--surface-2);padding:8px 10px}
td{padding:8px 10px;border-top:1px solid var(--line);vertical-align:top;min-width:96px}
td:first-child{font-weight:500}
.js .panel:not(.active),.js .sub:not(.active){display:none}
.js .subhead{display:none}
@media (max-width:640px){.lane{grid-template-columns:1fr;gap:6px}}
@media (prefers-reduced-motion:no-preference){html{scroll-behavior:smooth}}
</style></head>
<body><div class="wrap">
<header class="masthead"><span class="mark">FAM · agents</span><p class="name">FAM agents</p><div class="intro">{{INTRO}}</div></header>
<nav class="tabs" role="tablist" aria-label="Agents">{{TABS}}</nav>
<main>{{PANELS}}</main>
</div>
<script>
(function(){
  document.documentElement.classList.add("js");
  var panels=[].slice.call(document.querySelectorAll(".panel"));
  function show(hash){
    var id=(hash||"").replace(/^#/,""), tab=id.split("--")[0];
    var panel=panels.filter(function(p){return p.dataset.tab===tab;})[0]||panels[0];
    panels.forEach(function(p){p.classList.toggle("active",p===panel);});
    document.querySelectorAll(".tab").forEach(function(t){t.setAttribute("aria-selected",t.dataset.tab===panel.dataset.tab?"true":"false");});
    var subs=[].slice.call(panel.querySelectorAll(".sub"));
    var sub=subs.filter(function(s){return s.dataset.sub===id;})[0]||subs[0];
    subs.forEach(function(s){s.classList.toggle("active",s===sub);});
    panel.querySelectorAll(".subtab").forEach(function(t){t.setAttribute("aria-selected",sub&&t.dataset.sub===sub.dataset.sub?"true":"false");});
  }
  document.addEventListener("click",function(e){
    var a=e.target.closest(".tab,.subtab"); if(!a) return;
    e.preventDefault(); var h=a.getAttribute("href");
    try{history.replaceState(null,"",h);}catch(_){}
    show(h);
  });
  window.addEventListener("hashchange",function(){show(location.hash);});
  show(location.hash);
})();
</script>
</body></html>
"""

if __name__ == "__main__":
    out = build()
    print("wrote", out, out.stat().st_size)
