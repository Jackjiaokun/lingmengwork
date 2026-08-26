"""密钥保险箱 (Secrets Vault): 在工作区安全地保存 API Key / 令牌等敏感配置。

设计说明 (诚实声明):
- 本模块提供**轻量本地加密** (PBKDF2 派生密钥 + 每条约 16 字节随机盐 + XOR 流密码),
  落盘于 ``<主根>/.lmw_secrets.json``。它能在文件被意外拷贝/提交时避免明文泄露,
  **但不是操作系统级钥匙串 (Keychain/Credential Manager)** —— 同机同用户仍可读出。
  请勿将其当作军事级保密方案; 真正的生产密钥请走环境变量或专用密钥管理服务。

存储结构::

    {
      "secrets": [
        {"key":"OPENAI_API_KEY", "value_cipher":"<base64 盐+密文>",
         "note":"...", "created_at":"...", "updated_at":"..."}
      ]
    }

API Key 走向: 外部 LLM 配置 (config.toml 的 ``api_key_env``) 仍优先走环境变量; 本保险箱
为 Agent 任务与 Shell 工具提供"项目级密钥中心", 可由 secret_get 取用或注入执行环境。
"""

import base64
import hashlib
import json
import os
import time

DEFAULT_FILENAME = ".lmw_secrets.json"
_PEPPER = b"lingmengwork-secret-vault-v1"  # 应用常量(非密钥, 仅作派生盐的输入之一)
_SALT_LEN = 16


def _default_path(base_dir=None):
    return os.path.join(base_dir or os.getcwd(), DEFAULT_FILENAME)


def _derive_key(salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", _PEPPER, salt, 100_000)


def _encrypt(plain: str) -> str:
    salt = os.urandom(_SALT_LEN)
    key = _derive_key(salt)
    data = plain.encode("utf-8")
    out = bytearray()
    for i, b in enumerate(data):
        out.append(b ^ key[i % len(key)])
    return base64.b64encode(salt + bytes(out)).decode("ascii")


def _decrypt(token: str) -> str:
    raw = base64.b64decode(token)
    salt, enc = raw[:_SALT_LEN], raw[_SALT_LEN:]
    key = _derive_key(salt)
    out = bytes(b ^ key[i % len(key)] for i, b in enumerate(enc))
    return out.decode("utf-8")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def load(base_dir=None):
    path = _default_path(base_dir)
    if not os.path.isfile(path):
        return {"secrets": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"secrets": []}
    if not isinstance(data, dict) or not isinstance(data.get("secrets"), list):
        return {"secrets": []}
    return {"secrets": data["secrets"]}


def save(base_dir, data):
    path = _default_path(base_dir)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def list_secrets(base_dir=None):
    d = load(base_dir)
    items = []
    for s in d["secrets"]:
        items.append({
            "key": s.get("key"),
            "note": s.get("note", ""),
            "created_at": s.get("created_at", ""),
            "updated_at": s.get("updated_at", ""),
            "has_value": bool(s.get("value_cipher")),
        })
    items.sort(key=lambda x: x.get("key", ""))
    return {"secrets": items}


def get_secret(key, base_dir=None):
    d = load(base_dir)
    for s in d["secrets"]:
        if s.get("key") == key:
            try:
                return _decrypt(s["value_cipher"])
            except Exception:
                return None
    return None


def set_secret(key, value, note="", base_dir=None):
    key = (key or "").strip()
    if not key:
        raise ValueError("密钥名称不能为空")
    value = value or ""
    d = load(base_dir)
    rec = None
    for s in d["secrets"]:
        if s.get("key") == key:
            rec = s
            break
    is_new = rec is None
    if is_new:
        rec = {"key": key, "value_cipher": "", "note": note,
               "created_at": _now(), "updated_at": _now()}
        d["secrets"].append(rec)
    else:
        rec["updated_at"] = _now()
        if note:
            rec["note"] = note
    rec["value_cipher"] = _encrypt(value)
    save(base_dir, d)
    return is_new


def delete_secret(key, base_dir=None):
    d = load(base_dir)
    before = len(d["secrets"])
    d["secrets"] = [s for s in d["secrets"] if s.get("key") != key]
    removed = before - len(d["secrets"])
    save(base_dir, d)
    return removed
