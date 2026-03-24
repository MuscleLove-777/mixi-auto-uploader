# -*- coding: utf-8 -*-
"""
mixi (mixi.jp) Selenium認証ヘルパー
ブラウザ自動化でログインし、認証済みドライバーを返す
"""
import os
import time
import random
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# Selenium→ChromeDriver の HTTP 読み取りタイムアウト（秒）。mixi.jp が重いと 120 秒で落ちるため延長
REMOTE_HTTP_TIMEOUT_SEC = 360

# --- 定数 ---
MIXI_TOP_URL = "https://mixi.jp/"
MIXI_LOGIN_URL = "https://mixi.jp/login.pl"
MIXI_DIARY_EDITOR_URL = "https://mixi.jp/add_diary.pl"

# リアルなUser-Agentリスト
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]


def human_delay(min_sec=2, max_sec=5):
    """人間らしいランダム遅延"""
    delay = random.uniform(min_sec, max_sec)
    time.sleep(delay)
    return delay


def _extend_remote_http_timeout(driver):
    """ChromeDriver への HTTP 通信タイムアウトを延長（GitHub Actions で Read timed out 対策）"""
    try:
        ce = getattr(driver, "command_executor", None)
        cfg = getattr(ce, "_client_config", None) if ce else None
        if cfg is not None and hasattr(cfg, "timeout"):
            cfg.timeout = REMOTE_HTTP_TIMEOUT_SEC
    except Exception:
        pass


def safe_driver_get(driver, url, max_attempts=3, page_load_timeout_sec=300):
    """
    driver.get のタイムアウト・遅延に耐える。
    pageLoadStrategy=eager と併用すると効果的。
    """
    _extend_remote_http_timeout(driver)
    try:
        driver.set_page_load_timeout(page_load_timeout_sec)
    except Exception:
        pass

    for attempt in range(max_attempts):
        try:
            driver.get(url)
            return True
        except TimeoutException:
            print(f"  Page load timeout: {url} (attempt {attempt + 1}/{max_attempts})")
        except Exception as e:
            err = str(e).lower()
            if "read timed out" in err or "timeout" in err:
                print(f"  Navigation timeout: {url} (attempt {attempt + 1}/{max_attempts}): {e}")
            else:
                raise
        try:
            driver.execute_script("window.stop();")
        except Exception:
            pass
        time.sleep(5 + attempt * 5)

    print(f"  safe_driver_get failed after {max_attempts} attempts: {url}")
    return False


def create_driver(headless=True):
    """Chrome WebDriverを作成（検出回避設定付き）"""
    options = Options()
    if headless:
        options.add_argument("--headless=new")

    # 検出回避
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"--user-agent={random.choice(USER_AGENTS)}")

    # webdriver検出を回避
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # DOMContentLoaded 付近で先に進む（全リソース待ちで 120s 超えしがち）
    try:
        options.page_load_strategy = "eager"
    except Exception:
        pass

    service = Service()
    service.start_error_message = "ChromeDriver failed to start"
    driver = webdriver.Chrome(service=service, options=options)
    _extend_remote_http_timeout(driver)
    try:
        driver.set_page_load_timeout(300)
    except Exception:
        pass

    # navigator.webdriver を隠す
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['ja', 'en-US', 'en'],
            });
        """
    })

    return driver


def login_mixi(driver, email=None, password=None):
    """
    mixiにログインする

    Args:
        driver: Selenium WebDriver
        email: mixiメールアドレス（Noneの場合は環境変数から取得）
        password: mixiパスワード（Noneの場合は環境変数から取得）

    Returns:
        bool: ログイン成功したらTrue
    """
    email = email or os.environ.get("MIXI_EMAIL", "")
    password = password or os.environ.get("MIXI_PASSWORD", "")

    if not email or not password:
        print("Error: MIXI_EMAIL / MIXI_PASSWORD が未設定です")
        return False

    print(f"mixiにログイン中... (email: {email[:3]}***)")

    try:
        # トップページへ移動
        driver.get(MIXI_TOP_URL)
        human_delay(3, 6)

        # ログインリンク/ボタンをクリック
        try:
            # mixiトップページの「ログイン」リンクを探す
            login_selectors = [
                (By.LINK_TEXT, "ログイン"),
                (By.PARTIAL_LINK_TEXT, "ログイン"),
                (By.CSS_SELECTOR, 'a[href*="login"]'),
                (By.CSS_SELECTOR, '.loginBtn'),
                (By.CSS_SELECTOR, '#loginBtn'),
            ]
            login_clicked = False
            for by, selector in login_selectors:
                try:
                    login_link = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((by, selector))
                    )
                    login_link.click()
                    login_clicked = True
                    human_delay(3, 5)
                    print(f"ログインページ: {driver.current_url}")
                    break
                except (TimeoutException, NoSuchElementException):
                    continue

            if not login_clicked:
                # 直接ログインURLへ移動
                driver.get(MIXI_LOGIN_URL)
                human_delay(3, 5)
                print(f"直接ログインURL: {driver.current_url}")

        except Exception as e:
            print(f"ログインページへの遷移: {e}")
            driver.get(MIXI_LOGIN_URL)
            human_delay(3, 5)

        # JavaScriptレンダリングを待つ（mixiのログインページはJS描画）
        human_delay(2, 4)

        # メールアドレス入力欄を探す
        email_selectors = [
            'input[name="email"]',
            'input[name="username"]',
            'input[name="mail"]',
            'input[type="email"]',
            'input[type="text"]',
            'input[id="email"]',
            'input[id="mail"]',
            'input[placeholder*="メール"]',
            'input[placeholder*="mail"]',
        ]

        email_input = None
        for selector in email_selectors:
            try:
                email_input = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                if email_input.is_displayed():
                    break
                email_input = None
            except TimeoutException:
                continue

        if not email_input:
            # フォールバック: すべてのinput要素から探す
            inputs = driver.find_elements(By.TAG_NAME, "input")
            for inp in inputs:
                inp_type = inp.get_attribute("type") or ""
                if inp_type in ("text", "email") and inp.is_displayed():
                    email_input = inp
                    break

        if not email_input:
            print("Error: メールアドレス入力欄が見つかりません")
            print(f"Current URL: {driver.current_url}")
            print(f"Page title: {driver.title}")
            return False

        # メールアドレスを入力（人間らしくゆっくり）
        email_input.clear()
        for char in email:
            email_input.send_keys(char)
            time.sleep(random.uniform(0.05, 0.15))
        human_delay(1, 2)

        # パスワード入力欄を探す
        password_selectors = [
            'input[name="password"]',
            'input[type="password"]',
            'input[id="password"]',
        ]

        password_input = None
        for selector in password_selectors:
            try:
                password_input = driver.find_element(By.CSS_SELECTOR, selector)
                if password_input.is_displayed():
                    break
                password_input = None
            except NoSuchElementException:
                continue

        if not password_input:
            print("Error: パスワード入力欄が見つかりません")
            return False

        # パスワードを入力
        password_input.clear()
        for char in password:
            password_input.send_keys(char)
            time.sleep(random.uniform(0.05, 0.15))
        human_delay(1, 2)

        # ログインボタンをクリック
        submit_selectors = [
            'input[type="submit"]',
            'button[type="submit"]',
            'input[value="ログイン"]',
            'button[class*="login"]',
            'button[class*="submit"]',
            '.loginBtn',
            '#login_button',
        ]

        submit_btn = None
        for selector in submit_selectors:
            try:
                submit_btn = driver.find_element(By.CSS_SELECTOR, selector)
                if submit_btn.is_displayed() and submit_btn.is_enabled():
                    break
                submit_btn = None
            except NoSuchElementException:
                continue

        if not submit_btn:
            # フォールバック: ボタン/input要素から探す
            for tag in ("button", "input"):
                elements = driver.find_elements(By.TAG_NAME, tag)
                for el in elements:
                    text = (el.text or el.get_attribute("value") or "").strip()
                    if text in ("ログイン", "Login", "Sign in", "サインイン") and el.is_displayed():
                        submit_btn = el
                        break
                if submit_btn:
                    break

        if not submit_btn:
            print("Error: ログインボタンが見つかりません")
            return False

        submit_btn.click()
        human_delay(4, 7)

        # ログイン成功を確認
        current_url = driver.current_url
        print(f"ログイン後URL: {current_url}")

        # ログイン成功の判定
        if any(keyword in current_url for keyword in ["home", "check", "recent", "list_diary"]):
            if "login" not in current_url.lower():
                print("ログイン成功!")
                return True

        # Cookie確認でもログイン判定
        cookies = driver.get_cookies()
        session_cookies = [c for c in cookies if any(
            name in c["name"].lower() for name in ["session", "token", "auth", "login", "bid"]
        )]
        if session_cookies:
            print("ログイン成功! (セッションCookie確認)")
            return True

        # ページ内容でログイン確認
        try:
            page_source = driver.page_source
            if any(keyword in page_source for keyword in ["ログアウト", "マイページ", "日記を書く", "ホーム"]):
                print("ログイン成功! (ページ内容確認)")
                return True
        except Exception:
            pass

        print("Warning: ログイン状態が確認できません")
        print(f"URL: {current_url}")
        return False

    except Exception as e:
        print(f"ログインエラー: {e}")
        return False


def navigate_to_diary_editor(driver):
    """
    日記作成ページに移動する

    Args:
        driver: ログイン済みWebDriver

    Returns:
        bool: 日記作成ページに移動できたらTrue
    """
    print("日記作成ページに移動中...")
    human_delay(2, 4)

    try:
        if not safe_driver_get(driver, MIXI_DIARY_EDITOR_URL):
            print("Error: 日記ページへの遷移がタイムアウトしました")
            return False
        human_delay(3, 5)

        current_url = driver.current_url

        # ログインにリダイレクトされた場合
        if "login" in current_url.lower():
            print("Error: ログインが必要です（セッション切れ）")
            return False

        # 日記作成ページかどうか確認
        page_source = driver.page_source
        if any(keyword in page_source for keyword in [
            "diary_title", "diary_body", "日記を書く",
            "add_diary", "タイトル", "本文",
            "textarea", "日記の作成",
        ]):
            print(f"日記作成ページ到達: {current_url}")
            return True

        # URLにadd_diaryが含まれていれば成功
        if "add_diary" in current_url:
            print(f"日記作成ページ到達（URL判定）: {current_url}")
            return True

        print(f"Warning: 日記作成ページが確認できません (URL: {current_url})")
        # とりあえず続行
        return True

    except Exception as e:
        print(f"日記作成ページ移動エラー: {e}")
        return False


if __name__ == "__main__":
    print("=== mixi ログインテスト ===")
    print("環境変数 MIXI_EMAIL, MIXI_PASSWORD を設定してください")
    print()

    driver = create_driver(headless=False)  # テスト時はheadless=False
    try:
        success = login_mixi(driver)
        if success:
            print("\nログイン成功! 日記作成ページに移動します...")
            navigate_to_diary_editor(driver)
            input("Enterキーで終了...")
        else:
            print("\nログイン失敗")
    finally:
        driver.quit()
