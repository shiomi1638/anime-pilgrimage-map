"""
全ポイント画像をローカルにプリキャッシュ
実行: python3 prefetch_images.py
"""
import json, time, threading
import urllib.request
from pathlib import Path

CACHE_DIR = Path("data/img_cache")
BASE = "https://image.anitabi.cn"
CONCURRENCY = 20
INTERVAL = 0.02

with open("data/points.geojson") as f:
    features = json.load(f)["features"]

# 重複排除
seen, images = set(), []
for feat in features:
    img = feat["properties"].get("image", "")
    if not img:
        continue
    path = img.replace(BASE + "/", "").split("?")[0]
    if path in seen:
        continue
    seen.add(path)
    images.append(path)

already = sum(1 for p in images if (CACHE_DIR / p).exists())
todo = [p for p in images if not (CACHE_DIR / p).exists()]
print(f"合計 {len(images)} 枚 / キャッシュ済み {already} 枚 / 残り {len(todo)} 枚")
if not todo:
    print("すべてキャッシュ済みです")
    exit()

sem = threading.Semaphore(CONCURRENCY)
lock = threading.Lock()
last_fetch = [0.0]
done = [0]

def fetch(path):
    cache = CACHE_DIR / path
    cache.parent.mkdir(parents=True, exist_ok=True)
    url = f"{BASE}/{path}?plan=h360"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                     "Referer": "https://anitabi.cn/"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            cache.write_bytes(r.read())
    except Exception as e:
        pass
    finally:
        with lock:
            done[0] += 1
            n = done[0]
        if n % 50 == 0 or n == len(todo):
            print(f"  {n}/{len(todo)} 完了")
        sem.release()

threads = []
for path in todo:
    sem.acquire()
    with lock:
        wait = INTERVAL - (time.time() - last_fetch[0])
        if wait > 0:
            time.sleep(wait)
        last_fetch[0] = time.time()
    t = threading.Thread(target=fetch, args=(path,), daemon=True)
    t.start()
    threads.append(t)

for t in threads:
    t.join()

print("完了")
