#!/usr/bin/env python3
from pathlib import Path

from site_generator import generate_site

if __name__ == "__main__":
    generate_site(Path("."))
