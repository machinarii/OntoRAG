"""Run the referenced converter CLI in an isolated Python process.

Upstream has no figure-DPI CLI option. Adapt that one default in this process
only; never modify the checkout or share mutable converter state across jobs.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def main() -> None:
    root = Path(sys.argv[1])
    dpi = int(sys.argv[2])
    script = root / "pdf2md_all.py"
    sys.path.insert(0, str(root))  # upstream's sibling structured.py
    spec = importlib.util.spec_from_file_location("pdf2md_all", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load pdf2md from {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    render = module.render_region
    defaults = render.__defaults__
    if not defaults:
        raise RuntimeError("Unsupported pdf2md render_region signature")
    render.__defaults__ = (dpi, *defaults[1:])
    sys.argv = [str(script), *sys.argv[3:]]
    module.main()


if __name__ == "__main__":
    main()
