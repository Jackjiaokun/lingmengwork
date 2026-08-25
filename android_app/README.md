# 灵梦work · 安卓 App

把现有 Web 控制台 (零依赖 SPA) 用 [BeeWare](https://beeware.org/) 包成安卓原生 App。
App 内后台启动本地 http 服务 (`lingmengwork.web.server.run_web`)，
主界面用 Toga `WebView` 加载 `http://127.0.0.1:8318`，三端共用同一套后端与前端。

## 本机出 APK（需 Android SDK）

```bash
# 1. 准备环境 (Windows/macOS/Linux 均可)
pip install briefcase
# 安卓 SDK: 装 Android Studio, 设 ANDROID_HOME / JAVA_HOME

# 2. 进入工程
cd android_app

# 3. 生成安卓工程并构建
briefcase create android
briefcase build android

# 4. 装到真机/模拟器 (USB 调试已开)
briefcase run android
# 或直接取 APK:
# build\lingmengwork\android\gradle\app\debug\app-debug.apk
```

## 说明
- `app.py` 已把 `import toga` 延迟到运行时，因此在无 BeeWare 的环境也能验证后端起服逻辑。
- `pyproject.toml` 已声明 `toga` / `toga-android` / `toga-webview` 依赖与 `INTERNET` 权限。
- 当前环境 (沙箱) 无 Android SDK，无法真编 APK；以上命令在你本机执行即得可安装 APK。
- 模型后端：默认 `ollama` (本机需跑 Ollama)。若无，改 `config.toml` 的 `backend = "mock"` 离线演示，
  或在 App 启动前设环境变量走云端 API。

## 目录
- `app.py`           BeeWare 入口（起 server + WebView）
- `pyproject.toml`   briefcase 安卓工程配置
