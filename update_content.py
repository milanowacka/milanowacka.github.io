#!/usr/bin/env python3
import re
from pathlib import Path
from PIL import Image

CONTENT_DIR = Path("content")
VIEWS_DIR = Path("views")
THUMBNAIL_WIDTH = 1600

VIEWS_DIR.mkdir(exist_ok=True)

# Read the markdown file
md_file = Path("content/page-content.md")
content = md_file.read_text()

# Extract About section
about_match = re.search(r'# About\n(.*?)(?=\n# Drawing|\Z)', content, re.DOTALL)
about_raw = about_match.group(1).strip() if about_match else ""

# Check for an optional image at the start of the About section
about_image_match = re.match(r'!\[\]\(([^)]+)\)\s*\n', about_raw)
about_image_html = ""
if about_image_match:
    about_image_file = about_image_match.group(1)
    about_image_html = f'<img class="about-image" src="content/{about_image_file}" alt="">'
    about_raw = about_raw[about_image_match.end():]

about_text = about_raw.strip()
about_text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', about_text)
about_text = '<br>'.join(about_text.split('\n'))

# Extract Drawing section
drawing_match = re.search(r'# Drawing\n(.*?)(?=\n# Video|\Z)', content, re.DOTALL)
drawing_content = drawing_match.group(1) if drawing_match else ""

# Extract Video section
video_match = re.search(r'# Video\n(.*)', content, re.DOTALL)
video_content = video_match.group(1) if video_match else ""

# Parse drawings (empty alt text: ![](...))
drawings = []
drawing_pattern = r'## (.*?)\n!\[\]\((.*?)\)\n### Description\s*\n(.*?)(?=\n## |\Z)'
for match in re.finditer(drawing_pattern, drawing_content, re.DOTALL):
    title = match.group(1).strip()
    image = match.group(2).strip()
    description = match.group(3).strip()
    description = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', description)
    description = '<br>'.join(description.split('\n'))
    drawings.append({'title': title, 'image': image, 'description': description})

# Parse videos (alt text carries video filename: ![video.mp4](thumbnail.jpg))
videos = []
video_pattern = r'## (.*?)\n!\[([^\]]+)\]\(([^)]+)\)\n### Description\s*\n(.*?)(?=\n## |\Z)'
for match in re.finditer(video_pattern, video_content, re.DOTALL):
    title = match.group(1).strip()
    video_filename = match.group(2).strip()
    thumbnail = match.group(3).strip()
    description = match.group(4).strip()
    description = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', description)
    description = '<br>'.join(description.split('\n'))
    videos.append({
        'title': title,
        'video': video_filename,
        'thumbnail': thumbnail,
        'description': description,
    })


def make_thumbnail(image_filename):
    src = CONTENT_DIR / image_filename
    p = Path(image_filename)
    thumb_filename = f"{p.stem}_thumbnail{p.suffix}"
    dst = VIEWS_DIR / thumb_filename
    with Image.open(src) as img:
        orig_w, orig_h = img.size
        new_h = round(orig_h * THUMBNAIL_WIDTH / orig_w)
        thumb = img.resize((THUMBNAIL_WIDTH, new_h), Image.LANCZOS)
        thumb.save(dst)
    return thumb_filename


def make_preview_html(image_filename, title, drawing_index):
    p = Path(image_filename)
    preview_filename = f"{p.stem}_preview.html"
    preview_path = VIEWS_DIR / preview_filename
    image_src = f"../content/{image_filename}"
    back_href = f"../drawing.html#drawing{drawing_index}"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <link rel="stylesheet" href="../style.css">
</head>
<body class="preview-page">
    <header>
        <nav>
            <h1 id="logo">{title}</h1>
            <ul>
                <li><a href="{back_href}">← drawing</a></li>
            </ul>
        </nav>
    </header>
    <div class="img-wrapper">
        <img class="preview-image" src="{image_src}" alt="{title}" loading="lazy">
    </div>
</body>
</html>
"""
    preview_path.write_text(html)
    return preview_filename


def make_video_preview_html(thumbnail_filename, video_filename, title, video_index):
    video_p = Path(video_filename)
    preview_filename = f"{video_p.stem}_preview.html"
    preview_path = VIEWS_DIR / preview_filename
    video_src = f"../content/{video_filename}"
    back_href = f"../video.html#video{video_index}"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <link rel="stylesheet" href="../style.css">
</head>
<body class="preview-page">
    <header>
        <nav>
            <h1 id="logo">{title}</h1>
            <ul>
                <li><a href="{back_href}">← video</a></li>
            </ul>
        </nav>
    </header>
    <div class="video-wrapper">
        <video class="preview-video" src="{video_src}" autoplay controls></video>
    </div>
</body>
</html>
"""
    preview_path.write_text(html)
    return preview_filename


# Update about.html
about_html_file = Path("about.html")
about_html = about_html_file.read_text()
if about_image_html:
    about_body = f'<div class="about-content">\n\t\t\t\t{about_image_html}\n\t\t\t\t<p>{about_text}</p>\n\t\t\t</div>'
else:
    about_body = f'<p>{about_text}</p>'

about_html = re.sub(
    r'<section id="about">.*?</section>',
    f'<section id="about">\n\t\t\t{about_body}\n\t\t</section>',
    about_html,
    flags=re.DOTALL
)
about_html_file.write_text(about_html)
print(f"✓ Updated about.html with {len(about_text)} characters from About section")

# Update drawing.html
drawing_html_file = Path("drawing.html")
drawing_html = drawing_html_file.read_text()

carousel_drawings = ''
for i, drawing in enumerate(drawings, 1):
    thumb_filename = make_thumbnail(drawing['image'])
    preview_filename = make_preview_html(drawing['image'], drawing['title'], i)
    print(f"  ✓ {drawing['image']} → thumbnail + preview page")

    thumb_url = f"views/{thumb_filename}"
    preview_url = f"views/{preview_filename}"

    carousel_drawings += f'''<div id="drawing{i}" class="project">
\t\t\t\t\t<a href="{preview_url}" target="_self">
\t\t\t\t\t\t<div class="img-wrapper">
\t\t\t\t\t\t\t<img src="{thumb_url}" alt="{drawing['title']}" loading="lazy">
\t\t\t\t\t\t</div>
\t\t\t\t\t</a>
\t\t\t\t\t<h2>{drawing['title']}</h2>
\t\t\t\t\t<p>{drawing['description']}</p>
\t\t\t\t</div>
\t\t\t\t'''

drawings_section = f'''\t\t\t\t<div class="carousel-wrapper">
\t\t\t\t<button class="carousel-nav prev" aria-label="Previous">&#8592;</button>
\t\t\t\t<button class="carousel-nav next" aria-label="Next">&#8594;</button>
\t\t\t\t<div class="carousel">
\t\t\t\t{carousel_drawings}</div>
\t\t\t\t</div>'''

drawing_html = re.sub(
    r'<section id="drawing">.*?</section>',
    f'<section id="drawing">\n\t\t\t\t{drawings_section}\n\t\t</section>',
    drawing_html,
    flags=re.DOTALL
)
drawing_html_file.write_text(drawing_html)
print(f"✓ Updated drawing.html with {len(drawings)} drawings")

# Update video.html
video_html_file = Path("video.html")
video_html = video_html_file.read_text()

carousel_videos = ''
for i, video in enumerate(videos, 1):
    thumb_filename = make_thumbnail(video['thumbnail'])
    preview_filename = make_video_preview_html(video['thumbnail'], video['video'], video['title'], i)
    print(f"  ✓ {video['thumbnail']} → thumbnail + video preview page")

    thumb_url = f"views/{thumb_filename}"
    preview_url = f"views/{preview_filename}"

    carousel_videos += f'''<div id="video{i}" class="project">
\t\t\t\t\t<a href="{preview_url}" target="_self">
\t\t\t\t\t\t<div class="img-wrapper">
\t\t\t\t\t\t\t<img src="{thumb_url}" alt="{video['title']}" loading="lazy">
\t\t\t\t\t\t</div>
\t\t\t\t\t</a>
\t\t\t\t\t<h2>{video['title']}</h2>
\t\t\t\t\t<p>{video['description']}</p>
\t\t\t\t</div>
\t\t\t\t'''

video_section = f'''\t\t\t\t<div class="carousel-wrapper">
\t\t\t\t<button class="carousel-nav prev" aria-label="Previous">&#8592;</button>
\t\t\t\t<button class="carousel-nav next" aria-label="Next">&#8594;</button>
\t\t\t\t<div class="carousel">
\t\t\t\t{carousel_videos}</div>
\t\t\t\t</div>'''

video_html = re.sub(
    r'<section id="video">.*?</section>',
    f'<section id="video">\n\t\t\t\t{video_section}\n\t\t</section>',
    video_html,
    flags=re.DOTALL
)
video_html_file.write_text(video_html)
print(f"✓ Updated video.html with {len(videos)} videos")

print("\n✓ All pages updated successfully!")
