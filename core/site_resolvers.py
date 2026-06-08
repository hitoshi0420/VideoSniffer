"""各网站专属解析器 — 从页面提取真实视频地址"""

import re
import asyncio
import aiohttp
from typing import Optional
from dataclasses import dataclass

from utils.helpers import get_proxy
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SiteVideoInfo:
    url: str
    title: str = ""
    quality_urls: dict = None       # {"video": url, "audio": url} 用于 DASH 格式
    quality_options: list = None    # [{"label": "1080P", "url": "...", "height": 1080, ...}]
    headers: dict = None
    quality: str = ""               # 默认画质描述，如 "1080P"
    warning: str = ""               # 警告信息，如 "VIP限定仅3分钟试看"


# ============ MissAV 解析器 ============

MISSAV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


async def resolve_missav(url: str) -> Optional[SiteVideoInfo]:
    """从 MissAV 页面提取视频 m3u8 地址"""
    try:
        html = await _fetch(url, MISSAV_HEADERS)

        # 提取视频标题
        title = ""
        title_match = re.search(r'<title>(.*?)(?:\s*-+\s*MissAV|</title)', html, re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()
        if not title:
            title_match = re.search(r'<meta\s+name="description"\s+content="([^"]+)"', html, re.IGNORECASE)
            if title_match:
                title = title_match.group(1)[:80]

        # 提取混淆的 JS 数组
        array_match = re.search(r"'([^']*m3u8[^']*)'\.split\('\|'\)", html)
        if not array_match:
            return None

        parts = array_match.group(1).split('|')
        if len(parts) < 16:
            return None

        # 构建 base-36 替换映射
        def to_base36(n):
            d = "0123456789abcdefghijklmnopqrstuvwxyz"
            if n == 0:
                return "0"
            result = ""
            while n > 0:
                result = d[n % 36] + result
                n //= 36
            return result

        sub_map = {to_base36(i): p for i, p in enumerate(parts)}

        # MissAV 的 JS 模板固定为三种画质的 URL
        template = (
            "f='8://7.6/5-4-3-2-1/e.0';"
            "d='8://7.6/5-4-3-2-1/c/9.0';"
            "b='8://7.6/5-4-3-2-1/a/9.0';"
        )

        # 逐字符安全解码（只替换模板中的占位符，不递归替换结果）
        result = []
        i = 0
        while i < len(template):
            c = template[i]
            # 只有独立字符才是占位符（前后不是字母数字）
            prev_ok = i == 0 or not template[i - 1].isalnum()
            next_ok = i + 1 >= len(template) or not template[i + 1].isalnum()
            if c in sub_map and prev_ok and next_ok:
                result.append(sub_map[c])
            else:
                result.append(c)
            i += 1
        decoded = "".join(result)

        # 提取所有 m3u8 URL
        m3u8_urls = re.findall(r"https?://[^'\";]+?\.m3u8", decoded)

        # 按画质排序（优先高画质）
        quality_order = ["1080p", "720p", "480p"]
        selected_url = None
        for q in quality_order:
            for u in m3u8_urls:
                if q in u:
                    selected_url = u
                    break
            if selected_url:
                break
        if not selected_url and m3u8_urls:
            selected_url = m3u8_urls[0]

        if not selected_url:
            # 回退: 手动构造 playlists
            reversed_hashes = [parts[5], parts[4], parts[3], parts[2], parts[1]]
            hash_path = "-".join(reversed_hashes)
            domain = f"{parts[7]}.{parts[6]}"
            selected_url = f"https://{domain}/{hash_path}/1080p/video.m3u8"

        # 代理替换
        proxy_url = re.sub(
            r'https://([a-zA-Z0-9.-]+)/',
            r'https://www.missav1.vip/jmpres/\1/',
            selected_url
        )

        info = SiteVideoInfo(
            url=proxy_url,
            title=title,
            headers={
                "User-Agent": MISSAV_HEADERS["User-Agent"],
                "Referer": "https://www.missav1.vip/",
                "Origin": "https://www.missav1.vip",
            }
        )
        return info

    except Exception as e:
        logger.warning(f"MissAV 解析失败: {e}")
        return None


async def _fetch(url: str, headers: dict) -> str:
    import ssl as _ssl
    connector = aiohttp.TCPConnector(ssl=_ssl.create_default_context())
    proxy = get_proxy()
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.get(url, headers=headers, proxy=proxy,
                               timeout=aiohttp.ClientTimeout(total=30)) as resp:
            return await resp.text()


# ============ Bilibili 解析器 ============

BILIBILI_BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}

# B站画质 qn 值与描述映射
BILI_QN_MAP = {
    127: "8K",
    125: "4K HDR",
    120: "4K",
    116: "1080P 60帧",
    112: "1080P+",
    80: "1080P",
    74: "720P 60帧",
    64: "720P",
    48: "720P",
    32: "480P",
    16: "360P",
    6: "240P",
}


async def resolve_bilibili(url: str) -> Optional[SiteVideoInfo]:
    """从 B站页面/API 提取视频 DASH 地址"""
    import re
    try:
        # 读取 Cookie（登录后可获取更高画质）
        from config import settings as cfg
        cookie = cfg.get("site_cookies.bilibili", "")
        headers = dict(BILIBILI_BASE_HEADERS)
        if cookie:
            # 自动补全格式：如果用户只贴了值不含等号，加上 SESSDATA=
            if "=" not in cookie:
                cookie = f"SESSDATA={cookie}"
            headers["Cookie"] = cookie

        # 提取 ID
        ep_match = re.search(r'ep(\d+)', url)
        ss_match = re.search(r'ss(\d+)', url)
        bv_match = re.search(r'BV[\w]+', url)
        av_match = re.search(r'av(\d+)', url, re.IGNORECASE)

        title = ""
        video_url = ""
        audio_url = ""
        quality_label = ""
        play_resp_data = {}

        if ep_match:
            ep_id = ep_match.group(1)
            api_url = f"https://api.bilibili.com/pgc/player/web/playurl?ep_id={ep_id}&qn=127&fnval=4048&fourk=1"
            resp = await _fetch_json(api_url, headers)
            if resp.get("code") == 0:
                play_resp_data = resp["result"]
                title = play_resp_data.get("title", "")
                video_url, audio_url = _extract_best_dash(play_resp_data)
                quality_label = _extract_quality(play_resp_data)
                if not title:
                    sinfo = await _fetch_json(
                        f"https://api.bilibili.com/pgc/view/web/season?episode_id={ep_id}",
                        headers
                    )
                    if sinfo.get("code") == 0:
                        title = sinfo["result"].get("title", "")
            else:
                return None

        elif ss_match:
            ss_id = ss_match.group(1)
            sinfo = await _fetch_json(
                f"https://api.bilibili.com/pgc/view/web/season?season_id={ss_id}",
                headers
            )
            if sinfo.get("code") == 0:
                title = sinfo["result"]["title"]
                episodes = sinfo["result"].get("episodes", [])
                if episodes:
                    ep_id = episodes[0]["id"]
                    play_resp = await _fetch_json(
                        f"https://api.bilibili.com/pgc/player/web/playurl?ep_id={ep_id}&qn=127&fnval=4048&fourk=1",
                        headers
                    )
                    if play_resp.get("code") == 0:
                        play_resp_data = play_resp["result"]
                        video_url, audio_url = _extract_best_dash(play_resp_data)
                        quality_label = _extract_quality(play_resp_data)
            else:
                return None

        elif bv_match:
            bvid = bv_match.group(0)
            vinfo = await _fetch_json(
                f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
                headers
            )
            if vinfo.get("code") == 0:
                title = vinfo["data"]["title"]
                cid = vinfo["data"]["cid"]
                presp = await _fetch_json(
                    f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&qn=127&fnval=4048&fourk=1",
                    headers
                )
                if presp.get("code") == 0:
                    play_resp_data = presp["data"]
                    video_url, audio_url = _extract_best_dash(play_resp_data)
                    quality_label = _extract_quality(play_resp_data)

        elif av_match:
            avid = av_match.group(1)
            vinfo = await _fetch_json(
                f"https://api.bilibili.com/x/web-interface/view?aid={avid}",
                headers
            )
            if vinfo.get("code") == 0:
                title = vinfo["data"]["title"]
                cid = vinfo["data"]["cid"]
                presp = await _fetch_json(
                    f"https://api.bilibili.com/x/player/playurl?aid={avid}&cid={cid}&qn=127&fnval=4048&fourk=1",
                    headers
                )
                if presp.get("code") == 0:
                    play_resp_data = presp["data"]
                    video_url, audio_url = _extract_best_dash(play_resp_data)
                    quality_label = _extract_quality(play_resp_data)

        if not video_url:
            return None

        # 构建警告信息
        warning = ""
        if play_resp_data:
            vip_type = play_resp_data.get("vip_type", 0)
            vip_status = play_resp_data.get("vip_status", 0)
            is_preview = play_resp_data.get("is_preview", 0)
            if is_preview and vip_type == 1 and vip_status == 0:
                duration_ms = play_resp_data.get("timelength", 0)
                preview_min = (play_resp_data.get("durl", [{"length":0}])[0].get("length", 0)) / 1000 / 60
                warning = f"大会员限定，非会员仅{preview_min:.0f}分钟试看（完整{duration_ms/1000/60:.0f}分钟）"

        # 下载请求头（带 Referer/Origin/Cookie）
        dl_headers = {
            "User-Agent": BILIBILI_BASE_HEADERS["User-Agent"],
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
        }
        if cookie:
            dl_headers["Cookie"] = cookie  # cookie 变量已在上方补全了 SESSDATA= 前缀

        return SiteVideoInfo(
            url=video_url,
            title=title,
            headers=dl_headers,
            quality_urls={"video": video_url, "audio": audio_url},
            quality=quality_label,
            warning=warning,
        )

    except Exception as e:
        logger.warning(f"B站解析失败: {e}")
        return None


def _extract_best_dash(result: dict) -> tuple:
    """从 DASH 数据中提取最高画质视频 + 最高音质音频"""
    dash = result.get("dash")
    if not dash:
        durl = result.get("durl")
        if durl:
            return durl[0]["url"], ""
        return "", ""

    videos = dash.get("video", [])
    audios = dash.get("audio", [])

    # 选最高分辨率 H.264 视频（avc 编码兼容性最好）
    avc_videos = [v for v in videos if "avc" in v.get("codecs", "")]
    best_video = (avc_videos or videos)[0] if videos else None

    # 选最高码率音频
    best_audio = sorted(audios, key=lambda a: a.get("bandwidth", 0), reverse=True)
    best_audio = best_audio[0] if best_audio else None

    video_url = best_video.get("baseUrl") or best_video.get("base_url", "") if best_video else ""
    audio_url = best_audio.get("baseUrl") or best_audio.get("base_url", "") if best_audio else ""

    return video_url, audio_url


def _extract_quality(result: dict) -> str:
    """从 API 返回结果提取实际画质描述"""
    qn = result.get("quality", 0)
    return BILI_QN_MAP.get(qn, f"qn={qn}")


async def _fetch_json(url: str, headers: dict) -> dict:
    import requests as req
    import asyncio
    def _get():
        r = req.get(url, headers=headers, timeout=15)
        return r.json()
    return await asyncio.get_event_loop().run_in_executor(None, _get)


# ============ 腾讯视频解析器 ============

TENCENT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Referer": "https://v.qq.com/",
}


async def resolve_tencent(url: str) -> Optional[SiteVideoInfo]:
    """从腾讯视频页面提取视频地址"""
    import re
    try:
        # 提取 vid
        vid_match = re.search(r'/(\w+)\.html', url)
        if not vid_match:
            return None
        vid = vid_match.group(1)

        # 调用 getinfo API
        api_url = "https://h5vv6.video.qq.com/getinfo"
        params = {
            "vid": vid, "platform": "11001", "charge": "0",
            "otype": "json", "defn": "shd", "defnpayver": "1",
            "sphls": "1", "sphttps": "1", "dtype": "3",
        }

        import requests as req
        def _get():
            r = req.get(api_url, params=params, headers=TENCENT_HEADERS, timeout=15)
            text = r.text
            # 处理 QZOutputJson 包装
            if "QZOutputJson" in text:
                text = re.sub(r'^QZOutputJson\s*=\s*', '', text)
                text = re.sub(r';\s*$', '', text)
            import json
            return json.loads(text)

        data = await asyncio.get_event_loop().run_in_executor(None, _get)

        # 提取视频信息
        vi_list = data.get("vl", {}).get("vi", [])
        if not vi_list:
            return None
        vi = vi_list[0]

        title = vi.get("ti", vid)
        duration_sec = float(vi.get("td", 0))
        preview = data.get("preview", 0)

        # 检测预览/VIP 限制
        warning = ""
        if preview > 0:
            preview_min = preview / 60
            total_min = duration_sec / 60
            warning = f"VIP限定，非会员仅{preview_min:.0f}分钟试看（完整{total_min:.0f}分钟）"

        # 提取流 URL（选最高画质）
        ul = vi.get("ul", {}).get("ui", [])
        if not ul:
            return None

        # 按画质排序（优先高画质）
        quality_order = ["shd", "fhd", "hd", "sd"]
        best_url = ""
        best_quality = ""
        # fl.fi 中有画质信息
        fl_info = data.get("fl", {}).get("fi", [])
        # 找到当前请求的画质对应的 URL
        # ul 中的 URL 按画质顺序排列，通常最后一个最高画质
        # 取最后一个（最高画质）
        best_entry = ul[-1] if ul else {}
        best_url = best_entry.get("url", "")
        # 从 fl_info 匹配画质名
        for fi in fl_info:
            if fi.get("name") == data.get("format", "shd"):
                best_quality = fi.get("resolution", "")
                break
        if not best_quality:
            best_quality = data.get("format", "")

        if not best_url:
            return None

        dl_headers = {
            "User-Agent": TENCENT_HEADERS["User-Agent"],
            "Referer": "https://v.qq.com/",
        }

        return SiteVideoInfo(
            url=best_url,
            title=title,
            headers=dl_headers,
            quality=best_quality,
            warning=warning,
        )

    except Exception as e:
        logger.warning(f"腾讯视频解析失败: {e}")
        return None


# ============ 麻豆社 (madou.club) 解析器 ============

MADOU_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


async def resolve_madou(url: str) -> Optional[SiteVideoInfo]:
    """从麻豆社页面提取视频 m3u8 地址"""
    try:
        # 第一步：从页面提取 iframe（dash.madou.club/share/ID）
        html = await _fetch(url, MADOU_HEADERS)
        iframe_match = re.search(r'<iframe[^>]+src\s*=\s*["\']?(https?://dash\.madou\.club/share/\w+)', html)
        if not iframe_match:
            # 也尝试匹配相对路径
            iframe_match = re.search(r'<iframe[^>]+src\s*=\s*["\']?(//dash\.madou\.club/share/\w+)', html)
            if not iframe_match:
                logger.warning(f"麻豆社: 未找到 iframe dash 链接")
                return None

        dash_url = iframe_match.group(1)
        if dash_url.startswith("//"):
            dash_url = "https:" + dash_url

        # 提取标题
        title = ""
        title_match = re.search(r'<title>(.*?)(?:\s*[-|]\s*麻豆社|</title>)', html, re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()

        # 第二步：从 dash 播放器页面提取 m3u8 地址和 token
        dash_html = await _fetch(dash_url, {
            **MADOU_HEADERS,
            "Referer": url,
        })

        m3u8_path = ""
        token = ""

        m3u8_match = re.search(r"""m3u8\s*=\s*['"]([^'"]+)['"]""", dash_html)
        if m3u8_match:
            m3u8_path = m3u8_match.group(1)

        token_match = re.search(r"""token\s*=\s*['"]([^'"]*)['"]""", dash_html)
        if token_match:
            token = token_match.group(1)

        if not m3u8_path:
            logger.warning(f"麻豆社: dash 页面未找到 m3u8 路径")
            return None

        # 构建完整 m3u8 URL
        if m3u8_path.startswith("/"):
            m3u8_url = f"https://dash.madou.club{m3u8_path}"
        else:
            m3u8_url = m3u8_path

        if token:
            m3u8_url = f"{m3u8_url}?token={token}"

        # 下载请求头（Referer 必须是 dash.madou.club）
        dl_headers = {
            "User-Agent": MADOU_HEADERS["User-Agent"],
            "Referer": "https://dash.madou.club/",
            "Origin": "https://dash.madou.club",
        }

        if not title:
            title_match2 = re.search(r'<title>(.*?)</title>', dash_html, re.IGNORECASE)
            if title_match2:
                title = title_match2.group(1).replace(".mp4在线播放", "").strip()

        logger.info(f"麻豆社解析成功: {title or '未知'}, m3u8={m3u8_path}")
        return SiteVideoInfo(
            url=m3u8_url,
            title=title,
            headers=dl_headers,
            quality="",
        )

    except Exception as e:
        logger.warning(f"麻豆社解析失败: {e}")
        return None


# ============ YouTube 解析器 (yt-dlp) ============

async def resolve_youtube(url: str) -> Optional[SiteVideoInfo]:
    """使用 yt-dlp 提取 YouTube 视频地址（DASH 分离 + 合并流，返回多画质选项）"""
    import asyncio as _asyncio
    try:
        def _extract():
            import yt_dlp
            opts = {"quiet": True, "no_warnings": True}
            proxy = get_proxy()
            if proxy:
                opts["proxy"] = proxy
            with yt_dlp.YoutubeDL(opts) as ydl:
                full = ydl.extract_info(url, download=False)
                formats = full.get("formats", [])

                # 找最佳纯音频流（最高码率，所有画质共用）
                best_audio_url = ""
                best_audio_br = 0
                for f in formats:
                    if f.get("acodec") != "none" and f.get("vcodec") == "none":
                        br = f.get("abr") or f.get("tbr") or 0
                        if br > best_audio_br:
                            best_audio_br = br
                            best_audio_url = f.get("url", "")

                # 找最佳合并流（含音频，作为低画质兜底）
                combined_formats = []
                seen_combined = set()
                for f in formats:
                    url_direct = f.get("url", "")
                    if not url_direct:
                        continue
                    if f.get("ext") != "mp4" or f.get("acodec") == "none":
                        continue
                    height = f.get("height") or 0
                    if height == 0:
                        continue
                    key = str(height)
                    if key in seen_combined:
                        continue
                    seen_combined.add(key)
                    label = f"{height}P (合并)"
                    combined_formats.append({
                        "label": label, "url": url_direct,
                        "height": height, "audio_url": "",
                        "filesize": f.get("filesize") or f.get("filesize_approx", 0),
                        "dash": False,
                    })

                # 找纯视频 MP4 流，与最佳音频配对成 DASH 选项
                dash_formats = []
                seen_dash = set()
                for f in formats:
                    url_direct = f.get("url", "")
                    if not url_direct:
                        continue
                    if f.get("ext") != "mp4" or f.get("acodec") != "none":
                        continue
                    height = f.get("height") or 0
                    if height == 0:
                        continue
                    key = str(height)
                    if key in seen_dash:
                        continue
                    seen_dash.add(key)
                    label = f"{height}P"
                    if f.get("fps", 0) >= 50:
                        label = f"{height}P {f['fps']}fps"
                    video_size = f.get("filesize") or f.get("filesize_approx", 0)
                    dash_formats.append({
                        "label": label,
                        "video_url": url_direct,
                        "audio_url": best_audio_url,
                        "height": height,
                        "filesize": video_size,
                        "dash": True,
                    })

                # 合并：DASH 优先（高清），合并流兜底
                all_formats = sorted(dash_formats, key=lambda x: x["height"], reverse=True)
                # 把低画质合并流追加到末尾（如果 DASH 已有该分辨率则跳过）
                dash_heights = {f["height"] for f in all_formats}
                for cf in sorted(combined_formats, key=lambda x: x["height"], reverse=True):
                    if cf["height"] not in dash_heights:
                        all_formats.append(cf)

                default = all_formats[0] if all_formats else None
                # 默认用第一个格式的 URL（DASH 用 video_url，合并用 url）
                if default:
                    default_url = default.get("video_url") or default["url"]
                    format_note = default["label"]
                else:
                    default_url = full.get("url") or ""
                    format_note = ""

                return {
                    "title": full.get("title", ""),
                    "url": default_url,
                    "audio_url": all_formats[0].get("audio_url", "") if all_formats else "",
                    "quality_options": all_formats,
                    "format_note": format_note,
                }

        info = await _asyncio.get_event_loop().run_in_executor(None, _extract)

        title = info["title"]
        video_url = info["url"]
        if not video_url:
            return None

        dl_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.youtube.com/",
        }

        logger.info(f"YouTube 解析成功: {title}, 画质选项: {len(info['quality_options'])}")
        return SiteVideoInfo(
            url=video_url,
            title=title,
            headers=dl_headers,
            quality=info.get("format_note", ""),
            quality_options=info["quality_options"],
        )
    except Exception as e:
        logger.warning(f"YouTube 解析失败: {e}")
        return None


SITE_RESOLVERS = {
    "missav": resolve_missav,
    "missav1.vip": resolve_missav,
    "bilibili": resolve_bilibili,
    "bilibili.com": resolve_bilibili,
    "v.qq.com": resolve_tencent,
    "v.qq": resolve_tencent,
    "madou.club": resolve_madou,
    "dash.madou.club": resolve_madou,
    "youtube.com": resolve_youtube,
    "youtu.be": resolve_youtube,
}


def get_resolver(url: str):
    """根据 URL 域名返回对应的解析器"""
    for domain, resolver in SITE_RESOLVERS.items():
        if domain in url.lower():
            return resolver
    return None
