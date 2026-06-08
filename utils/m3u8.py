"""m3u8 播放列表解析工具"""

import re
import aiohttp
from dataclasses import dataclass, field
from typing import Optional
from utils.helpers import resolve_url


@dataclass
class M3u8Segment:
    url: str
    duration: float = 0.0
    title: str = ""
    sequence: int = 0
    key: Optional[dict] = None  # {"method": "AES-128", "uri": "...", "iv": "..."}


@dataclass
class M3u8Playlist:
    url: str
    segments: list[M3u8Segment] = field(default_factory=list)
    target_duration: int = 0
    media_sequence: int = 0
    is_master: bool = False
    variants: list[dict] = field(default_factory=list)  # 多码率列表
    keys: dict = field(default_factory=dict)             # segment_index -> key

    @property
    def total_duration(self) -> float:
        return sum(s.duration for s in self.segments)

    @property
    def segment_count(self) -> int:
        return len(self.segments)


async def fetch_m3u8(url: str, headers: dict = None) -> str:
    """获取 m3u8 文件内容"""
    _headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    }
    if headers:
        _headers.update(headers)
    from utils.helpers import get_proxy
    proxy = get_proxy()
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=_headers, proxy=proxy,
                               timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                raw = await resp.read()
                raise Exception(f"HTTP {resp.status}: {raw[:200]!r}")
            return await resp.text()


def parse_m3u8(content: str, base_url: str = "") -> M3u8Playlist:
    """解析 m3u8 内容"""
    playlist = M3u8Playlist(url=base_url)
    lines = content.strip().splitlines()
    current_key = None
    seq = 0

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if line.startswith("#EXT-X-STREAM-INF"):
            playlist.is_master = True
            variant = _parse_variant(line)
            if i + 1 < len(lines):
                variant["url"] = resolve_url(base_url, lines[i + 1].strip())
            playlist.variants.append(variant)
            i += 2
            continue

        if line.startswith("#EXT-X-TARGETDURATION"):
            try:
                playlist.target_duration = int(line.split(":")[1])
            except (ValueError, IndexError):
                pass

        elif line.startswith("#EXT-X-MEDIA-SEQUENCE"):
            try:
                playlist.media_sequence = int(line.split(":")[1])
            except (ValueError, IndexError):
                pass

        elif line.startswith("#EXT-X-KEY"):
            current_key = _parse_key(line)

        elif line.startswith("#EXTINF"):
            duration = 0.0
            title = ""
            dur_match = re.match(r"#EXTINF:([\d.]+),?(.*)", line)
            if dur_match:
                duration = float(dur_match.group(1))
                title = dur_match.group(2).strip()
            if i + 1 < len(lines):
                seg_url = resolve_url(base_url, lines[i + 1].strip())
                seg = M3u8Segment(
                    url=seg_url,
                    duration=duration,
                    title=title,
                    sequence=seq,
                    key=dict(current_key) if current_key else None,
                )
                playlist.segments.append(seg)
                seq += 1
            i += 2
            continue

        i += 1

    return playlist


def _parse_key(line: str) -> Optional[dict]:
    """解析 #EXT-X-KEY 标签"""
    key_info = {}
    method_match = re.search(r"METHOD=([\w-]+)", line)
    if method_match:
        key_info["method"] = method_match.group(1)
    uri_match = re.search(r'URI="([^"]*)"', line)
    if uri_match:
        key_info["uri"] = uri_match.group(1)
    iv_match = re.search(r"IV=0x([0-9a-fA-F]+)", line)
    if iv_match:
        key_info["iv"] = iv_match.group(1)
    return key_info if key_info else None


def _parse_variant(line: str) -> dict:
    """解析 #EXT-X-STREAM-INF 变体"""
    info = {}
    bw_match = re.search(r"BANDWIDTH=(\d+)", line)
    if bw_match:
        info["bandwidth"] = int(bw_match.group(1))
    res_match = re.search(r"RESOLUTION=(\d+x\d+)", line)
    if res_match:
        info["resolution"] = res_match.group(1)
    codec_match = re.search(r'CODECS="([^"]*)"', line)
    if codec_match:
        info["codecs"] = codec_match.group(1)
    return info


def select_best_quality(variants: list[dict]) -> Optional[dict]:
    """从多码率列表中选择最高质量"""
    if not variants:
        return None
    return max(variants, key=lambda v: v.get("bandwidth", 0))
