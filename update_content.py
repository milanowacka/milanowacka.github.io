#!/usr/bin/env python3
"""CI entry point: regenerates the whole site from content/page-content.md.

All logic lives in site_generator.py (shared with the desktop content-manager
app). Run from the repository root:  python update_content.py
"""
from pathlib import Path

from site_generator import generate_site

if __name__ == "__main__":
    generate_site(Path(__file__).resolve().parent)
