from __future__ import annotations

import pandas as pd

from src.etf_utils import diversify_etf_frame, infer_etf_theme


def test_infer_etf_theme_groups_similar_names() -> None:
    assert infer_etf_theme("半导体ETF") == "半导体/芯片"
    assert infer_etf_theme("芯片ETF基金") == "半导体/芯片"
    assert infer_etf_theme("证券ETF") == "金融地产"


def test_diversify_etf_frame_limits_same_theme_first() -> None:
    frame = pd.DataFrame(
        [
            {"symbol": "1", "name": "半导体ETF", "score": 0.90},
            {"symbol": "2", "name": "芯片ETF", "score": 0.89},
            {"symbol": "3", "name": "半导体设备ETF", "score": 0.88},
            {"symbol": "4", "name": "证券ETF", "score": 0.70},
            {"symbol": "5", "name": "人工智能ETF", "score": 0.60},
        ]
    )

    result = diversify_etf_frame(frame, limit=4, max_per_theme=2)

    assert result["symbol"].tolist() == ["1", "2", "4", "5"]
    assert (result["theme"] == "半导体/芯片").sum() == 2
