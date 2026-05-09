#!/usr/bin/env python3
import re
from pathlib import Path
from PIL import Image

CONTENT_DIR = Path("content")
VIEWS_DIR = Path("views")
THUMBNAIL_WIDTH = 1000

VIEWS_DIR.mkdir(exist_ok=True)

# Read the markdown file
md_file = Path("content/page-content.md")
content = md_file.read_text()

# Extract About section
about_match = re.search(r'# About\n(.*?)(?=\n# Projects|\Z)', content, re.DOTALL)
about_text = about_match.group(1).strip() if about_match else ""
# Convert Markdown links to HTML
about_text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', about_text)
# Add <br> for line breaks
about_text = '<br>'.join(about_text.split('\n'))

# Extract Projects section
projects_match = re.search(r'# Projects\n(.*)', content, re.DOTALL)
projects_content = projects_match.group(1) if projects_match else ""

# Parse individual projects
projects = []
project_pattern = r'## (.*?)\n!\[\]\((.*?)\)\n### Description\s*\n(.*?)(?=\n## |\Z)'
for match in re.finditer(project_pattern, projects_content, re.DOTALL):
    title = match.group(1).strip()
    image = match.group(2)
    description = match.group(3).strip()
    # Convert Markdown links to HTML
    description = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', description)
    # Add <br> for line breaks
    description = '<br>'.join(description.split('\n'))
    projects.append({
        'title': title,
        'image': image,
        'description': description
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


def make_preview_html(image_filename, title):
    p = Path(image_filename)
    preview_filename = f"{p.stem}_preview.html"
    preview_path = VIEWS_DIR / preview_filename
    image_src = f"../content/{image_filename}"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <link rel="stylesheet" href="../style.css">
</head>
<body class="preview-page">
    <img class="preview-image" src="{image_src}" alt="{title}">
</body>
</html>
"""
    preview_path.write_text(html)
    return preview_filename


# Update about.html
about_html_file = Path("about.html")
about_html = about_html_file.read_text()
about_html = re.sub(
    r'<section id="about">.*?</section>',
    f'<section id="about">\n\t\t\t<h2>About</h2>\n\t\t\t<p>{about_text}</p>\n\t\t</section>',
    about_html,
    flags=re.DOTALL
)
about_html_file.write_text(about_html)
print(f"✓ Updated about.html with {len(about_text)} characters from About section")

# Update index.html
index_html_file = Path("index.html")
index_html = index_html_file.read_text()

# Generate carousel projects
carousel_projects = ''
for i, project in enumerate(projects, 1):
    thumb_filename = make_thumbnail(project['image'])
    preview_filename = make_preview_html(project['image'], project['title'])
    print(f"  ✓ {project['image']} → thumbnail + preview page")

    thumb_url = f"views/{thumb_filename}"
    preview_url = f"views/{preview_filename}"

    carousel_projects += f'''<div id="project{i}" class="project">
\t\t\t\t\t<a href="{preview_url}" target="_self">
\t\t\t\t\t\t<img src="{thumb_url}" alt="{project['title']}">
\t\t\t\t\t</a>
\t\t\t\t\t<h2>{project['title']}</h2>
\t\t\t\t\t<p>{project['description']}</p>
\t\t\t\t</div>
\t\t\t\t'''

# Replace the entire projects section
projects_section = f'''\t\t\t\t<div class="carousel">
\t\t\t\t{carousel_projects}</div>'''

index_html = re.sub(
    r'<section id="projects">.*?</section>',
    f'<section id="projects">\n\t\t\t\t{projects_section}\n\t\t</section>',
    index_html,
    flags=re.DOTALL
)
index_html_file.write_text(index_html)
print(f"✓ Updated index.html with {len(projects)} projects")

print("\n✓ All pages updated successfully!")
