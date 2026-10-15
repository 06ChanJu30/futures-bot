"""거래소에서 과거 시세와 펀딩비를 받아 data/ 폴더에 저장 (API 키 불필요).

  python fetch_data.py
"""
import yaml

from fbot import data

if __name__ == "__main__":
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    d = cfg["data"]
    print(f"{d['exchange']} {d['symbol']} {d['timeframe']} 최근 {d['years']}년치 다운로드 시작")
    data.fetch(cfg)
