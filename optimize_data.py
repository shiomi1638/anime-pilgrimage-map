"""
points.geojsonの最適化:
1. imageが空のポイントを除去
2. 座標を小数点6桁に圧縮（約10cm精度で十分）
3. gzip圧縮
"""
import json
import gzip
import os
from pathlib import Path

DATA_DIR = Path("data")
INPUT = DATA_DIR / "points.geojson"
OUTPUT = DATA_DIR / "points_optimized.geojson"
OUTPUT_GZ = DATA_DIR / "points_optimized.geojson.gz"

def optimize():
    with open(INPUT) as f:
        data = json.load(f)
    
    original_count = len(data["features"])
    print(f"元のポイント数: {original_count:,}")
    print(f"元のファイルサイズ: {INPUT.stat().st_size / 1024:.0f} KB")
    
    # imageがあるポイントのみ保持
    features = [f for f in data["features"] if f["properties"].get("image")]
    
    # 座標を圧縮（小数点6桁）
    for f in features:
        coords = f["geometry"]["coordinates"]
        f["geometry"]["coordinates"] = [round(coords[0], 6), round(coords[1], 6)]
    
    data["features"] = features
    
    removed = original_count - len(features)
    print(f"削除したポイント(imageなし): {removed:,}")
    print(f"最適化後のポイント数: {len(features):,}")
    
    # JSON出力
    json_str = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    
    # 通常ファイル
    OUTPUT.write_text(json_str)
    print(f"最適化後ファイルサイズ: {OUTPUT.stat().st_size / 1024:.0f} KB")
    
    # gzip圧縮
    with gzip.open(OUTPUT_GZ, "wt", encoding="utf-8") as f:
        f.write(json_str)
    print(f"gzip圧縮後ファイルサイズ: {OUTPUT_GZ.stat().st_size / 1024:.0f} KB")
    
    # 削減率
    orig_size = INPUT.stat().st_size
    gz_size = OUTPUT_GZ.stat().st_size
    reduction = (1 - gz_size / orig_size) * 100
    print(f"\n削減率: {reduction:.0f}%")

if __name__ == "__main__":
    optimize()
