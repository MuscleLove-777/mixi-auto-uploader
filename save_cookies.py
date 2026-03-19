# -*- coding: utf-8 -*-
"""
mixi Cookie取得スクリプト
ブラウザが開く -> 90秒以内に手動でログイン -> Cookie自動保存
"""
import json
import time
import sys
from mixi_auth import create_driver

COOKIE_FILE = "mixi_cookies.json"


def main():
    print("=== mixi Cookie取得 ===")
    print("ブラウザが開きます。90秒以内にログインしてください。")

    driver = create_driver(headless=False)
    try:
        # ログインページへ
        driver.get("https://mixi.jp/")
        time.sleep(3)

        # ログインボタンをクリック
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        try:
            # ログインリンクを探す
            login_selectors = [
                (By.LINK_TEXT, "ログイン"),
                (By.PARTIAL_LINK_TEXT, "ログイン"),
                (By.CSS_SELECTOR, 'a[href*="login"]'),
            ]
            for by, selector in login_selectors:
                try:
                    login_link = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((by, selector))
                    )
                    login_link.click()
                    break
                except Exception:
                    continue
        except Exception:
            pass

        # 90秒待つ（その間に手動でログイン）
        print("ブラウザでログインしてください...")
        print("残り: ", end="", flush=True)
        for i in range(90, 0, -5):
            # ログイン完了チェック
            current = driver.current_url
            if "mixi.jp" in current and "login" not in current.lower() and "auth" not in current.lower():
                try:
                    if any(kw in driver.page_source for kw in ["ログアウト", "マイページ", "日記を書く", "ホーム"]):
                        print(f"\nログイン検出!")
                        break
                except Exception:
                    pass
            print(f"{i}s ", end="", flush=True)
            time.sleep(5)
        print()

        # 日記作成ページにもアクセスして関連Cookieを取得
        print("日記作成ページに移動...")
        driver.get("https://mixi.jp/add_diary.pl")
        time.sleep(8)

        # 日記作成ページでログインが必要な場合は待つ
        if "login" in driver.current_url.lower():
            print("日記ページへのログインが必要です。ブラウザで操作してください...")
            for i in range(60, 0, -5):
                current = driver.current_url
                if "mixi.jp" in current and "login" not in current.lower():
                    print(f"\n日記ページログイン検出!")
                    break
                print(f"{i}s ", end="", flush=True)
                time.sleep(5)
            print()

        # Cookie保存
        cookies = driver.get_cookies()
        with open(COOKIE_FILE, "w") as f:
            json.dump(cookies, f, indent=2)

        print(f"\nCookie保存完了! ({len(cookies)}個)")
        print(f"保存先: {COOKIE_FILE}")
        print(f"最終URL: {driver.current_url}")
        return 0

    except Exception as e:
        print(f"Error: {e}")
        return 1
    finally:
        driver.quit()


if __name__ == "__main__":
    sys.exit(main())
