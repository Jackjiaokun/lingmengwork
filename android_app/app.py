"""灵梦work 安卓 App 入口 (BeeWare / Toga + 内嵌 WebView)。

原理: 后台线程启动本地 http 服务 (复用 lingmengwork.web.server.run_web),
主界面用 Toga WebView 加载 http://127.0.0.1:8318, 即把现有 Web 控制台
包成安卓原生 App。三端(CLI/Web/安卓)共用同一套后端与前端代码。

出包 (在本机, 需 Android SDK):
    pip install briefcase
    cd android_app
    briefcase create android
    briefcase build android
    briefcase run android        # 连真机/模拟器装 APK
"""
import threading
import time
import urllib.request
import os
import sys

# 把 lingmengwork 源码目录加入路径 (android_app 与 lingmengwork 工程同级)
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lingmengwork.web.server import run_web

PORT = 8318
HOST = "127.0.0.1"


def _start_server():
    """后台线程: 启动本地 http 服务 (阻塞, 直到进程退出)。"""
    try:
        run_web(host=HOST, port=PORT, cfg=None)
    except Exception as e:
        print("[lingmengwork-android] server error:", e)


def _wait_server(timeout=10):
    """等 server 就绪 (轮询 health)。"""
    url = f"http://{HOST}:{PORT}/api/health"
    for _ in range(timeout * 10):
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def build(app):
    """构建主界面: 一个 WebView 加载本地控制台。"""
    import toga
    from toga.style import Pack
    from toga.style.pack import COLUMN

    # 启动 server 线程
    t = threading.Thread(target=_start_server, daemon=True)
    t.start()
    _wait_server()

    webview = toga.WebView(style=Pack(flex=1))
    try:
        webview.url = f"http://{HOST}:{PORT}/"
    except Exception:
        if hasattr(webview, "load"):
            webview.load(f"http://{HOST}:{PORT}/")

    main_box = toga.Box(children=[webview], style=Pack(direction=COLUMN, flex=1))
    return main_box


def main():
    import toga
    return toga.App(
        "灵梦work",
        "com.lingmengworkwork",
        startup=build,
        on_exit=lambda app: True,
    )


if __name__ == "__main__":
    main().main_loop()
