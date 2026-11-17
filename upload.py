# -*- coding: utf-8 -*-
"""
mixi (mixi.jp) 画像自動投稿（GitHub Actions用）
Google Driveから画像取得 -> ランダム1枚を日記として投稿 -> アップロード済みを記録
Selenium使用（mixi公式APIなし）
"""
from publication_evidence import record_publication_result, identity_from_url
from promotion_policy import CANONICAL_URL, canonical_cta, strip_plain_promotion, normalize_plain_promotion, normalize_html_promotion
import sys
import argparse
import html
import json
import os
import random
import re
import time
from datetime import datetime, timezone, timedelta

from pool_loader import as_insights

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def ensure_live_dependencies():
    global By, WebDriverWait, EC
    global create_driver, navigate_to_diary_editor, human_delay, safe_driver_get

    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from mixi_auth import create_driver, navigate_to_diary_editor, human_delay, safe_driver_get


COOKIE_FILE = os.path.join(os.path.dirname(__file__), "mixi_cookies.json")
RUN_STATUS_FILE = os.path.join(os.path.dirname(__file__), "run_status.json")
EXIT_AUTH_HOLD = 2


def is_auth_redirect(url):
    value = (url or "").lower()
    return "login" in value or "auth" in value or "session" in value


def write_run_status(status, reason="", exit_code=0):
    data = {
        "checked_at_jst": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST"),
        "status": status,
        "reason": reason,
        "exit_code": exit_code,
    }
    tmp = RUN_STATUS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, RUN_STATUS_FILE)


def login_with_cookies(driver):
    """保存済みCookieでログインする（reCAPTCHA回避）"""
    if not os.path.exists(COOKIE_FILE):
        print("Error: Cookie未保存。先に save_cookies.py を実行してください。")
        return False

    with open(COOKIE_FILE, "r") as f:
        cookies = json.load(f)

    # まずmixi.jpにアクセスしてドメインを設定
    if not safe_driver_get(driver, "https://mixi.jp/"):
        print("Error: mixi.jp への接続がタイムアウトしました（ネットワークまたは mixi 側の遅延）")
        return False
    human_delay(2, 3)

    # Cookieを追加
    for cookie in cookies:
        # sameSite属性の修正（Seleniumの互換性問題対策）
        if "sameSite" in cookie and cookie["sameSite"] not in ("Strict", "Lax", "None"):
            cookie["sameSite"] = "None"
        try:
            driver.add_cookie(cookie)
        except Exception:
            pass  # ドメインが違うCookieはスキップ

    # ホームページにアクセスしてログイン確認
    if not safe_driver_get(driver, "https://mixi.jp/home.pl"):
        print("Error: home.pl への接続がタイムアウトしました")
        return False
    human_delay(3, 5)

    current_url = driver.current_url
    if is_auth_redirect(current_url):
        print("Error: Cookieが期限切れです。save_cookies.py を再実行してください。")
        return False

    # ページ内容でログイン確認
    page_source = driver.page_source
    if any(keyword in page_source for keyword in ["ログアウト", "マイページ", "日記を書く", "ホーム"]):
        print("Cookieログイン成功!")
        return True

    # URLだけでは成功扱いしない。期限切れCookieでもトップへ戻る実例があるため、
    # ページ内の認証証拠が無ければfail-closedする。
    print("Error: Cookieログインの確実な証拠がありません。save_cookies.py を再実行してください。")
    return False


JST = timezone(timedelta(hours=9))

# --- 環境変数 ---
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID_MIXI", "")
SAFE_LANE = "safe_fitness"
SAFE_FALLBACK_CTA = (
    "まとめはこちら → "
    "https://musclelove-777.github.io/?utm_source=mixi&utm_medium=autopost"
)
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
UPLOADED_LOG = os.path.join(os.path.dirname(__file__), "uploaded_mixi.json")
DRY_RUN_OUTPUT_HTML = os.environ.get("DRY_RUN_OUTPUT_HTML", "dry_run_mixi_diary.html")
DRY_RUN_OUTPUT_JSON = os.environ.get("DRY_RUN_OUTPUT_JSON", "dry_run_mixi_diary.json")

# --- MuscleLove バックリンクプール（mixi一般プラットフォーム：フィットネス系のみ） ---
ML_BACKLINK_POOL_FITNESS = [
    ("https://musclelove-777.github.io/muscle-meal-girls/", "筋肉女子のマッスルメシ"),
    ("https://musclelove-777.github.io/runners-lab/", "ランナーラボ"),
    ("https://musclelove-777.github.io/armwrestling-girls-navi/", "腕相撲女子ナビ"),
    ("https://musclelove-777.github.io/physique-girls-navi/", "フィジーク女子ナビ"),
    ("https://musclelove-777.github.io/fighting-girls-navi/", "格闘技女子ナビ"),
    ("https://musclelove-777.github.io/joshi-prowrestling-navi/", "女子プロレスナビ"),
    ("https://musclelove-777.github.io/female-physique-queens/", "Female Physique Queens"),
    ("https://musclelove-777.github.io/network/fitness/", "Fitness Network 15サイト"),
    ("https://musclelove-777.github.io/network/academy/", "MuscleLove Academy 77"),
]


def build_backlink_block():
    """MuscleLove バックリンクのプレーンテキストブロック（mixi日記用、ランダム2件）"""
    try:
        k = min(2, len(ML_BACKLINK_POOL_FITNESS))
        selected = random.sample(ML_BACKLINK_POOL_FITNESS, k=k)
        lines = [f"  ・{n} {u}" for u, n in selected]
        return (
            "\n\n"
            "<!-- ML_BACKLINK -->\n"
            "▼ 関連サイト\n" + "\n".join(lines) + "\n"
            "<!-- /ML_BACKLINK -->\n"
        )
    except Exception:
        return ""

# --- タグマッピング（mixi日本語ユーザー向け） ---
CONTENT_TAG_MAP = {
    'training': ['筋トレ', 'ワークアウト', 'トレーニング', 'ジム', 'フィットネス'],
    'workout': ['筋トレ', 'ワークアウト', 'トレーニング', 'ジム', 'フィットネス'],
    'pullups': ['懸垂', 'プルアップ', 'バックワークアウト', 'カリステニクス'],
    'posing': ['ポージング', 'ボディビル', 'フィジーク'],
    'flex': ['フレックス', 'マッスル', 'ボディビル'],
    'muscle': ['筋肉', 'マッスル', 'フィットネス'],
    'bicep': ['上腕二頭筋', 'バイセップ', '腕トレ'],
    'abs': ['腹筋', 'シックスパック', 'コアトレ'],
    'leg': ['脚トレ', 'レッグデイ', 'スクワット'],
    'back': ['背中', 'バックデイ', '広背筋'],
    'squat': ['スクワット', '脚トレ', 'レッグデイ'],
    'deadlift': ['デッドリフト', 'パワーリフティング'],
    'bench': ['ベンチプレス', '胸トレ'],
    'bikini': ['ビキニ', 'ビキニフィットネス', 'フィギュア'],
    'competition': ['大会', 'コンテスト', 'ボディビル'],
}

BASE_TAGS = [
    '筋肉女子', '筋トレ女子', 'マッスルガール', 'フィットネス',
    'ボディメイク', 'ワークアウト', 'ジム', 'トレーニング',
    '筋肉美', 'AIイラスト', 'AIアート',
]

SAFE_FALLBACK_NG = {
    'uncensored', 'sexy', 'nude', 'porn', 'fetish', 'adult', 'nsfw',
    '18禁', 'エロ', 'アダルト', '無修正', '裸', 'フェチ', '腕フェチ',
    'ワキフェチ', 'armpit', 'armpitfetish', 'thicc',
}

CATEGORY_LABELS = {
    'training': 'トレーニング',
    'workout': 'ワークアウト',
    'pullups': '懸垂',
    'posing': 'ポージング',
    'flex': '筋肉美',
    'muscle': '筋肉美',
    'bicep': '腕トレ',
    'abs': '腹筋トレーニング',
    'leg': '脚トレ',
    'back': '背中トレーニング',
    'squat': 'スクワット',
    'deadlift': 'デッドリフト',
    'bench': 'ベンチプレス',
    'competition': 'フィットネス',
}

# 日記タイトルテンプレート（ランダム選択）
TITLE_TEMPLATES = [
    "【{category}】今日の一枚、強い 💪",
    "{category} | バキバキ。でも美しい",
    "この{category}、仕上がりエグい 🔥",
    "筋肉女子の{category}、やっぱ最高",
    "{category}の積み上げが形になった一枚 ✨",
]

# キャプションテンプレート
CAPTION_TEMPLATES = [
    "見てくれ、この圧。",
    "今日の一枚、強い。",
    "これは仕上がりエグい。",
    "筋肉女子、やっぱ最高。",
    "バキバキ。でも美しい。",
]


# ===== Google Drive =====

def list_gdrive_images(folder_id):
    """公開フォルダをgdownで取得する。APIキー経路は持たない。"""
    return _list_via_gdown(folder_id)


def _list_via_gdown(folder_id):
    """gdownでフォルダをダウンロード（APIキー不要）"""
    import gdown
    dl_dir = os.path.join(os.path.dirname(__file__), "images")
    os.makedirs(dl_dir, exist_ok=True)
    url = f"https://drive.google.com/drive/folders/{folder_id}"
    print(f"Downloading from Google Drive: {url}")
    try:
        gdown.download_folder(url, output=dl_dir, quiet=False, remaining_ok=True)
    except Exception as e:
        print(f"Download error: {e}")
        # 一部ファイルが失敗しても、ダウンロード済みファイルを使う

    # gdownダウンロード後にメモリを解放（後続のSelenium起動タイムアウト対策）
    import gc
    gc.collect()

    images = []
    for root, dirs, filenames in os.walk(dl_dir):
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                fpath = os.path.join(root, fname)
                images.append({
                    "id": None,
                    "name": fname,
                    "local_path": fpath,
                })

    if not images:
        print("gdownで画像を取得できませんでした（0ファイル）")
        return []

    return images


def dump_debug_artifacts(driver, prefix="mixi_debug"):
    """失敗解析用にスクリーンショットとHTMLを保存する"""
    ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    screenshot = f"{prefix}_{ts}.png"
    html_file = f"{prefix}_{ts}.html"
    try:
        driver.save_screenshot(screenshot)
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print(f"Debug artifacts saved: {screenshot}, {html_file}")
    except Exception as e:
        print(f"Debug artifact save failed: {e}")


# ===== タグ・テキスト生成 =====

def _contains_blocked_term(value, blocked_terms):
    normalized = str(value).casefold()
    return any(term.casefold() in normalized for term in blocked_terms if term)


def generate_tags(image_name, insights=None):
    """中央プールを優先し、Safe Fitness禁止語を除外してタグを生成する。"""
    insights = insights or {}
    blocked_terms = set(SAFE_FALLBACK_NG)
    blocked_terms.update(str(v).strip() for v in insights.get("avoid_tags", []) if str(v).strip())
    tags = list(insights.get("recommended_tags") or BASE_TAGS)
    name_lower = image_name.lower().replace('-', ' ').replace('_', ' ')
    matched = set()
    for keyword, keyword_tags in CONTENT_TAG_MAP.items():
        if keyword in name_lower:
            for t in keyword_tags:
                if t not in matched:
                    tags.append(t)
                    matched.add(t)
    # 重複除去
    seen = set()
    unique_tags = []
    for t in tags:
        tag = str(t).strip().lstrip('#')
        key = tag.casefold()
        if tag and key not in seen and not _contains_blocked_term(tag, blocked_terms):
            seen.add(key)
            unique_tags.append(tag)
    return unique_tags[:15]


def extract_category(image_name):
    """既知のSafe Fitness語だけからカテゴリを返し、IDや固有名詞を漏らさない。"""
    normalized = image_name.casefold().replace('-', ' ').replace('_', ' ')
    for keyword, label in CATEGORY_LABELS.items():
        if keyword in normalized:
            return label
    return "筋肉美"


def build_title(image_name):
    """日記タイトルを生成"""
    category = extract_category(image_name)
    template = random.choice(TITLE_TEMPLATES)
    return template.format(category=category)


def build_body_text(image_name, tags, insights=None):
    """中央プールの短文・CTAからSafe Fitness日記本文を生成する。"""
    insights = insights or {}
    hashtags = ' '.join([f'#{t}' for t in tags[:15]])
    templates = list(insights.get("recommended_templates") or CAPTION_TEMPLATES)
    template = random.choice(templates)
    try:
        caption = template.format(hashtags=hashtags).strip()
    except (KeyError, ValueError):
        caption = random.choice(CAPTION_TEMPLATES)
    if "{hashtags}" not in template and hashtags:
        caption = f"{caption}\n\n{hashtags}"

    ctas = [str(v).strip() for v in insights.get("recommended_ctas", []) if str(v).strip()]
    measurable = [value for value in ctas if "utm_source=mixi" in value]
    cta = random.choice(measurable or ctas or [SAFE_FALLBACK_CTA])
    return normalize_plain_promotion(f"{caption}\n\n{cta}")


def validate_public_payload(title, body_text, tags, insights=None):
    """Safe Fitness禁止語やファイル由来IDを公開直前にfail-closedで拒否する。"""
    insights = insights or {}
    blocked_terms = set(SAFE_FALLBACK_NG)
    blocked_terms.update(str(v).strip() for v in insights.get("avoid_tags", []) if str(v).strip())
    combined = "\n".join([title, body_text, " ".join(tags)])
    hits = sorted(term for term in blocked_terms if _contains_blocked_term(combined, [term]))
    if hits:
        raise ValueError(f"Safe Fitness blocked terms: {', '.join(hits)}")
    if re.search(r"(?i)(?:^|[^0-9a-f])[0-9a-f]{8,}(?:[^0-9a-f]|$)", title):
        raise ValueError("Hash-like source identifier in public title")


# ===== Selenium 日記投稿 =====


def post_diary_entry(driver, title, body_text, image_path, tags):
    """
    mixi日記を投稿する（確認画面対応の確定フロー）

    Args:
        driver: ログイン済み・日記作成ページのWebDriver
        title: 日記タイトル
        body_text: 日記本文（プレーンテキスト）
        image_path: アップロードする画像のローカルパス（Noneなら画像なし）
        tags: タグのリスト

    Returns:
        bool: 投稿成功したらTrue
    """
    try:
        # --- 1. タイトル入力 (input[name="diary_title"]) ---
        title_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'input[name="diary_title"]'))
        )
        title_input.clear()
        driver.execute_script(
            "arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input'));",
            title_input, title
        )
        human_delay(1, 2)
        print(f"タイトル入力: {title}")

        # --- 2. 画像アップロード (input[name="photo1"], photo2, photo3) ---
        if image_path:
            abs_path = os.path.abspath(image_path)
            try:
                photo_input = driver.find_element(By.CSS_SELECTOR, 'input[name="photo1"]')
                photo_input.send_keys(abs_path)
                print(f"画像アップロード: {os.path.basename(abs_path)}")
                time.sleep(8)  # アップロード完了まで待機
            except Exception as e:
                print(f"画像アップロードエラー（続行）: {e}")
            human_delay(2, 3)

        # --- 3. 本文入力 ---
        body_textarea = None
        for selector in [
            'textarea[name="diary_body"]',
            'textarea[name="body"]',
            'textarea[id*="diary"]',
            'textarea[id*="body"]',
            "textarea",
        ]:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for el in elements:
                if el.is_displayed():
                    body_textarea = el
                    break
            if body_textarea:
                break

        if not body_textarea:
            print("Error: 本文入力欄が見つかりません")
            dump_debug_artifacts(driver, "mixi_body_not_found")
            return False

        driver.execute_script(
            "arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input'));",
            body_textarea, body_text
        )
        human_delay(1, 2)
        print(f"本文入力完了 ({len(body_text)}文字)")

        # --- 4. 確認ボタンクリック (input[value*="確認"]) ---
        print("確認ボタンをクリック中...")
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1)

        confirm_btn = None
        candidates = driver.find_elements(By.CSS_SELECTOR, "input[type='submit'], input[type='button'], button")
        for btn in candidates:
            if not btn.is_displayed() or not btn.is_enabled():
                continue
            label = ((btn.text or "") + " " + (btn.get_attribute("value") or "")).strip()
            if any(k in label for k in ["確認", "次へ", "投稿", "作成", "送信"]):
                confirm_btn = btn
                break

        if not confirm_btn:
            print("Error: 確認/投稿ボタンが見つかりません")
            dump_debug_artifacts(driver, "mixi_confirm_not_found")
            return False

        confirm_label = ""
        try:
            confirm_label = ((confirm_btn.text or "") + " " + (confirm_btn.get_attribute("value") or "")).strip()
        except Exception:
            confirm_label = "unknown"
        confirm_btn.click()
        print(f"  確認ボタン: '{confirm_label}'")

        # --- 5. 確認画面を待つ ---
        time.sleep(5)
        print(f"確認画面URL: {driver.current_url}")

        # --- 6. 最終投稿ボタン (input[value="作成する"]) ---
        print("作成するボタンをクリック中...")
        submit_btn = None
        try:
            submit_btn = WebDriverWait(driver, 8).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, 'input[value="作成する"]'))
            )
        except Exception:
            pass

        if not submit_btn:
            final_candidates = driver.find_elements(By.CSS_SELECTOR, "input[type='submit'], input[type='button'], button")
            for btn in final_candidates:
                if not btn.is_displayed() or not btn.is_enabled():
                    continue
                label = ((btn.text or "") + " " + (btn.get_attribute("value") or "")).strip()
                if any(k in label for k in ["作成", "投稿", "公開", "送信", "完了"]):
                    submit_btn = btn
                    break

        if submit_btn:
            submit_label = ""
            try:
                submit_label = ((submit_btn.text or "") + " " + (submit_btn.get_attribute("value") or "")).strip()
            except Exception:
                submit_label = "unknown"
            submit_btn.click()
            print(f"  最終投稿ボタン: '{submit_label}'")
        else:
            print("最終投稿ボタンを検出できなかったため、確認画面なしフローとして継続判定します")

        # --- 7. 投稿成功確認 ("書きあがりました" テキスト) ---
        time.sleep(5)
        page_source = driver.page_source
        current_url = driver.current_url
        print(f"最終URL: {current_url}")

        if "書きあがりました" in page_source:
            print("投稿成功! 「書きあがりました」確認済み")
            return True

        # フォールバック: その他の成功判定
        success_indicators = [
            "日記を作成しました", "投稿完了", "作成しました",
            "view_diary", "list_diary",
        ]
        if any(indicator in page_source for indicator in success_indicators):
            print("投稿成功! (ページ内容確認)")
            return True

        if any(keyword in current_url for keyword in ["list_diary", "view_diary", "home"]):
            print("投稿成功! (日記ページに遷移)")
            return True

        # URLが変わっていれば成功とみなす
        if "add_diary" not in current_url.lower() and "confirm" not in current_url.lower():
            print(f"投稿完了 (URL変化: {current_url})")
            return True

        print(f"Warning: 投稿結果が確認できません (URL: {current_url})")
        dump_debug_artifacts(driver, "mixi_post_unconfirmed")
        return False

    except Exception as e:
        print(f"投稿エラー: {e}")
        import traceback
        traceback.print_exc()
        dump_debug_artifacts(driver, "mixi_post_exception")
        return False


# ===== アップロードログ =====

def load_uploaded_log():
    if os.path.exists(UPLOADED_LOG):
        with open(UPLOADED_LOG, 'r') as f:
            return json.load(f)
    return []


def save_uploaded_log(log):
    with open(UPLOADED_LOG, 'w', encoding="utf-8") as f:
        json.dump(log, f, indent=2)


# ===== メイン =====

def parse_args():
    parser = argparse.ArgumentParser(description="mixi diary auto uploader")
    parser.add_argument("--dry-run", action="store_true", help="Build a preview without browser login or posting")
    parser.add_argument(
        "--sample-name",
        default="",
        help="Use a synthetic image name for an offline dry-run (requires --dry-run)",
    )
    return parser.parse_args()


def env_truthy(name):
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def write_dry_run_preview(image, title, body_text, tags, insights):
    base_dir = os.path.dirname(__file__)
    html_path = os.path.join(base_dir, DRY_RUN_OUTPUT_HTML)
    json_path = os.path.join(base_dir, DRY_RUN_OUTPUT_JSON)
    preview = {
        "mode": "dry_run",
        "generated_at_jst": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST"),
        "selected_image": image.get("name"),
        "lane": SAFE_LANE,
        "content_pool": insights.get("updated_at_jst", "fallback"),
        "title": title,
        "tags": tags,
        "body_text": body_text,
        "cta_has_utm": "utm_source=mixi" in body_text,
        "live_actions": [],
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(preview, f, ensure_ascii=False, indent=2)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write("<!doctype html><html><head><meta charset='utf-8'>")
        f.write("<title>mixi dry run preview</title></head><body>")
        f.write(f"<h1>{html.escape(title)}</h1>")
        f.write(f"<p><strong>Selected image:</strong> {html.escape(str(image.get('name', '')))}</p>")
        if image.get("url"):
            f.write(f"<p><strong>Source:</strong> {html.escape(str(image.get('url')))}</p>")
        f.write("<h2>Tags</h2><p>")
        f.write(html.escape(', '.join(tags)))
        f.write("</p><h2>Body</h2><pre style='white-space: pre-wrap'>")
        f.write(html.escape(body_text))
        f.write("</pre></body></html>")
    print(f"DRY_RUN preview written: {html_path}")
    print(f"DRY_RUN metadata written: {json_path}")


def main():
    args = parse_args()
    dry_run = args.dry_run or env_truthy("DRY_RUN")

    if args.sample_name and not dry_run:
        print("Error: --sample-name requires --dry-run")
        return 2

    # 認証チェック（Cookie方式ではメール/パスワード不要）
    if not dry_run and not os.path.exists(COOKIE_FILE):
        print("Error: Cookie未保存。先に save_cookies.py を実行してください。")
        write_run_status("HOLD_AUTH", "cookie_file_missing", EXIT_AUTH_HOLD)
        return EXIT_AUTH_HOLD

    if not args.sample_name and not GDRIVE_FOLDER_ID:
        print("Error: GDRIVE_FOLDER_ID_MIXI が未設定です")
        return 1

    now_jst = datetime.now(JST)
    print("=" * 50)
    print("mixi Diary Auto Uploader")
    print(f"Time: {now_jst.strftime('%Y-%m-%d %H:%M JST')}")
    print("=" * 50)
    print()

    if args.sample_name:
        image = {"id": None, "name": os.path.basename(args.sample_name), "offline_sample": True}
        available = [image]
        print("Offline sample mode: source listing skipped")
    else:
        # 公開フォルダから画像一覧取得
        print("公開フォルダから画像一覧を取得中...")
        images = list_gdrive_images(GDRIVE_FOLDER_ID)
        if not images:
            print("No images found!")
            return 0

        # 未アップロード画像をフィルタ
        uploaded_log = load_uploaded_log()
        available = [img for img in images if img["name"] not in uploaded_log]
        if not available:
            print("All images already uploaded!")
            return 0

        print(f"Available: {len(available)} / Total: {len(images)}")
        image = random.choice(available)
    print(f"Selected: {image['name']}")

    insights = as_insights(
        SAFE_LANE,
        platform="mixi",
        allow_remote=not bool(args.sample_name),
    )
    print(f"Content pool: {insights.get('updated_at_jst', 'fallback')}")
    tags = generate_tags(image["name"], insights)

    # タイトル・本文テキスト生成
    title = build_title(image["name"])
    body_text = build_body_text(image["name"], tags, insights)
    try:
        validate_public_payload(title, body_text, tags, insights)
    except ValueError as exc:
        print(f"PUBLIC_PAYLOAD_HOLD: {exc}")
        return 4

    print(f"Title: {title}")
    print(f"Tags: {', '.join(tags[:10])}...")
    print()

    # ローカル画像パスを決定
    if dry_run:
        write_dry_run_preview(image, title, body_text, tags, insights)
        print("DRY_RUN complete: no source download, browser login, state ledger, or live post attempted.")
        return 0

    ensure_live_dependencies()

    image_path = None
    if image.get("local_path"):
        image_path = os.path.abspath(image["local_path"])
    else:
        print("Error: approved local image path is unavailable")
        return 1

    # Seleniumで日記投稿
    driver = None
    try:
        # Chromeブラウザ起動（タイムアウト時リトライ付き）
        for attempt in range(3):
            try:
                print(f"Chromeブラウザを起動中... (attempt {attempt + 1}/3)")
                driver = create_driver(headless=True)
                break
            except Exception as e:
                print(f"ブラウザ起動エラー (attempt {attempt + 1}/3): {e}")
                if attempt < 2:
                    print("10秒後にリトライ...")
                    time.sleep(10)
                else:
                    print("ブラウザ起動に3回失敗しました")
                    return 1

        # Cookieログイン（reCAPTCHA回避）
        if not login_with_cookies(driver):
            print("Cookieログイン失敗! save_cookies.py を再実行してください。")
            write_run_status("HOLD_AUTH", "cookie_expired_or_unverified", EXIT_AUTH_HOLD)
            return EXIT_AUTH_HOLD

        human_delay(2, 4)

        # 日記作成ページに移動
        if not navigate_to_diary_editor(driver):
            print("日記作成ページに移動できません!")
            if is_auth_redirect(getattr(driver, "current_url", "")):
                write_run_status("HOLD_AUTH", "session_expired_at_editor", EXIT_AUTH_HOLD)
                return EXIT_AUTH_HOLD
            write_run_status("REVISE", "editor_navigation_failed", 1)
            return 1

        human_delay(2, 4)

        # 日記を投稿
        if post_diary_entry(driver, title, body_text, image_path, tags):
            print()
            print("=" * 50)
            print("DIARY POST SUCCESS!")
            print("=" * 50)

            # 成功 -> ログ保存
            record_publication_result("mixi", {"title": title, "caption": body_text},
                                      source_locator=driver.current_url, confirmed=True, receipt_status="ui_confirmed")
            uploaded_log.append(image["name"])
            save_uploaded_log(uploaded_log)
            write_run_status("PASS", "post_confirmed", 0)
            print(f"Remaining: {len(available) - 1}")
            return 0
        else:
            print("投稿失敗!")
            write_run_status("REVISE", "post_unconfirmed", 1)
            return 1

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        write_run_status("REVISE", f"exception:{type(e).__name__}", 1)
        return 1

    finally:
        if driver:
            try:
                driver.quit()
                print("ブラウザ終了")
            except Exception:
                pass


if __name__ == '__main__':
    sys.exit(main())
