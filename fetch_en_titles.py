"""
AniList GraphQL バッチクエリで英語タイトルを取得
10件ずつまとめてリクエストし、レート制限を回避
"""
import json, time, urllib.request
from pathlib import Path

GRAPHQL_URL = "https://graphql.anilist.co"
BATCH = 10   # 1リクエストあたりのクエリ数
DELAY = 2.0  # リクエスト間隔(秒)

def build_batch_query(items):
    """複数タイトルをGQLエイリアスでまとめる"""
    parts = []
    for alias, title in items:
        escaped = title.replace('"', '\\"')
        parts.append(
            f'{alias}: Media(search: "{escaped}", type: ANIME) '
            f'{{ title {{ english romaji }} }}'
        )
    return "query { " + "\n".join(parts) + " }"

def fetch_batch(items):
    """items: [(alias, title), ...] → {alias: {english, romaji}}"""
    query = build_batch_query(items)
    payload = json.dumps({"query": query}).encode()
    req = urllib.request.Request(
        GRAPHQL_URL, data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            if "errors" in data:
                # 429など
                print(f"  API error: {data['errors'][0]['message']}")
                return {}
            return data.get("data") or {}
    except Exception as e:
        print(f"  Request error: {e}")
        return {}

def main():
    anime_path = Path("data/anime.json")
    out_path   = Path("data/en_titles.json")

    with open(anime_path) as f:
        anime_list = json.load(f)

    # 既存キャッシュ読み込み（中断再開）
    if out_path.exists():
        with open(out_path) as f:
            en_titles = json.load(f)
    else:
        en_titles = {}

    # 英語タイトルが取得できていないものだけ再取得
    missing = [a for a in anime_list if not en_titles.get(str(a["id"]))]
    print(f"全 {len(anime_list)} 作品 / 未取得 {len(missing)} 作品")
    print(f"バッチサイズ {BATCH} / 推定リクエスト数 {len(missing)//BATCH+1}\n")

    # バッチ処理
    for i in range(0, len(missing), BATCH):
        batch = missing[i:i+BATCH]
        items = [(f"a{a['id']}", a.get("ja") or a.get("cn") or "") for a in batch]

        result = fetch_batch(items)

        for a, (alias, _) in zip(batch, items):
            aid = str(a["id"])
            media = result.get(alias)
            if media and media.get("title"):
                t = media["title"]
                en = t.get("english") or t.get("romaji") or ""
            else:
                en = ""
            en_titles[aid] = en
            status = "✓" if en else "✗"
            ja = a.get("ja","")[:25]
            print(f"  {status} [{aid}] {ja:25} → {en[:35]}")

        # バッチごとに保存
        with open(out_path, "w") as f:
            json.dump(en_titles, f, ensure_ascii=False)

        done = min(i + BATCH, len(missing))
        print(f"── {done}/{len(missing)} 完了 ──\n")
        time.sleep(DELAY)

    found = sum(1 for v in en_titles.values() if v)
    print(f"完了: {found}/{len(en_titles)} 作品に英語タイトルあり")

if __name__ == "__main__":
    main()
