"""视频地址解析模块"""

import re
import asyncio
import aiohttp
from urllib.parse import urlparse
from dataclasses import dataclass, field

from models.task import VideoType
from utils.m3u8 import fetch_m3u8, parse_m3u8, select_best_quality, M3u8Playlist
from core.site_resolvers import get_resolver


@dataclass
class VideoInfo:
    """视频解析结果"""
    url: str
    title: str = ""
    video_type: VideoType = VideoType.DIRECT
    duration: float = 0.0
    quality: str = ""
    size: int = 0
    segments: list = field(default_factory=list)
    headers: dict = field(default_factory=dict)
    playlist: M3u8Playlist = None
    audio_url: str = ""          # B站等 DASH 格式的音频地址
    video_format: str = ""       # "m4s" / "ts" 等
    quality_options: list = field(default_factory=list)  # [{"label": "1080P", "url": "...", "audio_url": "..."}]
    error: str = ""
    warning: str = ""            # 非致命警告，如 VIP试看提示


VIDEO_EXTS = {".mp4", ".m3u8", ".ts", ".flv", ".webm", ".avi", ".mkv", ".mov", ".m4v", ".mpd"}


async def parse_url(url: str, headers: dict = None) -> VideoInfo:
    """智能解析视频 URL"""
    if not url or not url.startswith(("http://", "https://")):
        return VideoInfo(url=url, error="无效的 URL")

    _headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": url,
    }
    if headers:
        _headers.update(headers)

    path = urlparse(url).path.lower()

    # 站点专属解析器（优先级最高）
    resolver = get_resolver(url)
    if resolver:
        site_info = await resolver(url)
        if site_info and site_info.url:
            # DASH 格式（B站等）：直接构建 VideoInfo
            if site_info.quality_urls and site_info.quality_urls.get("audio"):
                info = VideoInfo(
                    url=site_info.url,
                    title=site_info.title,
                    video_type=VideoType.DIRECT,
                    quality=site_info.quality or "",
                    audio_url=site_info.quality_urls.get("audio", ""),
                    video_format="m4s",
                    headers=site_info.headers or {},
                    warning=site_info.warning or "",
                )
                return info
            # m3u8 流（MissAV 等）
            if ".m3u8" in site_info.url:
                info = await _parse_m3u8(site_info.url, {**_headers, **(site_info.headers or {})},
                                         title=site_info.title)
                info.warning = site_info.warning or ""
                return info
            # 其他直接 URL（含 YouTube 等多画质场景）
            # 检查是否 DASH 格式（quality_options 含 video_url/audio_url）
            first_opt = site_info.quality_options[0] if site_info.quality_options else {}
            is_dash = first_opt.get("dash") and first_opt.get("audio_url")
            info = VideoInfo(
                url=first_opt.get("video_url") if is_dash else site_info.url,
                title=site_info.title,
                video_type=VideoType.DIRECT,
                quality=site_info.quality or "",
                headers=site_info.headers or {},
                warning=site_info.warning or "",
                quality_options=site_info.quality_options or [],
                audio_url=first_opt.get("audio_url", "") if is_dash else "",
                video_format="m4s" if is_dash else "",
            )
            return info

    # m3u8 流
    if path.endswith(".m3u8") or "m3u8" in url.lower():
        return await _parse_m3u8(url, _headers)

    # MPD (DASH)
    if path.endswith(".mpd") or "mpd" in url.lower():
        return await _parse_mpd(url, _headers)

    # 直链
    if any(path.endswith(ext) for ext in VIDEO_EXTS):
        info = await _parse_direct(url, _headers)
        info.video_type = VideoType.DIRECT
        return info

    # 尝试 HEAD 请求探测
    return await _probe_url(url, _headers)


async def _parse_direct(url: str, headers: dict) -> VideoInfo:
    """解析直链视频"""
    info = VideoInfo(url=url, video_type=VideoType.DIRECT)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                info.size = int(resp.headers.get("Content-Length", 0))
                content_type = resp.headers.get("Content-Type", "")
                if "video/" in content_type:
                    pass
    except Exception:
        pass
    path = urlparse(url).path
    info.title = path.rsplit("/", 1)[-1].split("?")[0]
    return info


async def _parse_m3u8(url: str, headers: dict, title: str = "") -> VideoInfo:
    """解析 m3u8 播放列表"""
    info = VideoInfo(url=url, video_type=VideoType.M3U8, title=title)
    try:
        content = await fetch_m3u8(url, headers)
        playlist = parse_m3u8(content, url)
        info.playlist = playlist

        if playlist.is_master and playlist.variants:
            best = select_best_quality(playlist.variants)
            if best and "url" in best:
                info.quality = best.get("resolution", "")
                # 递归解析最佳质量的子播放列表
                content = await fetch_m3u8(best["url"], headers)
                playlist = parse_m3u8(content, best["url"])
                info.playlist = playlist

        info.segments = [{"url": s.url, "duration": s.duration, "key": s.key} for s in playlist.segments]
        info.duration = playlist.total_duration

        # 估计总大小
        if playlist.segments:
            avg_size = 500_000  # 假设每个分片约 500KB
            info.size = len(playlist.segments) * avg_size

        # 生成标题（保留已有的标题）
        if not info.title:
            path_parts = urlparse(url).path.rsplit("/", 1)[-1]
            info.title = path_parts.replace(".m3u8", "") or "m3u8_video"

    except Exception as e:
        info.error = f"m3u8 解析失败: {e}"

    return info


async def _parse_mpd(url: str, headers: dict) -> VideoInfo:
    """解析 MPD (DASH) 播放列表"""
    info = VideoInfo(url=url, video_type=VideoType.MPD)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                content = await resp.text()
        info.title = _extract_mpd_title(content) or urlparse(url).path.rsplit("/", 1)[-1]
        info.segments = _parse_mpd_segments(content, url)
    except Exception as e:
        info.error = f"MPD 解析失败: {e}"
    return info


async def _probe_url(url: str, headers: dict) -> VideoInfo:
    """探测未知 URL"""
    info = VideoInfo(url=url)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10), allow_redirects=True) as resp:
                content_type = resp.headers.get("Content-Type", "")
                if content_type.startswith("video/") or content_type == "application/vnd.apple.mpegurl":
                    ext_map = {
                        "video/mp4": ".mp4",
                        "video/webm": ".webm",
                        "application/vnd.apple.mpegurl": ".m3u8",
                    }
                    ext = ext_map.get(content_type.split(";")[0], "")
                    info.video_type = VideoType.M3U8 if ext == ".m3u8" else VideoType.DIRECT
                    info.size = int(resp.headers.get("Content-Length", 0))
                    info.title = f"video{ext}"
                else:
                    info.error = f"无法识别视频类型: {content_type}"
    except Exception as e:
        info.error = f"探测失败: {e}"
    return info


def _extract_mpd_title(xml_content: str) -> str:
    m = re.search(r'<title[^>]*>(.*?)</title>', xml_content, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _parse_mpd_segments(xml_content: str, base_url: str) -> list:
    """从 MPD XML 提取分段 URL（简化实现）"""
    segments = []
    init_match = re.search(r'initialization="([^"]*)"', xml_content)
    media_match = re.search(r'media="([^"]*)"', xml_content)
    timeline_pattern = re.findall(r'<S\s[^>]*t="(\d+)"[^>]*d="(\d+)"', xml_content)

    if media_match:
        template = media_match.group(1)
        for i, (t, d) in enumerate(timeline_pattern):
            seg_url = template.replace("$Time$", t).replace("$Number$", str(i + 1))
            if not seg_url.startswith("http"):
                from utils.helpers import resolve_url
                seg_url = resolve_url(base_url, seg_url)
            segments.append({"url": seg_url, "duration": float(d)})

    return segments
