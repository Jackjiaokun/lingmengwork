# 灵梦work 多模态域方案 · 音频 / 图像 / 视频

> 配套：`灵梦work_全量升级总纲_Phase20-30.md` · `灵梦work_工作伙伴与超级AGENT.md`
> 目标：把「编码工作台」扩成「能听、能看、能生成的综合超级 AGENT」——音频、图像、视频统一抽象、统一资产库、统一降级链。

---

## 0. 设计原则

1. **统一抽象先行**：音频/图像/视频共享同一 `MediaAsset` 模型（id / 类型 / 路径 / 元数据 / 来源 / 时间戳），上层无感切换。
2. **降级链不可破**：无云端 key → 本地占位（静音 wav / 纯色 png / 静态图序列）→ 明确提示「配置 key 解锁真实生成」。主流程永不崩。
3. **资产入库即检索**：所有产出进本地资产库（SQLite + 目录），支持按域/类型/时间/语义检索，供后续任务复用。
4. **创作域复用**：接入既有 `creation_domains` 引擎，新增 `audio` / `image` / `video` 三个域，统一走 `enhance` 提升协议。

---

## 1. 统一多模态引擎（Phase 21 基座）

```
lingmengwork/multimodal/
├── base.py          # MediaAsset / MediaRequest / Engine 抽象 / 降级链
├── registry.py      # 引擎注册（audio/image/video 各 provider）
├── assets.py        # 资产库（SQLite + 目录，检索/去重/清理）
├── audio_engine.py  # Phase 22
├── image_engine.py  # Phase 23
└── video_engine.py  # Phase 24
```

**MediaAsset 模型**
```python
@dataclass
class MediaAsset:
    id: str
    kind: str            # "audio" | "image" | "video"
    path: str            # 本机绝对路径
    mime: str
    source: str          # "local" | "cloud:<provider>"
    meta: dict           # duration/size/resolution/prompt/seed...
    created_at: float
```

**Engine 接口（统一）**
```python
class MediaEngine:
    def generate(self, req: MediaRequest) -> MediaAsset: ...
    def can_real(self) -> bool:            # 是否具备真实生成条件（有 key）
    def fallback(self, req) -> MediaAsset: # 降级占位
```

---

## 2. 音频域（Phase 22）

| 子能力 | 真实生成（有 key） | 本地降级 | 资产 meta |
|---|---|---|---|
| **TTS 语音合成** | 云端 TTS / 本地 coqui-tts（可选装） | 静音 wav + 文本字幕文件 | text / voice / lang / duration |
| **语音克隆** | 云端 voice-clone API | 不支持→提示 | speaker / sample |
| **音乐生成** | 云端 music API / 本地音频合成 | 简单和弦 wav | bpm / key / mood |
| **音效合成** | 云端 sfx / 本地振荡器 | 基础 beep wav | category / trigger |
| **音频剪辑** | ffmpeg（本机若有） | 复制/截断 | in/out/cut |

**接入点**：`creation_domains` 增 `audio` 域；对话中输入「给这段解说配语音」→ 路由 audio_engine → 产出 asset → 回写对话气泡（`<audio>` 标签）。

---

## 3. 图像域（Phase 23）

| 子能力 | 真实生成 | 本地降级 | 资产 meta |
|---|---|---|---|
| **文生图** | 云端文生图 API | 纯色/渐变 png + prompt 文本 | prompt / seed / size |
| **图生图** | img2img API | 复制原图 + 标注 | init_image / strength |
| **Inpainting** | inpaint API | 原图 + 蒙版标注 | mask / region |
| **超分** | sr API / 本地 | 原图放大（最近邻） | scale |
| **资产入库** | — | — | 自动 |

**接入点**：对话「画一张…」→ image_engine → asset → 气泡内 `<img>` + 可下载；支持「基于这张再改」链式 img2img。

---

## 4. 视频域（Phase 24）

| 子能力 | 真实生成 | 本地降级 | 资产 meta |
|---|---|---|---|
| **文生视频** | 云端 t2v API | 静态图序列 GIF（非真实视频，明确标注） | prompt / duration / fps |
| **图生视频** | i2v API | 原图 + KenBurns 缓动 GIF | init / motion |
| **剪辑/字幕/配音** | ffmpeg（本机若有） | 串联图片 + 文本字幕 | clips / sub / audio |

**接入点**：对话「做个 10 秒产品宣传视频」→ 拆解脚本→ image_engine 出帧→ audio_engine 出配音→ video_engine 合成→ asset（mp4）→ 气泡内 `<video>`。

---

## 5. 资产库（统一检索）

```sql
CREATE TABLE media_assets (
  id TEXT PRIMARY KEY, kind TEXT, path TEXT, mime TEXT,
  source TEXT, meta TEXT, created_at REAL, session_id TEXT
);
```
- 检索：`kind=` / `source=` / `created_at>` / `meta LIKE %prompt%`
- 清理：超过配额（默认 2GB）按 LRU + 人工确认删除
- 跨会话复用：超级 AGENT 编排时可检索历史资产（如「用上次那张图再做视频」）

---

## 6. 与 creation_domains 集成

`creation_domains` 现有 `enhance` 提升协议扩展为：
```python
DOMAINS = {
  "code": CodeDomain,
  "audio": AudioDomain,   # Phase 22
  "image": ImageDomain,   # Phase 23
  "video": VideoDomain,   # Phase 24
  "doc": DocDomain,
}
```
统一 `enhance(asset, level)` → 返回提升后 asset，保持现有调用契约不变。

---

## 7. 验收（Phase 21–24 合计）

- 无 key：音频/图/视频均走降级链，主流程零崩，明确提示「配置 key 解锁真实生成」。
- 有 key：真实产出进资产库，对话气泡可播放/预览/下载，审计链记录每次生成。
- 资产库检索：跨会话按 prompt/类型召回历史资产成功。
