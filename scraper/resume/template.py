"""Builds an HTML resume matching the visual style of the user's LaTeX
template (main (3).tex -- the "Jake's Resume" template: centered small-caps
header, section headers with a rule underneath, two-column subheading rows,
compact bullet lists), for printing to PDF via Playwright.

`render_html()` expects a *resolved* content dict -- unlike
experience_bank.json, bullets here are plain strings (already selected /
reworded by resume_tailor.py), not {id, text} objects.
"""
import html

CSS = """
@page { margin: 0; }
* { box-sizing: border-box; }
body {
  font-family: Georgia, "Times New Roman", serif;
  font-size: 10.5pt;
  color: #000;
  margin: 0;
  padding: 0.4in 0.5in;
}
.header { text-align: center; margin-bottom: 5pt; }
.header .name {
  font-size: 24pt;
  font-weight: bold;
  font-variant: small-caps;
}
.header .contact { font-size: 9.5pt; margin-top: 1pt; }
.header .contact a { color: #000; text-decoration: underline; }
.header .contact .sep { padding: 0 5pt; }

section { margin-top: 1pt; }
h2 {
  font-size: 12pt;
  font-variant: small-caps;
  font-weight: bold;
  border-bottom: 1px solid #000;
  margin: 5pt 0 2pt 0;
  padding: 0;
}

.entry { margin-bottom: 1pt; }
.entry-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.entry-row .left { font-weight: bold; }
.entry-row .right { font-size: 9.5pt; }
.entry-subrow {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-style: italic;
  font-size: 9.5pt;
  margin-bottom: 1pt;
}

ul.bullets {
  margin: 1pt 0 3pt 0;
  padding-left: 16pt;
}
ul.bullets li {
  font-size: 9.6pt;
  line-height: 1.2;
  margin-bottom: 0.5pt;
}

.project-heading { font-size: 9.8pt; margin-bottom: 1pt; }
.project-heading .name { font-weight: bold; }
.project-heading .stack { font-style: italic; }

.skills p {
  font-size: 9.8pt;
  margin: 1pt 0;
}
.skills b { font-weight: bold; }
"""


def _esc(text: str) -> str:
    return html.escape(text or "")


def _contact_line(header: dict) -> str:
    parts = [_esc(header.get("phone", ""))]
    if header.get("email"):
        parts.append(f'<a href="mailto:{_esc(header["email"])}">{_esc(header["email"])}</a>')
    if header.get("linkedin"):
        url = header["linkedin"]
        label = url.split("//")[-1]
        parts.append(f'<a href="{_esc(url)}">{_esc(label)}</a>')
    if header.get("github"):
        url = header["github"]
        label = url.split("//")[-1]
        parts.append(f'<a href="{_esc(url)}">{_esc(label)}</a>')
    return '<span class="sep">|</span>'.join(parts)


def _education_section(entries: list[dict]) -> str:
    rows = []
    for e in entries:
        rows.append(f"""
        <div class="entry">
          <div class="entry-row"><span class="left">{_esc(e.get('school',''))}</span><span class="right">{_esc(e.get('location',''))}</span></div>
          <div class="entry-subrow"><span>{_esc(e.get('detail',''))}</span><span>{_esc(e.get('dates',''))}</span></div>
        </div>""")
    return f"<section><h2>Education</h2>{''.join(rows)}</section>"


def _bullets_html(bullets: list[str]) -> str:
    if not bullets:
        return ""
    items = "".join(f"<li>{_esc(b)}</li>" for b in bullets)
    return f'<ul class="bullets">{items}</ul>'


def _role_entries_section(entries: list[dict], heading: str) -> str:
    """Renders a section shaped like Experience or Leadership -- entries
    with title/location on one row and company/dates italicized below.
    Omitted entirely if there are no entries (e.g. a tailored resume that
    dropped every leadership entry for a given role).
    """
    if not entries:
        return ""
    rows = []
    for e in entries:
        rows.append(f"""
        <div class="entry">
          <div class="entry-row"><span class="left">{_esc(e.get('title',''))}</span><span class="right">{_esc(e.get('location',''))}</span></div>
          <div class="entry-subrow"><span>{_esc(e.get('company',''))}</span><span>{_esc(e.get('dates',''))}</span></div>
          {_bullets_html(e.get('bullets', []))}
        </div>""")
    return f"<section><h2>{_esc(heading)}</h2>{''.join(rows)}</section>"


def _projects_section(entries: list[dict]) -> str:
    rows = []
    for p in entries:
        rows.append(f"""
        <div class="entry">
          <div class="project-heading"><span class="name">{_esc(p.get('name',''))}</span> | <span class="stack">{_esc(p.get('stack',''))}</span></div>
          {_bullets_html(p.get('bullets', []))}
        </div>""")
    return f"<section><h2>Projects</h2>{''.join(rows)}</section>"


def _skills_section(skills: dict) -> str:
    rows = "".join(
        f"<p><b>{_esc(k)}</b>: {_esc(v)}</p>" for k, v in skills.items() if v
    )
    return f'<section><h2>Technical Skills</h2><div class="skills">{rows}</div></section>'


def render_html(content: dict) -> str:
    header = content.get("header", {})
    body = "".join(
        [
            f'<div class="header"><div class="name">{_esc(header.get("name",""))}</div>'
            f'<div class="contact">{_contact_line(header)}</div></div>',
            _education_section(content.get("education", [])),
            _role_entries_section(content.get("experience", []), "Experience"),
            _projects_section(content.get("projects", [])),
            _role_entries_section(content.get("leadership", []), "Leadership"),
            _skills_section(content.get("skills", {})),
        ]
    )
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"
