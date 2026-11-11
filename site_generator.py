#!/usr/bin/env python3
"""Generates the site from content/page-content.md.

Outputs (all overwritten on every run):
  index.html, about.html, drawing.html, video.html, cv.html, workshops.html,
  404.html, sitemap.xml
  views/            responsive WebP images, share images, preview pages

The logic lives here (rather than in update_content.py) so it can be imported
both by the CI script and by the desktop content-manager app for offline
preview.  All paths in the generated HTML are relative, so the pages work both
on GitHub Pages and when opened directly from disk (file://).
"""
import datetime as dt
import html
import json
import re
import subprocess
from pathlib import Path

from PIL import Image

SITE_URL = "https://milanowacka.github.io"
SITE_NAME = "Mila Nowacka"
ARTIST_NAME = "Mila Nowacka"
ARTIST_SAME_AS = [
    "https://www.instagram.com/milanowacka",
    "https://milanowacka.bandcamp.com",
]

THUMB_WIDTHS = (800, 1600)      # grid / detail / about images
PREVIEW_WIDTHS = (1600, 2400)   # detail pages
OG_WIDTH, OG_HEIGHT = 1200, 630  # social share image
WEBP_QUALITY = 82
JPEG_QUALITY = 85
META_DESCRIPTION_MAX = 155

NAV = [("drawing.html", "Drawing"), ("video.html", "Video"), ("workshops.html", "Workshops"),
       ("cv.html", "CV"), ("about.html", "About")]


# --------------------------------------------------------------------------- #
# Markdown helpers
# --------------------------------------------------------------------------- #
def md_links_to_html(text: str) -> str:
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    return re.sub(r'(?<![\w*])\*([^*\n]+)\*(?![\w*])', r'<em>\1</em>', text)


def md_links_to_text(text: str) -> str:
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1', text)
    return re.sub(r'(?<![\w*])\*([^*\n]+)\*(?![\w*])', r'\1', text)


def plain_text(text: str) -> str:
    """Markdown → single-line plain text (for meta descriptions / JSON-LD)."""
    text = md_links_to_text(text)
    return re.sub(r'\s+', ' ', text).strip()


def truncate(text: str, limit: int = META_DESCRIPTION_MAX) -> str:
    text = plain_text(text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(' ', 1)[0]
    return cut.rstrip(' ,;:-–') + '…'


def prose(description: str) -> str:
    """Description without its leading metadata block (medium/size/year or
    duration/premieres): a paragraph made only of short lines is metadata."""
    paras = [p for p in re.split(r'\n\s*\n', description) if p.strip()]
    def is_meta_line(line: str) -> bool:
        return len(line.strip()) < 70 or 'premiere' in line.lower()
    body = [p for p in paras if not all(is_meta_line(line) for line in p.split('\n'))]
    return plain_text('\n\n'.join(body) if body else description)


def first_sentences(text: str, n: int = 2) -> str:
    text = plain_text(text)
    parts = re.split(r'(?<=[.!?])\s+', text)
    return ' '.join(parts[:n])


def split_description(description: str) -> tuple:
    """(caption, text): leading metadata paragraphs (details, medium, size,
    year / duration, premieres) vs. the descriptive prose, both as HTML."""
    def is_meta_line(line: str) -> bool:
        return len(line.strip()) < 70 or 'premiere' in line.lower()
    paras = [p.strip() for p in re.split(r'\n\s*\n', description) if p.strip()]
    meta, body = [], []
    for p in paras:
        lines = [l.strip() for l in p.split('\n') if l.strip()]
        (meta if not body and all(is_meta_line(l) for l in lines) else body).append(lines)
    def to_html(blocks):
        return ''.join('<p>' + '<br>'.join(md_links_to_html(l) for l in b) + '</p>' for b in blocks)
    return to_html(meta), to_html(body)


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def split_sections(content: str) -> dict:
    """{'About': '...', 'Drawing': '...', ...} keyed by top-level '# ' heading."""
    sections = {}
    for m in re.finditer(r'^# (.+?)\s*\n(.*?)(?=^# |\Z)', content, re.DOTALL | re.MULTILINE):
        sections[m.group(1).strip()] = m.group(2)
    return sections


# --------------------------------------------------------------------------- #
# Content parsing
# --------------------------------------------------------------------------- #
def parse_artwork_meta(description: str) -> dict:
    """Pull series / medium / size / year out of the first block of a description."""
    meta = {}
    for raw in description.split('\n'):
        line = raw.strip()
        if not line:
            break
        if line.startswith('['):
            continue
        m = re.match(r'^from (.+?) series$', line, re.I)
        if m:
            meta['series'] = m.group(1).strip()
            continue
        m = re.match(r'^(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*cm$', line, re.I)
        if m:
            meta['width_cm'] = m.group(1).replace(',', '.')
            meta['height_cm'] = m.group(2).replace(',', '.')
            continue
        if re.fullmatch(r'\d{4}', line):
            meta['year'] = line
            continue
        if 'medium' not in meta and (' on ' in line or 'pencil' in line.lower()):
            meta['medium'] = line
    return meta


def artwork_alt(title: str, meta: dict) -> str:
    alt = f"{title} – {meta.get('medium', 'drawing')}"
    if meta.get('series'):
        alt += f", from the {meta['series']} series"
    alt += f", by {ARTIST_NAME}"
    if meta.get('year'):
        alt += f", {meta['year']}"
    return alt


def parse_video_meta(description: str) -> dict:
    meta = {}
    m = re.search(r'duration:\s*(\d+):(\d{2})', description)
    if m:
        meta['duration_iso'] = f"PT{int(m.group(1))}M{int(m.group(2))}S"
        meta['duration_text'] = f"{m.group(1)}:{m.group(2)}"
    for m in re.finditer(r'([A-Z][a-z]+ \d{1,2}, \d{4})', description):
        try:
            d = dt.datetime.strptime(m.group(1), '%B %d, %Y').date()
        except ValueError:
            continue
        if 'premiere' not in meta or d < meta['premiere']:
            meta['premiere'] = d
    if 'premiere' in meta:
        meta['year'] = str(meta['premiere'].year)
    return meta


def strip_comments(text: str) -> str:
    return re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL).strip()


IMAGE_LINE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')


def parse_cv(raw: str) -> tuple:
    """CV markdown → ([(label, body_markdown)], [image dicts]).
    Labels are the ALL-CAPS lines (EXHIBITIONS, PUBLICATIONS, …)."""
    sections, images, cur = [], [], None
    for line in raw.split('\n'):
        st = line.strip()
        m = IMAGE_LINE.fullmatch(st)
        if m:
            images.append({'file': m.group(2).strip(), 'alt': m.group(1).strip()})
        elif re.fullmatch(r'[A-Z][A-Z ]{2,}', st):
            cur = [st, []]
            sections.append(cur)
        else:
            if cur is None:
                cur = ['', []]
                sections.append(cur)
            cur[1].append(line)
    return [(label, '\n'.join(body).strip()) for label, body in sections
            if label or '\n'.join(body).strip()], images


def parse_workshops(raw: str) -> list:
    """Each '## Title' block → {'title', 'text', 'images': [up to 3]}."""
    events = []
    for m in re.finditer(r'^## (.+?)\s*\n(.*?)(?=^## |\Z)', raw, re.DOTALL | re.MULTILINE):
        body = m.group(2)
        images = [{'file': i.group(2).strip(), 'alt': i.group(1).strip()} for i in IMAGE_LINE.finditer(body)]
        text = IMAGE_LINE.sub('', body).strip()
        events.append({'title': m.group(1).strip(), 'text': text, 'images': images[:3]})
    return events


def video_resolution(path: Path) -> str | None:
    """'1920 × 1080 px' read with ffprobe (None when ffprobe or the file is missing)."""
    try:
        out = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height', '-of', 'csv=p=0', str(path)],
            capture_output=True, text=True, check=True, timeout=30).stdout.strip()
        w, h = out.split(',')[:2]
        return f"{int(w)} × {int(h)} px"
    except (OSError, ValueError, subprocess.SubprocessError):
        print(f"  ! ffprobe could not read {path.name}; resolution omitted")
        return None


def parse_content(content: str) -> dict:
    sections = split_sections(content)

    # ---- About -----------------------------------------------------------
    about_raw = sections.get('About', '').strip()
    about_image = None
    m = re.match(r'!\[([^\]]*)\]\(([^)]+)\)\s*\n', about_raw)
    if m:
        about_image = m.group(2)
        about_raw = about_raw[m.end():].strip()
    about_intro = about_raw.split('\n\n', 1)[0] if about_raw else ''

    # ---- Home (optional; falls back to the first sentences of About) ------
    home_raw = sections.get('Home', '').strip()
    if not home_raw:
        home_raw = first_sentences(about_intro, 3)

    # ---- Drawings --------------------------------------------------------
    drawings = []
    pattern = r'## (.*?)\n!\[\]\((.*?)\)\n### Description\s*\n(.*?)(?=\n## |\Z)'
    for m in re.finditer(pattern, sections.get('Drawing', ''), re.DOTALL):
        title = m.group(1).strip()
        description = m.group(3).strip()
        meta = parse_artwork_meta(description)
        drawings.append({
            'title': title,
            'image': m.group(2).strip(),
            'description': description,
            'meta': meta,
            'alt': artwork_alt(title, meta),
        })

    # ---- Videos ----------------------------------------------------------
    videos = []
    pattern = r'## (.*?)\n!\[([^\]]+)\]\((.*?)\)\n### Description\s*\n(.*?)(?=\n## |\Z)'
    for m in re.finditer(pattern, sections.get('Video', ''), re.DOTALL):
        title = m.group(1).strip()
        description = m.group(4).strip()
        meta = parse_video_meta(description)
        alt = f"{title} – still from the video by {ARTIST_NAME}"
        if meta.get('year'):
            alt += f", {meta['year']}"
        videos.append({
            'title': title,
            'video': m.group(2).strip(),
            'thumbnail': m.group(3).strip(),
            'description': description,
            'meta': meta,
            'alt': alt,
        })

    return {
        'cv': strip_comments(sections.get('CV', '')),
        'workshops': strip_comments(sections.get('Workshops', '')),
        'home': home_raw,
        'about': about_raw,
        'about_intro': about_intro,
        'about_image': about_image,
        'drawings': drawings,
        'videos': videos,
    }


def parse_upcoming_events(about_raw: str) -> list:
    """Bullets under 'Upcoming:' → schema.org Event dicts (best effort)."""
    events = []
    block = re.search(r'^Upcoming:\s*\n((?:·.*\n?)+)', about_raw, re.MULTILINE | re.IGNORECASE)
    if not block:
        return events
    date_re = r'(\d{1,2})\.(\d{1,2})\.(\d{4})'
    for line in block.group(1).splitlines():
        line = line.strip().lstrip('·').strip()
        m = re.match(rf'{date_re}\s*[–-]\s*{date_re}\s*[–-]\s*(.*)$', line)
        if not m:
            continue
        start = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
        end = f"{m.group(6)}-{int(m.group(5)):02d}-{int(m.group(4)):02d}"
        rest = m.group(7).strip()
        link = re.match(r'\[([^\]]+)\]\(([^)]+)\)', rest)
        text, url = (link.group(1), link.group(2)) if link else (rest, None)
        parts = [p.strip() for p in text.split(',')]
        event = {
            "@type": "ExhibitionEvent",
            "name": parts[0],
            "startDate": start,
            "endDate": end,
            "performer": {"@type": "Person", "name": ARTIST_NAME},
        }
        if len(parts) > 1:
            event["location"] = {"@type": "Place", "name": parts[1],
                                 "address": ', '.join(parts[2:]) or parts[1]}
        if url:
            event["url"] = url
        events.append(event)
    return events


def render_about_blocks(about_raw: str) -> str:
    """About markdown → semantic HTML (headings, lists, paragraphs)."""
    out, para, items = [], [], []

    def flush_para():
        if para:
            out.append('<p>' + '<br>'.join(para) + '</p>')
            para.clear()

    def flush_list():
        if items:
            out.append('<ul>' + ''.join(f'<li>{i}</li>' for i in items) + '</ul>')
            items.clear()

    for raw in about_raw.split('\n'):
        line = raw.strip()
        if not line:
            flush_para()
            flush_list()
        elif re.fullmatch(r'[A-Z][A-Z ]{2,}', line):
            flush_para(); flush_list()
            out.append(f'<h2>{line}</h2>')
        elif line.endswith(':') and len(line) < 30 and not line.startswith('·'):
            flush_para(); flush_list()
            out.append(f'<h3>{line}</h3>')
        elif line.startswith('·'):
            flush_para()
            items.append(md_links_to_html(line[1:].strip()))
        else:
            flush_list()
            para.append(md_links_to_html(line))
    flush_para()
    flush_list()
    return '\n\t\t\t\t'.join(out)


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #
def slugify(filename: str) -> str:
    return re.sub(r'[^A-Za-z0-9_-]+', '-', Path(filename).stem).strip('-')


class ImageBuilder:
    """Writes resized derivatives into views/ and skips work already done.

    A manifest (views/manifest.json) records the source size for every output,
    so an unchanged source is not re-encoded on every run, while a replaced
    source file (different size) is picked up automatically.
    """

    def __init__(self, content_dir: Path, views_dir: Path):
        self.content_dir = content_dir
        self.views_dir = views_dir
        self.manifest_path = views_dir / 'manifest.json'
        try:
            self.manifest = json.loads(self.manifest_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.manifest = {}
        self.produced = set()

    def _entry(self, out_name: str, src: Path):
        entry = self.manifest.get(out_name)
        if entry and entry.get('source_size') == src.stat().st_size and (self.views_dir / out_name).exists():
            return entry
        return None

    def _record(self, out_name: str, src: Path, size):
        self.manifest[out_name] = {
            'source': src.name, 'source_size': src.stat().st_size,
            'width': size[0], 'height': size[1],
        }

    def resized(self, image_filename: str, width: int, fmt: str = 'webp') -> dict:
        """Returns {'name', 'width', 'height'} for a width-constrained derivative."""
        src = self.content_dir / image_filename
        out_name = f"{slugify(image_filename)}_{width}.{fmt}"
        self.produced.add(out_name)
        entry = self._entry(out_name, src)
        if entry is None:
            with Image.open(src) as img:
                img = img.convert('RGB')
                if img.width > width:
                    img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
                self._save(img, self.views_dir / out_name, fmt)
                size = img.size
            self._record(out_name, src, size)
            entry = self.manifest[out_name]
        return {'name': out_name, 'width': entry['width'], 'height': entry['height']}

    def share_image(self, image_filename: str, out_name: str) -> dict:
        """Centre-cropped 1200×630 JPEG for Open Graph / Twitter cards."""
        src = self.content_dir / image_filename
        self.produced.add(out_name)
        if self._entry(out_name, src) is None:
            with Image.open(src) as img:
                img = img.convert('RGB')
                target = OG_WIDTH / OG_HEIGHT
                w, h = img.size
                if w / h > target:
                    new_w = round(h * target)
                    box = ((w - new_w) // 2, 0, (w - new_w) // 2 + new_w, h)
                else:
                    new_h = round(w / target)
                    box = (0, (h - new_h) // 2, w, (h - new_h) // 2 + new_h)
                img = img.crop(box).resize((OG_WIDTH, OG_HEIGHT), Image.LANCZOS)
                self._save(img, self.views_dir / out_name, 'jpeg')
            self._record(out_name, src, (OG_WIDTH, OG_HEIGHT))
        return {'name': out_name, 'width': OG_WIDTH, 'height': OG_HEIGHT}

    @staticmethod
    def _save(img, dst: Path, fmt: str):
        if fmt == 'webp':
            img.save(dst, 'WEBP', quality=WEBP_QUALITY, method=6)
        else:
            img.save(dst, 'JPEG', quality=JPEG_QUALITY, optimize=True, progressive=True)

    def finish(self, keep: set):
        """Write manifest and delete anything in views/ this run did not produce."""
        self.manifest = {k: v for k, v in self.manifest.items() if k in self.produced}
        self.manifest_path.write_text(json.dumps(self.manifest, indent=1, sort_keys=True), encoding='utf-8')
        wanted = self.produced | keep | {self.manifest_path.name}
        for f in self.views_dir.iterdir():
            if f.is_file() and f.name not in wanted and not f.name.startswith('.'):
                f.unlink()


def srcset(variants: list, prefix: str) -> str:
    return ', '.join(f"{prefix}views/{v['name']} {v['width']}w" for v in variants)


# --------------------------------------------------------------------------- #
# HTML template
# --------------------------------------------------------------------------- #
def jsonld_script(data) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=1).replace('</', '<\\/')
    return f'<script type="application/ld+json">\n{text}\n</script>'


def render_page(*, title, description, path, body, nav_html, prefix='',
                og_image=None, og_image_alt='', og_type='website',
                jsonld=None, noindex=False, body_class='', preload_image=None):
    canonical = f"{SITE_URL}/{path}" if path != 'index.html' else f"{SITE_URL}/"
    head = [
        '<meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f'<title>{esc(title)}</title>',
        f'<meta name="description" content="{esc(description)}">',
        f'<link rel="canonical" href="{canonical}">',
    ]
    if noindex:
        head.append('<meta name="robots" content="noindex, follow">')
    head += [
        f'<meta property="og:type" content="{og_type}">',
        f'<meta property="og:site_name" content="{SITE_NAME}">',
        f'<meta property="og:title" content="{esc(title)}">',
        f'<meta property="og:description" content="{esc(description)}">',
        f'<meta property="og:url" content="{canonical}">',
        '<meta property="og:locale" content="en_GB">',
    ]
    if og_image:
        head += [
            f'<meta property="og:image" content="{SITE_URL}/views/{og_image["name"]}">',
            f'<meta property="og:image:width" content="{og_image["width"]}">',
            f'<meta property="og:image:height" content="{og_image["height"]}">',
            f'<meta property="og:image:alt" content="{esc(og_image_alt)}">',
            '<meta name="twitter:card" content="summary_large_image">',
            f'<meta name="twitter:image" content="{SITE_URL}/views/{og_image["name"]}">',
        ]
    head += [
        f'<meta name="twitter:title" content="{esc(title)}">',
        f'<meta name="twitter:description" content="{esc(description)}">',
        f'<link rel="icon" href="{prefix}favicon.ico" sizes="32x32">',
        f'<link rel="preload" href="{prefix}content/open-sans-latin-300.woff2" as="font" type="font/woff2" crossorigin>',
    ]
    if preload_image:
        head.append(f'<link rel="preload" as="image" href="{prefix}views/{preload_image["name"]}">')
    head.append(f'<link rel="stylesheet" href="{prefix}style.css">')
    if jsonld:
        head.append(jsonld_script(jsonld))

    body_attr = f' class="{body_class}"' if body_class else ''
    head_html = '\n\t\t'.join(head)
    return f"""<!DOCTYPE html>
<html lang="en">

	<head>
		{head_html}
	</head>

	<body{body_attr}>
		<header>
			<nav aria-label="Main">
				{nav_html}
			</nav>
		</header>
{body}
{page_script()}
	</body>
</html>
"""


def main_nav(active: str, prefix: str = '') -> str:
    items = []
    for href, label in NAV:
        current = ' aria-current="page"' if href == active else ''
        items.append(f'<li><a href="{prefix}{href}"{current}>{label}</a></li>')
    items_html = '\n\t\t\t\t\t'.join(items)
    return f'''<h1 id="logo"><a href="{prefix}index.html">Mila Nowacka</a></h1>
					<button class="menu-toggle" type="button" aria-expanded="false" aria-controls="main-menu" aria-label="Menu">
						<img src="{prefix}content/slimaczek.png" width="74" height="74" alt="">
					</button>
					<ul id="main-menu">
					{items_html}
					</ul>'''


def page_script() -> str:
    return """		<script>
			const menuToggle = document.querySelector('.menu-toggle');
			menuToggle.addEventListener('click', () => {
				const open = document.body.classList.toggle('menu-open');
				menuToggle.setAttribute('aria-expanded', open);
			});
			// Works "train": wheel and mouse-drag scroll it sideways (touch/trackpad scroll natively)
			const train = document.querySelector('.works');
			if (train) {
				train.addEventListener('wheel', (e) => {
					if (window.innerWidth > 700 && Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
						train.scrollLeft += e.deltaY;
						e.preventDefault();
					}
				}, { passive: false });
				let down = false, moved = false, startX = 0, startLeft = 0;
				train.addEventListener('pointerdown', (e) => {
					if (e.pointerType !== 'mouse') return;
					down = true; moved = false; startX = e.clientX; startLeft = train.scrollLeft;
				});
				window.addEventListener('pointermove', (e) => {
					if (!down) return;
					const dx = e.clientX - startX;
					if (Math.abs(dx) > 4) { moved = true; train.classList.add('dragging'); }
					train.scrollLeft = startLeft - dx;
				});
				window.addEventListener('pointerup', () => { down = false; train.classList.remove('dragging'); });
				train.addEventListener('click', (e) => { if (moved) { e.preventDefault(); moved = false; } }, true);
				train.addEventListener('dragstart', (e) => e.preventDefault());
			}
		</script>"""


def person_jsonld(data: dict, portrait: dict | None) -> dict:
    person = {
        "@type": "Person",
        "@id": f"{SITE_URL}/#person",
        "name": ARTIST_NAME,
        "url": f"{SITE_URL}/",
        "jobTitle": "Visual artist",
        "description": plain_text(data['about_intro']),
        "sameAs": ARTIST_SAME_AS,
        "knowsAbout": ["drawing", "video art", "feedback", "art history", "media theory"],
    }
    if portrait:
        person["image"] = f"{SITE_URL}/views/{portrait['name']}"
    return person


def artwork_jsonld(d: dict, image: dict, url: str) -> dict:
    meta = d['meta']
    obj = {
        "@type": "VisualArtwork",
        "name": d['title'],
        "url": url,
        "image": f"{SITE_URL}/views/{image['name']}",
        "description": prose(d['description']),
        "artform": "Drawing",
        "creator": {"@id": f"{SITE_URL}/#person"},
        "inLanguage": "en",
    }
    if meta.get('medium'):
        obj["artMedium"] = meta['medium']
    if meta.get('series'):
        obj["isPartOf"] = {"@type": "CreativeWorkSeries", "name": f"{meta['series']} series"}
    if meta.get('year'):
        obj["dateCreated"] = meta['year']
    if meta.get('width_cm'):
        obj["width"] = {"@type": "QuantitativeValue", "value": meta['width_cm'], "unitCode": "CMT"}
        obj["height"] = {"@type": "QuantitativeValue", "value": meta['height_cm'], "unitCode": "CMT"}
    return obj


def video_jsonld(v: dict, poster: dict, url: str) -> dict:
    meta = v['meta']
    obj = {
        "@type": "VideoObject",
        "name": v['title'],
        "url": url,
        "description": prose(v['description']),
        "thumbnailUrl": f"{SITE_URL}/views/{poster['name']}",
        "contentUrl": f"{SITE_URL}/content/{v['video']}",
        "creator": {"@id": f"{SITE_URL}/#person"},
        "inLanguage": "en",
    }
    if meta.get('duration_iso'):
        obj["duration"] = meta['duration_iso']
    if meta.get('premiere'):
        obj["uploadDate"] = meta['premiere'].isoformat()
        obj["datePublished"] = meta['premiere'].isoformat()
    return obj


# --------------------------------------------------------------------------- #
# Site generation
# --------------------------------------------------------------------------- #
def work_card(anchor: str, title: str, caption_html: str, href: str, variants: list,
              alt: str, label: str, eager: bool = False, kind: str = 'drawing') -> str:
    """Train tile: caption above, image (linking to the detail page) below."""
    loading = 'fetchpriority="high"' if eager else 'loading="lazy"'
    sizes = '(max-width: 700px) 160vw, 464px'  # tiles are shown enlarged (see --tile-zoom)
    return f'''<article id="{anchor}" class="work {kind}">
					<div class="caption"><h2>{esc(title)}</h2>{caption_html}</div>
					<a href="{href}" aria-label="{esc(label)}">
						<img src="views/{variants[0]['name']}" srcset="{srcset(variants, '')}"
							sizes="{sizes}"
							width="{variants[0]['width']}" height="{variants[0]['height']}"
							alt="{esc(alt)}" {loading}>
					</a>
				</article>'''


def zones_page(*, caption: str = '', text: str = '', media: str = '', css: str = '') -> str:
    """Three-zone content layout: left margin | text block | image."""
    return f'''<article class="detail {css}">
				<div class="detail-caption">{caption}</div>
				<div class="detail-text">{text}</div>
				{media}
			</article>'''


def generate_site(base_dir: Path = Path(".")) -> None:
    base_dir = Path(base_dir)
    content_dir = base_dir / "content"
    views_dir = base_dir / "views"
    views_dir.mkdir(exist_ok=True)

    content = (content_dir / "page-content.md").read_text(encoding="utf-8")
    data = parse_content(content)
    drawings, videos = data['drawings'], data['videos']
    images = ImageBuilder(content_dir, views_dir)
    today = dt.date.today().isoformat()
    html_outputs = set()

    # ---- Shared assets ---------------------------------------------------
    portrait = None
    portrait_variants = []
    if data['about_image']:
        portrait_variants = [images.resized(data['about_image'], w) for w in THUMB_WIDTHS]
        portrait = portrait_variants[-1]

    site_share = None
    if drawings:
        site_share = images.share_image(drawings[0]['image'], 'og-image.jpg')
    elif data['about_image']:
        site_share = images.share_image(data['about_image'], 'og-image.jpg')
    site_share_alt = drawings[0]['alt'] if drawings else f"Portrait of {ARTIST_NAME}"

    person = person_jsonld(data, portrait)
    website = {
        "@type": "WebSite",
        "@id": f"{SITE_URL}/#website",
        "url": f"{SITE_URL}/",
        "name": SITE_NAME,
        "inLanguage": "en",
        "publisher": {"@id": f"{SITE_URL}/#person"},
    }

    # ---- Drawings: images, detail pages ------------------------
    drawing_graph, drawing_sitemap_images = [], []
    series_names = []
    for i, d in enumerate(drawings, 1):
        thumbs = [images.resized(d['image'], w) for w in THUMB_WIDTHS]
        previews = [images.resized(d['image'], w) for w in PREVIEW_WIDTHS]
        share = images.share_image(d['image'], f"{slugify(d['image'])}_og.jpg")
        d['thumbs'], d['previews'], d['share'] = thumbs, previews, share
        if d['meta'].get('series') and d['meta']['series'] not in series_names:
            series_names.append(d['meta']['series'])

        preview_name = f"{slugify(d['image'])}_preview.html"
        page_url = f"{SITE_URL}/drawing.html#drawing{i}"
        artwork = artwork_jsonld(d, thumbs[-1], page_url)
        drawing_graph.append(artwork)
        drawing_sitemap_images.append((thumbs[-1]['name'], d['title']))

        # detail page (noindex: the drawing page carries the structured data)
        caption_html, text_html = split_description(d['description'])
        d['caption_html'], d['detail_href'] = caption_html, f"views/{preview_name}"
        preview_body = f"""		<main>
			<article class="detail">
				<div class="detail-caption"><h2>{esc(d['title'])}</h2>{caption_html}</div>
				<div class="detail-text">{text_html}</div>
				<figure class="detail-media">
					<img src="../views/{previews[0]['name']}" srcset="{srcset(previews, '../')}"
						sizes="(max-width: 700px) 100vw, 35vw"
						width="{previews[0]['width']}" height="{previews[0]['height']}"
						alt="{esc(d['alt'])}" fetchpriority="high">
				</figure>
			</article>
		</main>"""
        desc_text = prose(d['description'])
        (views_dir / preview_name).write_text(render_page(
            title=f"{d['title']} – drawing by {ARTIST_NAME}",
            description=truncate(f"{d['alt']}. {first_sentences(desc_text, 1)}"),
            path=f"views/{preview_name}",
            body=preview_body,
            nav_html=main_nav('drawing.html', '../'),
            prefix='../',
            og_image=share, og_image_alt=d['alt'], og_type='article',
            jsonld={"@context": "https://schema.org", "@graph": [dict(artwork, url=f"{SITE_URL}/views/{preview_name}"), person]},
            noindex=True, body_class='detail-page', preload_image=previews[0],
        ), encoding='utf-8')
        html_outputs.add(preview_name)
        print(f"  ✓ {d['image']} → images + detail page")

        d['card'] = work_card(f"drawing{i}", d['title'], caption_html, d['detail_href'],
                              thumbs, d['alt'], f"View {d['title']} in detail", eager=(i == 1))

    # ---- Videos: poster images, detail pages -------------------
    video_graph, video_sitemap_entries = [], []
    for i, v in enumerate(videos, 1):
        posters = [images.resized(v['thumbnail'], w) for w in THUMB_WIDTHS]
        share = images.share_image(v['thumbnail'], f"{slugify(v['thumbnail'])}_og.jpg")
        v['posters'], v['share'] = posters, share
        preview_name = f"{slugify(v['video'])}_preview.html"
        page_url = f"{SITE_URL}/video.html#video{i}"
        vobj = video_jsonld(v, posters[-1], page_url)
        video_graph.append(vobj)
        video_sitemap_entries.append((v, posters[-1]))

        caption_html, text_html = split_description(v['description'])
        preview_body = f"""		<main>
			<article class="detail video-detail">
				<div class="detail-caption"><h2>{esc(v['title'])}</h2>{caption_html}</div>
				<div class="detail-text">{text_html}</div>
				<div class="detail-media">
					<video class="detail-video" src="../content/{v['video']}" poster="../views/{posters[-1]['name']}"
						controls playsinline preload="metadata" aria-label="{esc(v['title'])} by {ARTIST_NAME}"></video>
				</div>
			</article>
		</main>"""
        desc_text = prose(v['description'])
        (views_dir / preview_name).write_text(render_page(
            title=f"{v['title']} – video by {ARTIST_NAME}",
            description=truncate(f"{v['alt']}. {first_sentences(desc_text, 1)}"),
            path=f"views/{preview_name}",
            body=preview_body,
            nav_html=main_nav('video.html', '../'),
            prefix='../',
            og_image=share, og_image_alt=v['alt'], og_type='video.other',
            jsonld={"@context": "https://schema.org", "@graph": [dict(vobj, url=f"{SITE_URL}/views/{preview_name}"), person]},
            noindex=True, body_class='detail-page',
        ), encoding='utf-8')
        html_outputs.add(preview_name)
        print(f"  ✓ {v['thumbnail']} → images + video detail page")

        tile_lines = []
        if v['meta'].get('duration_text'):
            tile_lines.append(f"duration: {v['meta']['duration_text']} minutes")
        resolution = video_resolution(content_dir / v['video'])
        if resolution:
            tile_lines.append(resolution)
        if v['meta'].get('year'):
            tile_lines.append(v['meta']['year'])
        tile_caption = '<p>' + '<br>'.join(tile_lines) + '</p>' if tile_lines else caption_html
        v['card'] = work_card(f"video{i}", v['title'], tile_caption, f"views/{preview_name}",
                              posters, v['alt'], f"Play {v['title']}", eager=(i == 1), kind='video')

    def grid(cards: list, label: str) -> str:
        joined = '\n\t\t\t\t'.join(cards)
        return f'''<section class="works" aria-label="{label}">
				{joined}
			</section>'''

    # ---- index.html -------------------------------------------------------
    index_body = f"""		<main>
			{grid([d['card'] for d in drawings], 'Selected drawings')}
		</main>"""
    (base_dir / 'index.html').write_text(render_page(
        title=f"{ARTIST_NAME} – visual artist | drawing and video",
        description=truncate(data['home']),
        path='index.html',
        body=index_body,
        nav_html=main_nav(''),
        og_image=site_share, og_image_alt=site_share_alt,
        jsonld={"@context": "https://schema.org", "@graph": [website, person]},
        preload_image=drawings[0]['thumbs'][0] if drawings else None,
        body_class='home-page',
    ), encoding='utf-8')
    print("✓ Wrote index.html")

    # ---- about.html -------------------------------------------------------
    about_blocks = render_about_blocks(data['about'])
    about_media = ''
    if portrait_variants:
        about_media = f'''<figure class="detail-media">
					<img src="views/{portrait_variants[0]['name']}" srcset="{srcset(portrait_variants, '')}"
						sizes="(max-width: 700px) 100vw, 400px" width="{portrait_variants[0]['width']}" height="{portrait_variants[0]['height']}"
						alt="Portrait of {ARTIST_NAME}">
				</figure>'''
    about_body = f"""		<main>
			{zones_page(text=about_blocks, media=about_media, css='about')}
		</main>"""
    about_graph = [dict(person, mainEntityOfPage=f"{SITE_URL}/about.html")]
    (base_dir / 'about.html').write_text(render_page(
        title=f"About – biography | {ARTIST_NAME}",
        description=truncate(data['about_intro']),
        path='about.html',
        body=about_body,
        nav_html=main_nav('about.html'),
        og_image=site_share, og_image_alt=site_share_alt, og_type='profile',
        jsonld={"@context": "https://schema.org", "@graph": about_graph},
        preload_image=portrait_variants[0] if portrait_variants else None,
    ), encoding='utf-8')
    print("✓ Wrote about.html")

    # ---- drawing.html -----------------------------------------------------
    series_part = ' and '.join(f"{s}" for s in series_names)
    drawing_title = f"Drawings – {series_part} series | {ARTIST_NAME}" if series_names else f"Drawings | {ARTIST_NAME}"
    by_series = {}
    for d in drawings:
        by_series.setdefault(d['meta'].get('series', 'other'), []).append(d['title'])
    drawing_desc = f"Colour pencil drawings by {ARTIST_NAME}: " + '; '.join(
        f"the {s} series ({', '.join(t[:4])}{'…' if len(t) > 4 else ''})" for s, t in by_series.items()) + '.'
    drawing_body = f"""		<main>
			{grid([d['card'] for d in drawings], 'Drawings')}
		</main>"""
    (base_dir / 'drawing.html').write_text(render_page(
        title=drawing_title,
        description=truncate(drawing_desc),
        path='drawing.html',
        body=drawing_body,
        nav_html=main_nav('drawing.html'),
        og_image=site_share, og_image_alt=site_share_alt,
        jsonld={"@context": "https://schema.org", "@graph": [
            {"@type": "CollectionPage", "name": drawing_title, "url": f"{SITE_URL}/drawing.html",
             "isPartOf": {"@id": f"{SITE_URL}/#website"},
             "hasPart": drawing_graph},
            person]},
        preload_image=drawings[0]['thumbs'][-1] if drawings else None,
    ), encoding='utf-8')
    print(f"✓ Wrote drawing.html with {len(drawings)} drawings")

    # ---- video.html -------------------------------------------------------
    video_names = ', '.join(v['title'] for v in videos)
    video_title = f"Video works – {video_names} | {ARTIST_NAME}" if videos else f"Video | {ARTIST_NAME}"
    video_desc = f"Video works by {ARTIST_NAME}: " + '; '.join(
        f"{v['title']}" + (f" ({v['meta']['year']})" if v['meta'].get('year') else '') for v in videos) + '. '
    if videos:
        video_desc += first_sentences(prose(videos[0]['description']), 1)
    video_body = f"""		<main>
			{grid([v['card'] for v in videos], 'Video works')}
		</main>"""
    (base_dir / 'video.html').write_text(render_page(
        title=video_title,
        description=truncate(video_desc),
        path='video.html',
        body=video_body,
        nav_html=main_nav('video.html'),
        og_image=videos[0]['share'] if videos else site_share,
        og_image_alt=videos[0]['alt'] if videos else site_share_alt,
        jsonld={"@context": "https://schema.org", "@graph": [
            {"@type": "CollectionPage", "name": video_title, "url": f"{SITE_URL}/video.html",
             "isPartOf": {"@id": f"{SITE_URL}/#website"},
             "hasPart": video_graph},
            person]},
        preload_image=videos[0]['posters'][-1] if videos else None,
    ), encoding='utf-8')
    print(f"✓ Wrote video.html with {len(videos)} videos")

    # ---- cv.html ----------------------------------------------------------
    def content_images(items: list, sizes: str, css: str = '') -> str:
        out = []
        for im in items:
            variants = [images.resized(im['file'], w) for w in THUMB_WIDTHS]
            v = variants[0]
            out.append(f'<img src="views/{v["name"]}" srcset="{srcset(variants, "")}" sizes="{sizes}" '
                       f'width="{v["width"]}" height="{v["height"]}" alt="{esc(im["alt"])}" loading="lazy">')
        return '\n\t\t\t\t\t'.join(out)

    cv_sections, cv_images = parse_cv(data['cv'])
    text_pages = []
    if cv_sections:
        rows = []
        for label, body in cv_sections:
            rows.append(f'<div class="detail-caption"><h2>{esc(label)}</h2></div>\n\t\t\t\t'
                        f'<div class="detail-text">{render_about_blocks(body)}</div>')
        media = ''
        if cv_images:
            media = (f'<div class="detail-media stack" style="grid-row: 1 / span {len(cv_sections)}">'
                     f'{content_images(cv_images, "(max-width: 700px) 100vw, 18vw")}</div>')
        cv_body = f"""		<main>
			<article class="detail cv">
				{chr(10).join(rows)}
				{media}
			</article>
		</main>"""
    else:
        cv_body = '\t\t<main>\n\t\t\t<section class="text-page" aria-label="CV"></section>\n\t\t</main>'
    (base_dir / 'cv.html').write_text(render_page(
        title=f"CV – exhibitions, publications and collaborations | {ARTIST_NAME}",
        description=f"Curriculum vitae of {ARTIST_NAME}: exhibitions, publications, collaborations and video shows.",
        path='cv.html',
        body=cv_body,
        nav_html=main_nav('cv.html'),
        og_image=site_share, og_image_alt=site_share_alt,
        jsonld={"@context": "https://schema.org",
                "@graph": [dict(person, mainEntityOfPage=f"{SITE_URL}/cv.html")] + parse_upcoming_events(data['cv'])},
        noindex=not cv_sections,
    ), encoding='utf-8')
    if cv_sections:
        text_pages.append('cv.html')
    print(f"✓ Wrote cv.html ({len(cv_sections)} section(s))")

    # ---- workshops.html ---------------------------------------------------
    events = parse_workshops(data['workshops'])
    if events:
        event_html = []
        for ev in events:
            media = ''
            if ev['images']:
                media = (f'<div class="detail-media trio">'
                         f'{content_images(ev["images"], "(max-width: 700px) 100vw, 36vw")}</div>')
            event_html.append(zones_page(caption=f'<h2>{esc(ev["title"])}</h2>',
                                         text=render_about_blocks(ev['text']), media=media, css='event'))
        workshops_body = '\t\t<main>\n\t\t\t' + '\n\t\t\t'.join(event_html) + '\n\t\t</main>'
        workshops_desc = truncate(events[0]['text']) or f"Workshops by {ARTIST_NAME}."
        text_pages.append('workshops.html')
    else:
        workshops_body = '\t\t<main>\n\t\t\t<section class="text-page" aria-label="Workshops"></section>\n\t\t</main>'
        workshops_desc = f"Workshops by {ARTIST_NAME}, visual artist working in drawing and video."
    (base_dir / 'workshops.html').write_text(render_page(
        title=f"Workshops | {ARTIST_NAME}",
        description=workshops_desc,
        path='workshops.html',
        body=workshops_body,
        nav_html=main_nav('workshops.html'),
        og_image=site_share, og_image_alt=site_share_alt,
        noindex=not events,
    ), encoding='utf-8')
    print(f"✓ Wrote workshops.html ({len(events)} event(s){'' if events else ', empty, noindex'})")

    # ---- 404.html ---------------------------------------------------------
    body_404 = """		<main>
			<section class="text-page">
				<p>Page not found.</p>
				<p><a href="/">home</a> · <a href="/drawing.html">drawings</a> · <a href="/video.html">video works</a> · <a href="/about.html">about the artist</a></p>
			</section>
		</main>"""
    (base_dir / '404.html').write_text(render_page(
        title=f"Page not found | {ARTIST_NAME}",
        description=f"This page does not exist. Browse the drawings, video works and biography of {ARTIST_NAME}.",
        path='404.html',
        body=body_404,
        nav_html=main_nav(''),
        noindex=True,
    ), encoding='utf-8')

    # ---- sitemap.xml ------------------------------------------------------
    urls = []
    urls.append(f"""  <url>
    <loc>{SITE_URL}/</loc>
    <lastmod>{today}</lastmod>
  </url>""")
    image_xml = ''.join(f"""
    <image:image>
      <image:loc>{SITE_URL}/views/{esc(name)}</image:loc>
    </image:image>""" for name, _ in drawing_sitemap_images)
    urls.append(f"""  <url>
    <loc>{SITE_URL}/drawing.html</loc>
    <lastmod>{today}</lastmod>{image_xml}
  </url>""")
    video_xml = ''
    for v, poster in video_sitemap_entries:
        extra = ''
        if v['meta'].get('duration_iso'):
            m = re.match(r'PT(\d+)M(\d+)S', v['meta']['duration_iso'])
            extra += f"\n      <video:duration>{int(m.group(1)) * 60 + int(m.group(2))}</video:duration>"
        if v['meta'].get('premiere'):
            extra += f"\n      <video:publication_date>{v['meta']['premiere'].isoformat()}</video:publication_date>"
        video_xml += f"""
    <video:video>
      <video:thumbnail_loc>{SITE_URL}/views/{esc(poster['name'])}</video:thumbnail_loc>
      <video:title>{esc(v['title'])}</video:title>
      <video:description>{esc(truncate(prose(v['description']), 2000))}</video:description>
      <video:content_loc>{SITE_URL}/content/{esc(v['video'])}</video:content_loc>{extra}
    </video:video>"""
    urls.append(f"""  <url>
    <loc>{SITE_URL}/video.html</loc>
    <lastmod>{today}</lastmod>{video_xml}
  </url>""")
    portrait_xml = f"""
    <image:image>
      <image:loc>{SITE_URL}/views/{esc(portrait['name'])}</image:loc>
    </image:image>""" if portrait else ''
    urls.append(f"""  <url>
    <loc>{SITE_URL}/about.html</loc>
    <lastmod>{today}</lastmod>{portrait_xml}
  </url>""")
    for page in text_pages:
        urls.append(f"""  <url>
    <loc>{SITE_URL}/{page}</loc>
    <lastmod>{today}</lastmod>
  </url>""")
    sitemap = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n'
               '        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1"\n'
               '        xmlns:video="http://www.google.com/schemas/sitemap-video/1.1">\n'
               + '\n'.join(urls) + '\n</urlset>\n')
    (base_dir / 'sitemap.xml').write_text(sitemap, encoding='utf-8')
    print("✓ Wrote sitemap.xml")

    images.finish(keep=html_outputs)
    print("\n✓ All pages updated successfully!")


if __name__ == "__main__":
    generate_site(Path("."))
