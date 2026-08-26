"""工作区备份与回滚: 持久化时间点快照 (类 Time Machine)。

设计:
- 备份: 把工作区根 (allowed_roots) 整目录打包为 .zip, 存入 <主根>/.lmw_backups/<id>.zip,
  并写 sidecar <id>.json 供快速列表; 压缩包内含 backup_manifest.json 自描述。
- 回滚: 把指定备份解压回各根目录; clean=True 时额外删除备份中不存在的文件 (危险, 需显式开启)。
- 自动排除 .git / .lmw_backups / __pycache__ / .lmw_index / node_modules / .lmw_audit.log 等,
  避免递归与体积膨胀。

与内存级单步 undo (tools/undo.py) 互补: undo 是进程内最近一次改动的轻量回退;
本模块是跨进程、可长期保留、可命名的工作区级时间点快照。
"""
import os
import time
import json
import zipfile
import datetime

# 目录级排除 (备份时不进入, 也用于 clean 回滚时跳过的保留目录)
DEFAULT_EXCLUDE_DIRS = {".git", ".lmw_backups", "__pycache__", ".lmw_index", "node_modules", ".venv", "venv", "dist"}
# 文件级排除
DEFAULT_EXCLUDE_FILES = {".lmw_audit.log"}


def _sanitize_id(s):
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in s)[:40]


class BackupManager:
    def __init__(self, roots):
        self.roots = [os.path.abspath(r) for r in (roots or []) if r]
        self.store = os.path.join(self.roots[0], ".lmw_backups") if self.roots else None

    # ---------- 内部工具 ----------
    def _new_id(self):
        base = time.strftime("%Y%m%d_%H%M%S")
        pid = base
        i = 2
        while os.path.exists(os.path.join(self.store, pid + ".zip")):
            pid = "%s_%d" % (base, i)
            i += 1
        return pid

    def _iter_files(self, root):
        """yield (abs_path, rel_path) 跳过排除项。"""
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in DEFAULT_EXCLUDE_DIRS]
            rel_dir = os.path.relpath(dirpath, root)
            for fn in filenames:
                if fn in DEFAULT_EXCLUDE_FILES:
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.join(rel_dir, fn) if rel_dir != "." else fn
                yield full, rel

    def _load_manifest(self, bid):
        """优先 sidecar, 回退 zip 内 manifest; 都不在则抛 FileNotFoundError。"""
        sp = os.path.join(self.store, bid + ".json")
        if os.path.isfile(sp):
            try:
                with open(sp, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        zp = os.path.join(self.store, bid + ".zip")
        if os.path.isfile(zp):
            with zipfile.ZipFile(zp) as z:
                return json.loads(z.read("backup_manifest.json").decode("utf-8"))
        raise FileNotFoundError("备份不存在: %s" % bid)

    # ---------- 对外接口 ----------
    def list(self):
        if not self.store or not os.path.isdir(self.store):
            return []
        out = []
        for name in os.listdir(self.store):
            if not name.endswith(".json"):
                continue
            p = os.path.join(self.store, name)
            try:
                with open(p, encoding="utf-8") as f:
                    m = json.load(f)
                out.append(m)
            except Exception:
                continue
        out.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return out

    def create(self, label=""):
        if not self.store:
            raise RuntimeError("未配置工作区根目录, 无法备份")
        os.makedirs(self.store, exist_ok=True)
        bid = self._new_id()
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        manifest = {
            "id": bid,
            "created_at": ts,
            "label": (label or "").strip(),
            "roots": self.roots,
            "file_count": 0,
            "total_bytes": 0,
            "clean_exclude": sorted(DEFAULT_EXCLUDE_DIRS),
        }
        zip_path = os.path.join(self.store, bid + ".zip")
        file_count = 0
        total_bytes = 0
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for ri, root in enumerate(self.roots):
                if not os.path.isdir(root):
                    continue
                prefix = "root_%d" % ri
                for full, rel in self._iter_files(root):
                    try:
                        with open(full, "rb") as f:
                            data = f.read()
                    except Exception:
                        continue
                    arc = "%s/%s" % (prefix, rel.replace(os.sep, "/"))
                    z.writestr(arc, data)
                    file_count += 1
                    total_bytes += len(data)
            # manifest 放在最后写: 此时文件计数已确定, 单次写入无重复条目
            manifest["file_count"] = file_count
            manifest["total_bytes"] = total_bytes
            z.writestr("backup_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        # sidecar 供快速列表
        with open(os.path.join(self.store, bid + ".json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        return manifest

    def rollback(self, bid, clean=False):
        manifest = self._load_manifest(bid)
        zp = os.path.join(self.store, bid + ".zip")
        if not os.path.isfile(zp):
            raise FileNotFoundError("备份压缩包不存在: %s" % bid)
        roots = manifest.get("roots", []) or []
        restored = 0
        removed = 0
        with zipfile.ZipFile(zp) as z:
            names = z.namelist()
            for ri, root in enumerate(roots):
                prefix = "root_%d/" % ri
                backed = set()
                for n in names:
                    if not n.startswith(prefix):
                        continue
                    rel = n[len(prefix):]
                    if not rel or rel == "backup_manifest.json":
                        continue
                    backed.add(rel)
                    dest = os.path.join(root, rel.replace("/", os.sep))
                    try:
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        with open(dest, "wb") as f:
                            f.write(z.read(n))
                        restored += 1
                    except Exception:
                        continue
                if clean:
                    for dirpath, dirnames, filenames in os.walk(root):
                        dirnames[:] = [d for d in dirnames if d not in DEFAULT_EXCLUDE_DIRS]
                        rel_dir = os.path.relpath(dirpath, root)
                        for fn in filenames:
                            if fn in DEFAULT_EXCLUDE_FILES:
                                continue
                            rel = os.path.join(rel_dir, fn) if rel_dir != "." else fn
                            rel = rel.replace(os.sep, "/")
                            if rel not in backed:
                                try:
                                    os.remove(os.path.join(dirpath, fn))
                                    removed += 1
                                except Exception:
                                    pass
        return {
            "id": bid,
            "restored": restored,
            "removed": removed,
            "clean": clean,
            "label": manifest.get("label", ""),
            "created_at": manifest.get("created_at", ""),
        }

    def delete(self, bid):
        removed = []
        for ext in (".zip", ".json"):
            p = os.path.join(self.store, bid + ".json") if ext == ".json" else os.path.join(self.store, bid + ".zip")
            if os.path.isfile(p):
                try:
                    os.remove(p)
                    removed.append(p)
                except Exception:
                    pass
        return {"id": bid, "removed": removed}
