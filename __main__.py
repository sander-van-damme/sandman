"""Top-level entry point for PyInstaller.

PyInstaller runs this file as the __main__ script, so relative imports
inside the sandman package would fail if we pointed it at sandman/main.py
directly.  By using an absolute import here the sandman package is loaded
normally and all relative imports inside it resolve correctly.
"""

import sys
from sandman.main import main

if __name__ == "__main__":
    sys.exit(main())
