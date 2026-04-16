"""
キャッシュプロキシ付きHTTPサーバー + JSON API
- 静的ファイルを通常通り配信
- /img-cache/* → ローカルキャッシュ確認 → なければ image.anitabi.cn から取得してキャッシュ
- /api/*       → アニメ・ポイントデータをJSONで返す

API エンドポイント:
  GET /api/anime              全アニメ一覧（?q=検索ワード&limit=N）
  GET /api/anime/{id}         アニメ詳細 + 全ポイント
  GET /api/points/nearby      近くの聖地（?lat=&lng=&radius=km&limit=N）
  GET /api/search?q=          アニメ名で検索
"""
import json, os, time, threading, urllib.request, math
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

CACHE_DIR   = Path("data/img_cache")
ANITABI_IMG = "https://image.anitabi.cn"
PORT        = 8767

# ── データを起動時に読み込む ──────────────────────────────────────────
_anime_list   = []   # list of anime dicts
_anime_index  = {}   # id → anime dict
_points       = []   # all GeoJSON features
_en_titles    = {}   # id → english title

def _load_data():
    global _anime_list, _anime_index, _points, _en_titles
    try:
        with open("data/anime.json") as f:
            _anime_list = json.load(f)
        _anime_index = {a["id"]: a for a in _anime_list}
    except Exception as e:
        print(f"[api] anime.json 読み込みエラー: {e}")

    try:
        with open("data/points.geojson") as f:
            _points = json.load(f)["features"]
    except Exception as e:
        print(f"[api] points.geojson 読み込みエラー: {e}")

    try:
        with open("data/en_titles.json") as f:
            _en_titles = json.load(f)
    except Exception:
        pass

    print(f"[api] データ読み込み完了: {len(_anime_list)} 作品 / {len(_points)} ポイント")

# ── レート制限 ────────────────────────────────────────────────────────
_fetch_sem  = threading.Semaphore(10)
_fetch_lock = threading.Lock()
_last_fetch = 0.0
FETCH_INTERVAL = 0.05


def fetch_and_cache(remote_url: str, cache_path: Path):
    global _last_fetch
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    with _fetch_sem:
        with _fetch_lock:
            wait = FETCH_INTERVAL - (time.time() - _last_fetch)
            if wait > 0:
                time.sleep(wait)
            _last_fetch = time.time()

        try:
            req = urllib.request.Request(
                remote_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                    "Referer": "https://anitabi.cn/",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            cache_path.write_bytes(data)
            return data
        except Exception as e:
            print(f"[proxy] fetch error: {remote_url} → {e}")
            return None


# ── ユーティリティ ────────────────────────────────────────────────────
def _haversine_km(lat1, lng1, lat2, lng2):
    """2点間の距離（km）"""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def _anime_with_en(a):
    """英語タイトルを付加したアニメdictを返す"""
    d = dict(a)
    d["en"] = _en_titles.get(str(a["id"]), "")
    return d


# ── ハンドラ ──────────────────────────────────────────────────────────
class Handler(SimpleHTTPRequestHandler):

    def do_GET(self):
        if self.path.startswith("/api/"):
            self.handle_api()
        elif self.path.startswith("/img-cache/"):
            self.handle_img_cache()
        else:
            super().do_GET()

    # ── JSON API ───────────────────────────────────────────────────────
    def handle_api(self):
        parsed = urlparse(self.path)
        qs     = parse_qs(parsed.query)
        path   = parsed.path.rstrip("/")

        try:
            if path == "/api/anime":
                self._api_anime_list(qs)
            elif path.startswith("/api/anime/"):
                aid = path[len("/api/anime/"):]
                self._api_anime_detail(aid)
            elif path == "/api/points/nearby":
                self._api_nearby(qs)
            elif path == "/api/search":
                self._api_search(qs)
            else:
                self._json(404, {"error": "Not found"})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def _api_anime_list(self, qs):
        q     = qs.get("q", [""])[0].lower()
        limit = int(qs.get("limit", [200])[0])
        result = []
        for a in _anime_list:
            if q and not (
                q in (a.get("ja") or "").lower() or
                q in (a.get("cn") or "").lower() or
                q in _en_titles.get(str(a["id"]), "").lower()
            ):
                continue
            result.append(_anime_with_en(a))
            if len(result) >= limit:
                break
        self._json(200, {"count": len(result), "anime": result})

    def _api_anime_detail(self, aid_str):
        try:
            aid = int(aid_str)
        except ValueError:
            self._json(400, {"error": "Invalid id"})
            return
        anime = _anime_index.get(aid)
        if not anime:
            self._json(404, {"error": "Anime not found"})
            return
        points = [f for f in _points if f["properties"].get("aid") == aid]
        self._json(200, {
            "anime": _anime_with_en(anime),
            "pointCount": len(points),
            "points": points,
        })

    def _api_nearby(self, qs):
        try:
            lat    = float(qs["lat"][0])
            lng    = float(qs["lng"][0])
            radius = float(qs.get("radius", [5])[0])
            limit  = int(qs.get("limit", [50])[0])
        except (KeyError, ValueError):
            self._json(400, {"error": "lat, lng が必要です"})
            return

        results = []
        for f in _points:
            coords = f["geometry"]["coordinates"]  # [lng, lat]
            dist = _haversine_km(lat, lng, coords[1], coords[0])
            if dist <= radius:
                item = dict(f["properties"])
                item["distanceKm"] = round(dist, 3)
                item["coordinates"] = coords
                anime = _anime_index.get(item.get("aid"))
                if anime:
                    item["animeName"] = _en_titles.get(str(anime["id"])) or anime.get("ja") or anime.get("cn")
                results.append(item)
        results.sort(key=lambda x: x["distanceKm"])
        self._json(200, {"count": len(results[:limit]), "points": results[:limit]})

    def _api_search(self, qs):
        self._api_anime_list(qs)  # /api/anime?q= と同じ

    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    # ── 画像プロキシ ───────────────────────────────────────────────────
    def handle_img_cache(self):
        rel      = unquote(self.path[len("/img-cache/"):])
        rel_no_q = rel.split("?")[0]
        cache_path = CACHE_DIR / rel_no_q

        if cache_path.exists():
            data = cache_path.read_bytes()
        else:
            qs = ("?" + rel.split("?", 1)[1]) if "?" in rel else ""
            data = fetch_and_cache(f"{ANITABI_IMG}/{rel_no_q}{qs}", cache_path)

        if data is None:
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        if "/img-cache/" in (args[0] if args else ""):
            return
        super().log_message(fmt, *args)


class ThreadingServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    os.chdir(Path(__file__).parent)
    _load_data()
    server = ThreadingServer(("", PORT), Handler)
    print(f"Serving on http://localhost:{PORT}")
    print()
    print("API エンドポイント:")
    print(f"  GET http://localhost:{PORT}/api/anime              # 全作品一覧")
    print(f"  GET http://localhost:{PORT}/api/anime/{{id}}         # 作品詳細＋ポイント")
    print(f"  GET http://localhost:{PORT}/api/anime?q=ゆるキャン  # 検索")
    print(f"  GET http://localhost:{PORT}/api/points/nearby?lat=35.6&lng=138.5&radius=10  # 近くの聖地")
    server.serve_forever()
