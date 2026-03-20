# -*- coding: utf-8 -*-
"""
mixi (mixi.jp) 画像自動投稿（GitHub Actions用）
Google Driveから画像取得 -> ランダム1枚を日記として投稿 -> アップロード済みを記録
Selenium使用（mixi公式APIなし）
"""
import sys
import json
import os
import random
import time
from datetime import datetime, timezone, timedelta

import requests
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from mixi_auth import create_driver, navigate_to_diary_editor, human_delay

COOKIE_FILE = os.path.join(os.path.dirname(__file__), "mixi_cookies.json")


def login_with_cookies(driver):
    """保存済みCookieでログインする（reCAPTCHA回避）"""
    if not os.path.exists(COOKIE_FILE):
        print("Error: Cookie未保存。先に save_cookies.py を実行してください。")
        return False

    with open(COOKIE_FILE, "r") as f:
        cookies = json.load(f)

    # まずmixi.jpにアクセスしてドメインを設定
    driver.get("https://mixi.jp/")
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
    driver.get("https://mixi.jp/home.pl")
    human_delay(3, 5)

    current_url = driver.current_url
    if "login" in current_url.lower():
        print("Error: Cookieが期限切れです。save_cookies.py を再実行してください。")
        return False

    # ページ内容でログイン確認
    page_source = driver.page_source
    if any(keyword in page_source for keyword in ["ログアウト", "マイページ", "日記を書く", "ホーム"]):
        print("Cookieログイン成功!")
        return True

    # URLがhomeを含んでいれば成功とみなす
    if "home" in current_url or "mixi.jp" in current_url:
        if "login" not in current_url.lower():
            print("Cookieログイン成功! (URL判定)")
            return True

    print("Error: Cookieログインの確認ができません。save_cookies.py を再実行してください。")
    return False


JST = timezone(timedelta(hours=9))

# --- 環境変数 ---
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID_MIXI", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

PATREON_LINK = "https://www.patreon.com/cw/MuscleLove"
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
UPLOADED_LOG = os.path.join(os.path.dirname(__file__), "uploaded_mixi.json")

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
]

# 日記タイトルテンプレート（ランダム選択）
TITLE_TEMPLATES = [
    "{category} | MuscleLove",
    "{category} - 筋肉美の世界",
    "【{category}】筋肉女子の魅力",
    "{category} | Fitness Art",
    "MuscleLove | {category}",
    "{category} - パワフルな美しさ",
    "今日の{category} - MuscleLove",
    "{category} 筋肉美女ギャラリー",
]

# 日記本文テンプレート（プレーンテキスト、mixi日記はtextarea）
BODY_TEMPLATES = [
    """{caption}

{hashtags}

━━━━━━━━━━━━━━━━━━━━
Patreonで限定コンテンツ配信中!
{patreon_link}
ここでしか見れない筋肉美女のコンテンツを毎日更新中
━━━━━━━━━━━━━━━━━━━━""",
    """{caption}

{hashtags}

★ Patreonで限定コンテンツ公開中! ★
{patreon_link}
毎日更新のオリジナルコンテンツをチェック!""",
    """{caption}

{hashtags}

▼ More exclusive content on Patreon ▼
{patreon_link}""",
]

# キャプションテンプレート
CAPTION_TEMPLATES = [
    "筋肉美の世界へようこそ! パワフルで美しい筋肉女子のコンテンツをお届けします。",
    "MuscleLoveが厳選した筋肉美女コンテンツ。力強さと美しさの融合をご覧ください。",
    "フィットネスの美しさを追求する女性たちの魅力的なコンテンツです。",
    "鍛え抜かれた美しい筋肉。MuscleLoveのオリジナルコンテンツをお楽しみください。",
    "パワフルで魅力的な筋肉女子の世界。ここでしか見れないコンテンツを毎日更新中!",
]


# ===== Google Drive =====

def list_gdrive_images(folder_id):
    """Google Drive APIまたはgdownで画像一覧を取得"""
    if GOOGLE_API_KEY:
        return _list_via_api(folder_id)
    else:
        return _list_via_gdown(folder_id)


def _list_via_api(folder_id):
    """Google Drive API v3で画像一覧を取得"""
    url = "https://www.googleapis.com/drive/v3/files"
    query = f"'{folder_id}' in parents and trashed = false"
    params = {
        "q": query,
        "key": GOOGLE_API_KEY,
        "fields": "files(id,name,mimeType)",
        "pageSize": 1000,
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    files = resp.json().get("files", [])

    images = []
    for f in files:
        ext = os.path.splitext(f["name"])[1].lower()
        if ext in IMAGE_EXTENSIONS:
            images.append({
                "id": f["id"],
                "name": f["name"],
                "url": f"https://drive.google.com/uc?export=download&id={f['id']}",
            })
    return images


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
    return images


def download_single_image(file_id):
    """Google Driveから1ファイルをダウンロード"""
    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.content


# ===== タグ・テキスト生成 =====

def generate_tags(image_name):
    """ファイル名からハッシュタグを生成"""
    tags = list(BASE_TAGS)
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
        if t.lower() not in seen:
            seen.add(t.lower())
            unique_tags.append(t)
    return unique_tags


def extract_category(image_name):
    """ファイル名からカテゴリを推定"""
    parts = image_name.replace('-', ' ').replace('_', ' ').split()
    skip = {'jpg', 'jpeg', 'png', 'webp', 'img', 'image', 'photo'}
    for p in parts:
        if p.lower() not in skip and len(p) > 2:
            return p.capitalize()
    return "Muscle Art"


def build_title(image_name):
    """日記タイトルを生成"""
    category = extract_category(image_name)
    template = random.choice(TITLE_TEMPLATES)
    return template.format(category=category)


def build_body_text(image_name, tags):
    """日記本文のプレーンテキストを生成（mixi日記はtextarea）"""
    hashtags = ' '.join([f'#{t}' for t in tags[:15]])
    caption = random.choice(CAPTION_TEMPLATES)
    template = random.choice(BODY_TEMPLATES)

    text = template.format(
        caption=caption,
        hashtags=hashtags,
        patreon_link=PATREON_LINK,
    )
    return text.strip()


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
        title_input.send_keys(title)
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

        # --- 3. 本文入力 (textarea[name="diary_body"]) ---
        body_textarea = driver.find_element(By.CSS_SELECTOR, 'textarea[name="diary_body"]')
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

        confirm_btn = driver.find_element(By.CSS_SELECTOR, 'input[value*="確認"]')
        confirm_btn.click()
        print(f"  確認ボタン: '{confirm_btn.get_attribute('value')}'")

        # --- 5. 確認画面を待つ ---
        time.sleep(5)
        print(f"確認画面URL: {driver.current_url}")

        # --- 6. 最終投稿ボタン (input[value="作成する"]) ---
        print("作成するボタンをクリック中...")
        submit_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, 'input[value="作成する"]'))
        )
        submit_btn.click()
        print("  最終投稿ボタン: '作成する'")

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
        return False

    except Exception as e:
        print(f"投稿エラー: {e}")
        import traceback
        traceback.print_exc()
        return False


# ===== アップロードログ =====

def load_uploaded_log():
    if os.path.exists(UPLOADED_LOG):
        with open(UPLOADED_LOG, 'r') as f:
            return json.load(f)
    return []


def save_uploaded_log(log):
    with open(UPLOADED_LOG, 'w') as f:
        json.dump(log, f, indent=2)


# ===== メイン =====

def main():
    # 認証チェック（Cookie方式ではメール/パスワード不要）
    if not os.path.exists(COOKIE_FILE):
        print("Error: Cookie未保存。先に save_cookies.py を実行してください。")
        return 1

    if not GDRIVE_FOLDER_ID:
        print("Error: GDRIVE_FOLDER_ID_MIXI が未設定です")
        return 1

    now_jst = datetime.now(JST)
    print("=" * 50)
    print("mixi Diary Auto Uploader")
    print(f"Time: {now_jst.strftime('%Y-%m-%d %H:%M JST')}")
    print("=" * 50)
    print()

    # Google Driveから画像一覧取得
    print("Google Driveから画像一覧を取得中...")
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

    # ランダムに1枚選択
    image = random.choice(available)
    print(f"Selected: {image['name']}")

    # タグ生成
    tags = generate_tags(image["name"])

    # トレンドタグ追加
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'x-auto-uploader'))
        from trending import get_trending_tags
        trend_tags = get_trending_tags(max_tags=5)
        if trend_tags:
            seen = {t.lower() for t in tags}
            for t in trend_tags:
                if t.lower() not in seen:
                    tags.append(t)
                    seen.add(t.lower())
    except ImportError:
        print("trending.py not found, skipping trend tags")

    # タイトル・本文テキスト生成
    title = build_title(image["name"])
    body_text = build_body_text(image["name"], tags)

    print(f"Title: {title}")
    print(f"Tags: {', '.join(tags[:10])}...")
    print()

    # ローカル画像パスを決定
    image_path = None
    if image.get("local_path"):
        image_path = os.path.abspath(image["local_path"])
    elif image.get("id"):
        # Google Drive APIの画像をダウンロードしてローカルに保存
        print("Google Driveから画像をダウンロード中...")
        try:
            img_data = download_single_image(image["id"])
            dl_dir = os.path.join(os.path.dirname(__file__), "images")
            os.makedirs(dl_dir, exist_ok=True)
            image_path = os.path.abspath(os.path.join(dl_dir, image["name"]))
            with open(image_path, "wb") as f:
                f.write(img_data)
            print(f"ダウンロード完了: {image_path}")
        except Exception as e:
            print(f"画像ダウンロードエラー: {e}")
            # 画像なしでテキストのみ投稿を続行
            image_path = None

    # Seleniumで日記投稿
    driver = None
    try:
        print("Chromeブラウザを起動中...")
        driver = create_driver(headless=True)

        # Cookieログイン（reCAPTCHA回避）
        if not login_with_cookies(driver):
            print("Cookieログイン失敗! save_cookies.py を再実行してください。")
            return 1

        human_delay(2, 4)

        # 日記作成ページに移動
        if not navigate_to_diary_editor(driver):
            print("日記作成ページに移動できません!")
            return 1

        human_delay(2, 4)

        # 日記を投稿
        if post_diary_entry(driver, title, body_text, image_path, tags):
            print()
            print("=" * 50)
            print("DIARY POST SUCCESS!")
            print("=" * 50)

            # 成功 -> ログ保存
            uploaded_log.append(image["name"])
            save_uploaded_log(uploaded_log)
            print(f"Remaining: {len(available) - 1}")
            return 0
        else:
            print("投稿失敗!")
            return 1

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
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
