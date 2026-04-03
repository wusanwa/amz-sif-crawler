#!/usr/bin/env python3
import sys
import os
import argparse
import subprocess
from playwright.sync_api import Error, sync_playwright


def clear_profile_locks(profile_path):
    """清理 Chromium 可能残留的单实例锁文件。"""
    lock_files = ["SingletonLock", "SingletonCookie", "SingletonSocket"]
    removed = []
    for filename in lock_files:
        file_path = os.path.join(profile_path, filename)
        if os.path.lexists(file_path):
            try:
                os.remove(file_path)
                removed.append(filename)
            except OSError as exc:
                print(f"[WARN] 无法删除锁文件 {file_path}: {exc}")
    if removed:
        print(f"[INFO] 已清理锁文件: {', '.join(removed)}")


def explain_profile_lock(profile_path, name):
    print(f"\n[ERROR] {name} Profile 正在被另一个 Chromium/Playwright 实例使用。")
    print(f"[PATH ] {profile_path}")
    print("[FIX  ] 请先关闭所有使用该 profile 的浏览器/脚本后重试。")
    print("[TIP  ] 如果你确认没有进程在使用，可执行:")
    print(f"        python3 setup_profiles.py --sif --force-unlock")
    print("        (Amazon 可改为 --amazon --force-unlock)")


def explain_profile_permission(profile_path, name):
    try:
        st = os.stat(profile_path)
        owner_uid = st.st_uid
        group_gid = st.st_gid
        mode = oct(st.st_mode & 0o777)
    except OSError:
        owner_uid = "?"
        group_gid = "?"
        mode = "?"

    print(f"\n[ERROR] {name} Profile 目录权限不足，当前用户无法访问。")
    print(f"[PATH ] {profile_path}")
    print(f"[STAT ] uid={owner_uid}, gid={group_gid}, mode={mode}")
    print("[FIX  ] 请修复目录属主和权限后再重试：")
    print(f"        sudo chown -R $USER:$USER {profile_path}")
    print(f"        chmod -R u+rwX {profile_path}")


def open_profile(profile_path, url, name, force_unlock=False):
    print(f"\n[INFO] 正在启动 {name} 浏览器...")
    print(f"[PATH] Profile 目录: {profile_path}")
    print(f"[URL ] 目标地址: {url}")
    print(f"[HINT] >>> 请在浏览器窗口中完成登录或验证码处理。")
    print(f"[HINT] >>> 操作完成后，关闭浏览器窗口即可保存状态。\n")

    if not os.path.exists(profile_path):
        os.makedirs(profile_path, exist_ok=True)
    elif force_unlock:
        clear_profile_locks(profile_path)
    if not os.access(profile_path, os.R_OK | os.W_OK | os.X_OK):
        explain_profile_permission(profile_path, name)
        raise SystemExit(3)

    with sync_playwright() as p:
        try:
            # 启动持久化上下文
            context = p.chromium.launch_persistent_context(
                user_data_dir=profile_path,
                headless=False,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
        except Error as exc:
            message = str(exc)
            if "EACCES" in message or "Permission denied" in message:
                explain_profile_permission(profile_path, name)
                raise SystemExit(3) from exc
            if "ProcessSingleton" in message or "profile is already in use" in message:
                explain_profile_lock(profile_path, name)
                raise SystemExit(2) from exc
            raise

        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(url)
            # 进入暂停模式，等待用户手动操作
            page.pause()
        except KeyboardInterrupt:
            print("\n[INFO] 用户手动中断。")
        finally:
            context.close()
            print(f"[SUCCESS] {name} 环境已关闭并保存。")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Amazon & SIF Profile 维护工具")
    parser.add_argument("--amazon", action="store_true", help="配置 Amazon Profile (地区/验证码)")
    parser.add_argument("--sif", action="store_true", help="配置 SIF Profile (手动登录)")
    parser.add_argument("--auto-sif", action="store_true", help="自动执行 SIF 登录 (自动补全账号密码)")
    parser.add_argument("--no-pack", action="store_true", help="配置完成后不自动打包 profile 压缩包")
    parser.add_argument(
        "--force-unlock",
        action="store_true",
        help="启动前清理 Chromium 残留锁文件（确认无相关浏览器进程时再使用）"
    )
    
    args = parser.parse_args()
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    runtime_root = os.getenv("APP_RUNTIME_ROOT", os.path.join(base_dir, "runtime_data"))
    profile_root = os.getenv("PROFILE_ROOT_DIR", os.path.join(runtime_root, "profiles"))
    bundle_script = os.path.join(base_dir, "scripts", "profile_bundle.sh")

    def pack_profile(target_name):
        if args.no_pack:
            print(f"[INFO] 已跳过自动打包（--no-pack）：{target_name}")
            return
        if not os.path.exists(bundle_script):
            print(f"[WARN] 未找到打包脚本，跳过：{bundle_script}")
            return
        print(f"[INFO] 正在打包 {target_name} profile 压缩包...")
        subprocess.run(["bash", bundle_script, "pack", target_name], check=False)
    
    if args.amazon:
        path = os.path.join(profile_root, "amazon")
        target = "https://www.amazon.com"
        open_profile(path, target, "Amazon", force_unlock=args.force_unlock)
        pack_profile("amazon")
    elif args.sif:
        path = os.path.join(profile_root, "sif")
        target = "https://www.sif.com/reverse?country=US&asin=B0CDX5XGLK&isListingSearch=0"
        open_profile(path, target, "SIF", force_unlock=args.force_unlock)
        pack_profile("sif")
    elif args.auto_sif:
        log_script = os.path.join(base_dir, "sif_login.py")
        print("\n[INFO] 正在启动自动登录...")
        # 此时可以用 headless=False 让用户看到过程
        subprocess.run([sys.executable, log_script])
        pack_profile("sif")
    else:
        parser.print_help()
