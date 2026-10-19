"""GodHand's local task service and MCP interface (no GUI imports)."""
from pathlib import Path
import site
import sys

VERSION = '1.1.0'
if not getattr(sys, 'frozen', False):
    vendor = Path(__file__).parent / 'vendor'
    if vendor.is_dir():
        site.addsitedir(str(vendor))

