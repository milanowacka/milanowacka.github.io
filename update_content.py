#!/usr/bin/env python3
import re
from pathlib import Path

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

# Generate carousel projects (no navigation buttons)
carousel_projects = ''
for i, project in enumerate(projects, 1):
    image_url = f"content/{project['image']}"
    carousel_projects += f'''<div id="project{i}" class="project">
\t\t\t\t\t<a href="{image_url}" target="_self">
\t\t\t\t\t\t<img src="{image_url}" alt="{project['title']}">
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
