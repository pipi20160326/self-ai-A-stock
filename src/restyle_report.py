from __future__ import annotations

import argparse
from pathlib import Path

from bs4 import BeautifulSoup


STYLE = """
    .stance-strong td { background: #fee2e2; }
    .stance-strong td:nth-child(5), .stance-strong td:nth-child(8) { color: #991b1b; font-weight: 800; }
    .stance-moderate td { background: #fff7ed; }
    .stance-moderate td:nth-child(5), .stance-moderate td:nth-child(8) { color: #9a3412; font-weight: 700; }
    .stance-watch td { background: #f8fafc; color: #475569; }
"""


def _score(text: str) -> float:
    try:
        return float(text)
    except Exception:
        return 0.0


def _stance(signal: str, score: float) -> tuple[str, str]:
    if signal == "买入" and score >= 0.6:
        return "强势看涨", "stance-strong"
    if signal == "买入" and score >= 0.2:
        return "一般看涨", "stance-moderate"
    return "观察", "stance-watch"


def _insert_style(soup: BeautifulSoup) -> None:
    style = soup.find("style")
    if style and "stance-strong" not in style.text:
        style.string = (style.string or "") + "\n" + STYLE


def _restyle_table(soup: BeautifulSoup, title: str, signal_index: int, score_index: int, insert_index: int) -> None:
    heading = None
    for h2 in soup.find_all("h2"):
        if h2.get_text(strip=True) == title:
            heading = h2
            break
    if heading is None:
        return
    table = heading.find_next("table")
    if table is None:
        return
    header = table.find("thead").find("tr")
    headers = [th.get_text(strip=True) for th in header.find_all("th")]
    if "观点" not in headers:
        th = soup.new_tag("th")
        th.string = "观点"
        header.insert(insert_index, th)
    for tr in table.find("tbody").find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) <= max(signal_index, score_index):
            continue
        if any(td.get_text(strip=True) in {"强势看涨", "看涨", "一般看涨", "观察"} for td in cells):
            continue
        stance, cls = _stance(cells[signal_index].get_text(strip=True), _score(cells[score_index].get_text(strip=True)))
        td = soup.new_tag("td")
        td.string = stance
        tr.insert(insert_index, td)
        tr["class"] = cls


def restyle_report(path: Path) -> Path:
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    _insert_style(soup)
    _restyle_table(soup, "板块内候选个股", signal_index=4, score_index=6, insert_index=4)
    _restyle_table(soup, "ETF 候选", signal_index=3, score_index=6, insert_index=3)
    path.write_text(str(soup), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="给已生成的 HTML 报告增加观点分层和颜色标记")
    parser.add_argument("path")
    args = parser.parse_args()
    print(restyle_report(Path(args.path)))


if __name__ == "__main__":
    main()
