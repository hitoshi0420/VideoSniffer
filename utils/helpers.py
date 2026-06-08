"""通用工具函数"""

import re
import os
import time
from pathlib import Path
from urllib.parse import urlparse, urljoin


def format_bytes(b: int) -> str:
    if b <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(b)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.1f} {units[i]}" if i > 0 else f"{int(size)} B"


def format_time(seconds: float) -> str:
    if seconds <= 0 or seconds == float("inf"):
        return "--:--"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_speed(bytes_per_sec: float) -> str:
    return format_bytes(int(bytes_per_sec)) + "/s"


def sanitize_filename(name: str) -> str:
    """移除文件名中的非法字符"""
    name = re.sub(r"""[<>:"/\\|?*']""", "_", name)
    name = name.strip().strip(".")
    return name or "untitled"


def extract_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.hostname or parsed.netloc or "unknown"
    except Exception:
        return "unknown"


def resolve_url(base_url: str, relative_url: str) -> str:
    return urljoin(base_url, relative_url)


def is_video_url(url: str) -> bool:
    """检测 URL 是否可能是视频资源"""
    video_exts = {".mp4", ".m3u8", ".ts", ".flv", ".webm", ".avi", ".mkv", ".mov", ".m4v", ".mpd"}
    keywords = {"video", "stream", "playlist", "segment"}
    url_lower = url.lower()
    parsed = urlparse(url_lower)
    path = parsed.path
    if any(path.endswith(ext) for ext in video_exts):
        return True
    if any(kw in url_lower for kw in keywords):
        return True
    return False


def get_file_extension(url: str) -> str:
    path = urlparse(url).path
    ext = os.path.splitext(path)[1].lower().split("?")[0]
    return ext


def ensure_dir(path: str | Path):
    Path(path).mkdir(parents=True, exist_ok=True)


def timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def get_proxy() -> str | None:
    """获取上游代理 URL，优先级：settings > 系统环境变量"""
    from config import settings
    upstream = settings.get("network.upstream_proxy", "")
    if upstream:
        return _normalize_proxy(upstream)
    # 回退到系统环境变量
    for env_var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        val = os.environ.get(env_var)
        if val:
            return _normalize_proxy(val)
    return None


def _normalize_proxy(raw: str) -> str:
    """确保代理 URL 有 http:// 前缀"""
    raw = raw.strip()
    if not raw:
        return raw
    if "://" not in raw:
        raw = "http://" + raw
    return raw


def get_proxy_dict() -> dict[str, str] | None:
    """获取 aiohttp 兼容的代理字典"""
    proxy_url = get_proxy()
    if proxy_url:
        return {"http": proxy_url, "https": proxy_url}
    return None
