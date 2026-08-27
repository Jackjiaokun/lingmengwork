"""灵梦work 多模态基座 (Phase 21): 统一媒体资产抽象 + 可检索资产库。

收口「统一抽象先行 / 降级链不可破 / 资产入库即检索 / 创作域复用」四大原则:
- MediaAsset: 音频/图像/视频共享的统一资产模型
- AssetLibrary: 本机 SQLite 元数据索引 + 目录落盘, 支持按域/类型/时间/语义检索、去重、配额清理
- generate(): 包装既有 multimodal_adapters.render (真实渲染 + 降级链), 产出即登记入库
上层 (对话气泡 / 超级 AGENT 编排) 可直接检索历史资产跨会话复用。

zero-dependency: 仅用标准库 (sqlite3 / json / uuid / os / time)。
"""

import os
import json
import time
import sqlite3
import uuid
from dataclasses import dataclass, asdict

MEDIA_SUBDIR = "multimodal"   # 媒体文件落盘于 <cwd>/outputs/multimodal
DB_SUBDIR = ".lmw_media"      # 资产库元数据 <cwd>/.lmw_media/assets.db

_KIND_ALIASES = {"audio": "audio", "image": "image", "video": "video",
                 "png": "image", "jpg": "image", "jpeg": "image", "gif": "image",
                 "mp3": "audio", "wav": "audio", "mp4": "video", "webm": "video"}


def _mime_of(path):
    ext = (os.path.splitext(path)[1] or "").lower()
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".mp3": "audio/mpeg", ".wav": "audio/wav",
        ".mp4": "video/mp4", ".webm": "video/webm",
    }.get(ext, "application/octet-stream")


def _url_of(path):
    """资产访问 URL (server 的 /outputs/ 路由固定映射到 outputs/multimodal)。"""
    return "/outputs/" + os.path.basename(path)


@dataclass
class MediaAsset:
    id: str
    kind: str          # "audio" | "image" | "video"
    path: str          # 本机绝对路径
    mime: str
    source: str        # "local" | "local:fallback" | "cloud:<provider>"
    real: bool
    meta: dict
    note: str
    created_at: float
    session_id: str = ""


class AssetLibrary:
    """本机媒体资产库: SQLite 元数据索引 + 目录落盘 (zero-dependency)。"""

    def __init__(self, base_dir="."):
        self.base_dir = base_dir
        self.out_dir = os.path.join(base_dir, "outputs", MEDIA_SUBDIR)
        os.makedirs(self.out_dir, exist_ok=True)
        self.db = os.path.join(base_dir, DB_SUBDIR, "assets.db")
        os.makedirs(os.path.dirname(self.db), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db) as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS media_assets("
                "id TEXT PRIMARY KEY, kind TEXT, path TEXT, mime TEXT, "
                "source TEXT, real INTEGER, meta TEXT, note TEXT, "
                "created_at REAL, session_id TEXT)"
            )

    def save(self, asset: MediaAsset) -> dict:
        with sqlite3.connect(self.db) as c:
            c.execute(
                "INSERT OR REPLACE INTO media_assets VALUES(?,?,?,?,?,?,?,?,?,?)",
                (asset.id, asset.kind, asset.path, asset.mime, asset.source,
                 1 if asset.real else 0,
                 json.dumps(asset.meta, ensure_ascii=False), asset.note,
                 asset.created_at, asset.session_id),
            )
        return asdict(asset)

    def list(self, kind=None, q=None, limit=60, offset=0):
        sql = ("SELECT id,kind,path,mime,source,real,meta,note,created_at,session_id "
               "FROM media_assets")
        where, params = [], []
        if kind:
            where.append("kind=?")
            params.append(kind)
        if q:
            where.append("(note LIKE ? OR meta LIKE ?)")
            params += ["%" + q + "%", "%" + q + "%"]
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        with sqlite3.connect(self.db) as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["real"] = bool(d["real"])
            d["meta"] = json.loads(d["meta"] or "{}")
            d["url"] = _url_of(d["path"])
            out.append(d)
        return out

    def get(self, aid):
        with sqlite3.connect(self.db) as c:
            c.row_factory = sqlite3.Row
            r = c.execute("SELECT * FROM media_assets WHERE id=?", (aid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["real"] = bool(d["real"])
        d["meta"] = json.loads(d["meta"] or "{}")
        d["url"] = _url_of(d["path"])
        return d

    def delete(self, aid):
        a = self.get(aid)
        if not a:
            return False
        try:
            if os.path.exists(a["path"]):
                os.remove(a["path"])
        except OSError:
            pass
        with sqlite3.connect(self.db) as c:
            c.execute("DELETE FROM media_assets WHERE id=?", (aid,))
        return True

    def quota_bytes(self, limit_gb=2.0):
        """清理超出配额的资产 (按 created_at 最旧优先), 返回清理条数。"""
        limit = int(limit_gb * 1024 * 1024 * 1024)
        rows = self.list(limit=10000)
        total = sum((os.path.getsize(r["path"]) if os.path.exists(r["path"]) else 0)
                    for r in rows)
        if total <= limit:
            return 0
        removed = 0
        for r in sorted(rows, key=lambda x: x["created_at"]):
            if total <= limit:
                break
            sz = os.path.getsize(r["path"]) if os.path.exists(r["path"]) else 0
            if self.delete(r["id"]):
                total -= sz
                removed += 1
        return removed


def register_asset(art, session_id="", base_dir="."):
    """把 multimodal_adapters.render 的返回 dict 规范化为 MediaAsset 并入库。"""
    if not art or not art.get("file"):
        return None
    path = art["file"]
    kind = art.get("domain") or _KIND_ALIASES.get(
        os.path.splitext(path)[1].lower(), "image")
    asset = MediaAsset(
        id=uuid.uuid4().hex[:12],
        kind=kind,
        path=path,
        mime=art.get("mime") or _mime_of(path),
        source=("local" if art.get("real") else "local:fallback"),
        real=bool(art.get("real")),
        meta=art.get("meta") or {},
        note=art.get("note") or "",
        created_at=time.time(),
        session_id=session_id or "",
    )
    d = AssetLibrary(base_dir).save(asset)
    d["url"] = _url_of(asset.path)
    return d


def generate(domain, brief, blueprint="", ctx="", session_id="", base_dir=".", llm_call=None):
    """统一生成入口: 包装 multimodal_adapters.render + 登记资产库。

    返回 MediaAsset dict (含 url), 或 None (不支持的域/失败)。
    降级链由 adapters 内部保证: 无 key/无依赖时产出占位资产, 主流程永不崩。
    """
    from . import multimodal_adapters as _ma
    out_dir = os.path.join(base_dir, "outputs", MEDIA_SUBDIR)
    art = _ma.render(domain, brief or "", blueprint or "", ctx or "",
                     out_dir=out_dir, llm_call=llm_call)
    if not art or not art.get("file"):
        return None
    return register_asset(art, session_id, base_dir)
