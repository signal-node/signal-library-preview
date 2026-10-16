"""clone直後にも依存導入なしで動く共通CLI入口。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from signal_library.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
