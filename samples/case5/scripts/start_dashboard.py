"""Start the Case 5 dashboard from the project root without package syntax."""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from time_frequency_dashboard.main import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
