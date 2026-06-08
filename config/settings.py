"""配置管理模块 — 基于 JSON 文件的读写，敏感字段 AES 加密"""

import json
import os
import base64
import hashlib
import uuid
from pathlib import Path
from threading import Lock

CONFIG_DIR = Path.home() / ".video_sniffer"
CONFIG_FILE = CONFIG_DIR / "settings.json"
KEY_FILE = CONFIG_DIR / ".secret_key"

# 需要加密存储的敏感字段路径
_SENSITIVE_KEYS = {"ai.api_key", "site_cookies.bilibili", "site_cookies.missav"}

# 加密值标记前缀
_ENCRYPTED_PREFIX = "ENC:"

DEFAULT_SETTINGS = {
    "network": {
        "proxy_host": "127.0.0.1",
        "proxy_port": 8080,
        "upstream_proxy": "",       # 上游代理（访问外网），如 http://127.0.0.1:7897
        "download_threads": 20,
        "speed_limit": 0,
        "timeout": 30,
        "retry_count": 3,
    },
    "video": {
        "default_quality": "highest",
        "auto_merge": True,
        "auto_transcode": False,
        "keep_temp_files": False,
        "download_path": str(Path.home() / "Downloads" / "VideoSniffer"),
    },
    "ui": {
        "theme": "dark",
        "language": "zh_CN",
        "notify_complete": True,
        "auto_start": False,
    },
    "sniffer": {
        "filter_domains": [],
        "filter_extensions": [".mp4", ".m3u8", ".ts", ".flv", ".webm", ".avi", ".mkv", ".mov"],
        "filter_keywords": ["video", "stream", "playlist", "segment", "m3u8"],
    },
    "site_cookies": {
        "bilibili": "",
        "missav": "",
    },
    "ai": {
        "api_key": "",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
}

_lock = Lock()
_cache = None
_aes_key: bytes | None = None


# ── 密钥管理 ──────────────────────────────────────────────

def _get_or_create_key() -> bytes:
    """获取或创建 AES 加密密钥（机器绑定）"""
    global _aes_key
    if _aes_key is not None:
        return _aes_key

    _ensure_config_dir()
    if KEY_FILE.exists():
        with open(KEY_FILE, "rb") as f:
            _aes_key = f.read()
    else:
        raw = hashlib.sha256(uuid.getnode().to_bytes(8, "big") + os.urandom(32)).digest()
        with open(KEY_FILE, "wb") as f:
            f.write(raw)
        # 限制文件权限（仅 Windows 下有效，忽略失败）
        try:
            import stat
            os.chmod(KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
        _aes_key = raw
    return _aes_key


def _encrypt_value(plaintext: str) -> str:
    """AES-256-GCM 加密 → Base64"""
    if not plaintext:
        return plaintext
    from Crypto.Cipher import AES
    key = _get_or_create_key()
    nonce = os.urandom(12)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode("utf-8"))
    payload = nonce + tag + ciphertext
    return _ENCRYPTED_PREFIX + base64.b64encode(payload).decode("ascii")


def _decrypt_value(token: str) -> str:
    """Base64 → AES-256-GCM 解密"""
    if not token or not token.startswith(_ENCRYPTED_PREFIX):
        return token
    from Crypto.Cipher import AES
    key = _get_or_create_key()
    raw = base64.b64decode(token[len(_ENCRYPTED_PREFIX):])
    nonce, tag, ciphertext = raw[:12], raw[12:28], raw[28:]
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    try:
        plain = cipher.decrypt_and_verify(ciphertext, tag)
        return plain.decode("utf-8")
    except Exception:
        return ""


# ── 配置加载 / 保存 ──────────────────────────────────────

def _ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load() -> dict:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        _ensure_config_dir()
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
            except (json.JSONDecodeError, IOError):
                saved = {}
            merged = _deep_merge(DEFAULT_SETTINGS, saved)
        else:
            merged = dict(DEFAULT_SETTINGS)
        # 解密敏感字段
        _decrypt_sensitive(merged)
        _cache = merged
        return merged


def save(settings: dict = None) -> None:
    global _cache
    with _lock:
        _ensure_config_dir()
        data = settings if settings is not None else _cache
        if data is None:
            data = DEFAULT_SETTINGS
        # 先写加密副本到磁盘
        to_save = _deep_copy(data)
        _encrypt_sensitive(to_save)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(to_save, f, ensure_ascii=False, indent=2, default=str)
        _cache = data


def get(key: str, default=None):
    """点号分隔路径取值，如 get('network.proxy_port')"""
    settings = load()
    keys = key.split(".")
    val = settings
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return default
    return val if val is not None else default


def set_(key: str, value) -> None:
    """点号分隔路径设值"""
    settings = load()
    keys = key.split(".")
    obj = settings
    for k in keys[:-1]:
        obj = obj.setdefault(k, {})
    obj[keys[-1]] = value
    save(settings)


def reset() -> None:
    global _cache
    with _lock:
        _cache = dict(DEFAULT_SETTINGS)
        save(_cache)


# ── 敏感字段加解密 ────────────────────────────────────────

def _walk_and_transform(data: dict, prefix: str | tuple, transform_fn) -> None:
    """递归遍历 dict，对匹配前缀的叶子节点调用 transform_fn"""
    for k, v in data.items():
        path = f"{prefix}.{k}" if isinstance(prefix, str) else k
        if isinstance(v, dict):
            _walk_and_transform(v, path, transform_fn)
        elif path in _SENSITIVE_KEYS and v:
            data[k] = transform_fn(v)


def _encrypt_sensitive(data: dict) -> None:
    _walk_and_transform(data, "", _encrypt_value)


def _decrypt_sensitive(data: dict) -> None:
    _walk_and_transform(data, "", _decrypt_value)


# ── 工具函数 ──────────────────────────────────────────────

def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _deep_copy(d: dict) -> dict:
    """深拷贝 dict 用于加密后存储"""
    result = {}
    for k, v in d.items():
        if isinstance(v, dict):
            result[k] = _deep_copy(v)
        else:
            result[k] = v
    return result
