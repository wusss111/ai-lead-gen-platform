"""生成 examples/demo_input.xlsx，供本地冒烟演示。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    out_dir = ROOT / "examples"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "demo_input.xlsx"
    df = pd.DataFrame(
        [
            {
                "company_name": "Demo Tools GmbH",
                "website": "",
                "country_region": "DE",
                "target_products": "digital multimeter",
                "priority": "中",
                "notes": "演示行：仅粘贴证据",
                "evidence_paste": "Demo company sells industrial test equipment including multimeters and distributors in EU.",
            }
        ]
    )
    df.to_excel(path, index=False, engine="openpyxl")
    print(path)


if __name__ == "__main__":
    main()
