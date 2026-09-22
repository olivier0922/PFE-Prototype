from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data import DataValidationError, validate_sources  # noqa: E402


def main() -> int:
    try:
        summary = validate_sources()
    except DataValidationError as error:
        print(f"Erreur de validation: {error}", file=sys.stderr)
        return 1
    print("Données prêtes:")
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

