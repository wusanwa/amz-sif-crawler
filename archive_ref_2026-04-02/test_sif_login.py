import os
import sys
import time
from playwright.sync_api import sync_playwright

# 配置信息
PHONE = "13714929577"
PASS = "JiaOyu21122"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SIF_PROFILE = os.path.join(BASE_DIR, "profiles", "sif")

def test_sif_login():
    print(f"🚀 开始测试 SIF 自动登录...", flush=True)
    print(f"📂 Profile 路径: {SIF_PROFILE}", flush=True)
    
    if not os.path.exists(SIF_PROFILE):
        os.makedirs(SIF_PROFILE, exist_ok=True)

    with sync_playwright() as p:
        # 启动持久化上下文 (类似 crawler_worker 使用的配置)
        context = p.chromium.launch_persistent_context(
            user_data_dir=SIF_PROFILE,
            headless=False,  # 修改为 True 以便在无界面环境下直接运行
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            viewport={"width": 1280, "height": 800}
        )
        
        page = context.pages[0]
        
        try:
            print("🌐 访问 SIF 首页...", flush=True)
            page.goto("https://www.sif.com/", wait_until="networkidle")
            time.sleep(2)

            # 检查是否已经登录 (寻找头像或退出按钮)
            # 根据 SIF 结构，登录后通常右上角有用户中心
            if page.query_selector(".user-name") or page.query_selector("text='退出登录'"):
                print("✅ 检测到已处于登录状态，无需重复登录。", flush=True)
                return

            print("🔑 未登录，尝试触发登录弹窗...", flush=True)
            # 点击导航栏的登录按钮
            login_btn = page.query_selector(".nav-item:has-text('登录')") or page.query_selector("text='登录'")
            if login_btn:
                login_btn.click()
                time.sleep(2)
            else:
                print("❌ 未找到登录按钮，直接尝试进入登录页...", flush=True)
                page.goto("https://new.sif.com/login")
                time.sleep(2)

            # 切换到手机号密码登录选项卡
            print("📑 切换到 '手机号密码登录'...", flush=True)
            tab_selector = "text='手机号密码登录'"
            if page.wait_for_selector(tab_selector, timeout=5000):
                page.click(tab_selector)
                time.sleep(1)
            else:
                print("⚠️ 未找到切换选项卡，可能已经在该页面...", flush=True)

            print(f"⌨️ 正在输入账号: {PHONE}...", flush=True)
            # 使用 identified selectors
            page.fill('input[placeholder="手机号码"]', PHONE)
            page.fill('input[type="password"]', PASS)
            
            # 点击同意协议 (SIF 登录通常需要勾选)
            # 通过观察，可能有一个 checkbox 
            agree_checkbox = page.query_selector('input[type="checkbox"]') or page.query_selector(".el-checkbox__inner")
            if agree_checkbox:
                print("✅ 勾选用户协议...", flush=True)
                agree_checkbox.click()

            print("🔘 点击登录按钮...", flush=True)
            submit_btn = page.query_selector(".el-dialog button.el-button--primary") or page.query_selector("button:has-text('登录')")
            if submit_btn:
                submit_btn.click()
                
                # 等待登录成功跳转
                print("⌛ 等待跳转中...", flush=True)
                page.wait_for_url("**/reverse**", timeout=10000)
                print("🎉 登录成功！已跳转回数据页。", flush=True)
            else:
                print("❌ 未找到提交按钮。", flush=True)

        except Exception as e:
            print(f"💥 过程中出现异常: {e}", flush=True)
            # 截图保存以便排查
            screenshot_path = os.path.join(BASE_DIR, "sif_login_error.png")
            page.screenshot(path=screenshot_path)
            print(f"📸 错误截图已保存至: {screenshot_path}", flush=True)
        finally:
            print("关闭浏览器...", flush=True)
            context.close()

if __name__ == "__main__":
    test_sif_login()
