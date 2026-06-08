"""AI 智能找片模块 — DeepSeek + 网络搜索"""

import json
import re
import asyncio
from dataclasses import dataclass, field
from typing import Optional

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SearchResult:
    """搜索结果"""
    title: str
    url: str
    platform: str = ""        # "bilibili" / "v.qq.com" / "other"
    confidence: float = 0.0   # 0.0 - 1.0
    reason: str = ""          # AI 的判断理由
    parse_ok: bool = False    # 是否已通过解析验证
    parse_error: str = ""


def _get_client():
    """获取 DeepSeek OpenAI 兼容客户端"""
    from openai import OpenAI
    import httpx
    api_key = settings.get("ai.api_key", "")
    base_url = settings.get("ai.base_url", "https://api.deepseek.com/v1")
    if not api_key:
        return None
    timeout = httpx.Timeout(30.0, connect=10.0)
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)


def web_search(query: str, max_results: int = 10) -> list[dict]:
    """DuckDuckGo 网络搜索"""
    try:
        DDGS = None
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        if DDGS is None:
            return []
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
        return results
    except ImportError:
        return []
    except Exception as e:
        logger.warning(f"DDG 搜索异常: {e}")
        return []


def _extract_urls_from_text(text: str) -> list[str]:
    """从 AI 返回的文本中提取所有 URL"""
    urls = re.findall(r'https?://[^\s\'"<>]+', text)
    return urls


def ai_search_urls(query: str) -> list[dict]:
    """直接让 DeepSeek 从训练知识中返回视频 URL"""
    client = _get_client()
    if not client:
        return []

    model = settings.get("ai.model", "deepseek-chat")

    system_prompt = (
        "你是一个视频搜索引擎助手。用户会给你一个视频名称或关键词，你需要返回能在以下网站直接观看该视频的页面URL：\n"
        "- bilibili.com (B站)\n"
        "- v.qq.com (腾讯视频)\n"
        "- 其他中文视频网站\n\n"
        "要求：\n"
        "1. 只返回你确定存在的视频页面URL，不要猜测\n"
        "2. 优先返回B站的链接（bilibili.com/video/BVxxx 或 bilibili.com/bangumi/play/ssxxx）\n"
        "3. 每个URL一行，格式: 标题 | URL\n"
        "4. 如果找不到或不确定，返回: NONE\n"
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请找到以下视频的播放页面URL: {query}"},
            ],
            temperature=0.3,
            max_tokens=1000,
        )
        content = response.choices[0].message.content.strip()
        if content.upper().startswith("NONE"):
            return []

        results = []
        for line in content.split("\n"):
            line = line.strip()
            if not line:
                continue
            urls = _extract_urls_from_text(line)
            if urls:
                title_part = line.split("|")[0].strip() if "|" in line else line.split("http")[0].strip()
                title_part = re.sub(r'^\d+[\.\)、]\s*', '', title_part).strip()
                results.append({
                    "title": title_part or query,
                    "url": urls[0],
                    "platform": _guess_platform(urls[0]),
                    "confidence": 0.7,
                    "reason": "AI 知识库匹配",
                })
        return results
    except Exception as e:
        logger.warning(f"DeepSeek 搜索异常: {e}")
        return []


def ai_analyze_results(query: str, candidates: list[dict]) -> list[dict]:
    """让 DeepSeek 分析候选 URL，判断是否为真实视频页面"""
    if not candidates:
        return []

    client = _get_client()
    if not client:
        return []

    model = settings.get("ai.model", "deepseek-chat")

    # 构建候选列表给 AI
    candidate_text = ""
    for i, c in enumerate(candidates):
        candidate_text += f"[{i}] {c.get('title', '?')} | {c.get('url', '')}\n"

    system_prompt = (
        "你是一个视频链接分析助手。你需要分析给定的候选URL列表，判断每个URL是否是真实的视频播放页面（而非搜索列表页、首页或广告页）。\n\n"
        "判断标准：\n"
        "1. URL是否指向具体的视频播放页（如 bilibili.com/video/BVxxx, bilibili.com/bangumi/play/xxx, v.qq.com/x/cover/xxx/xxx.html）\n"
        "2. 标题是否与用户搜索相关\n"
        "3. 是否来自可用的视频平台\n\n"
        "返回JSON格式（不要包含其他文字）:\n"
        '[{"index": 候选编号, "title": "视频标题", "platform": "bilibili/vqq/other", "confidence": 0.0-1.0, "reason": "简短理由"}]\n\n'
        "只返回你确认为真实视频页面的结果，过滤掉列表页、搜索页、广告等非视频内容。confidence低于0.5的不要返回。"
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"用户搜索: {query}\n\n候选URL列表:\n{candidate_text}"},
            ],
            temperature=0.3,
            max_tokens=2000,
        )
        content = response.choices[0].message.content.strip()
        # 提取 JSON
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            analyzed = json.loads(json_match.group(0))
            results = []
            for item in analyzed:
                idx = item.get("index", 0)
                if 0 <= idx < len(candidates):
                    results.append({
                        **candidates[idx],
                        "title": item.get("title", candidates[idx].get("title", "")),
                        "platform": item.get("platform", ""),
                        "confidence": item.get("confidence", 0.5),
                        "reason": item.get("reason", ""),
                    })
            return results
        return []
    except Exception as e:
        logger.warning(f"DeepSeek 分析异常: {e}")
        return []


def _guess_platform(url: str) -> str:
    url_lower = url.lower()
    if "bilibili" in url_lower:
        return "bilibili"
    if "v.qq.com" in url_lower:
        return "vqq"
    if "missav" in url_lower:
        return "missav"
    if "youtube" in url_lower:
        return "youtube"
    return "other"


async def verify_url(url: str) -> Optional[dict]:
    """验证单个 URL 是否可解析（返回解析结果或 None）"""
    from core.parser import parse_url
    try:
        info = await parse_url(url)
        if info.error:
            return {"url": url, "ok": False, "error": info.error}
        return {
            "url": url,
            "ok": True,
            "title": info.title,
            "quality": info.quality,
            "video_type": info.video_type.value,
            "warning": info.warning,
        }
    except Exception as e:
        return {"url": url, "ok": False, "error": str(e)}


async def search_video(query: str, on_progress=None) -> list[dict]:
    """主入口：AI 搜索视频

    on_progress(step_name: str, detail: str) — 进度回调
    返回: [{title, url, platform, confidence, reason, verified, ...}]
    """
    def progress(step, detail=""):
        if on_progress:
            on_progress(step, detail)

    all_candidates = []

    # A路: DeepSeek 直接搜索（基于训练知识）
    progress("AI 知识库搜索", f"DeepSeek 搜索: {query}")
    ai_urls = ai_search_urls(query)
    if ai_urls:
        progress("AI 知识库搜索", f"DeepSeek 返回 {len(ai_urls)} 个候选")
    all_candidates.extend(ai_urls)

    # B路: DuckDuckGo 网络搜索（分平台搜索）
    search_queries = [
        f"{query} bilibili",
        f"{query} v.qq.com",
        f"{query} 在线播放",
    ]
    for sq in search_queries:
        progress("网络搜索", sq)
        web_results = web_search(sq, max_results=5)
        for wr in web_results:
            if wr.get("url"):
                all_candidates.append({
                    "title": wr.get("title", ""),
                    "url": wr.get("url", ""),
                    "platform": _guess_platform(wr.get("url", "")),
                    "confidence": 0.3,
                    "reason": f"搜索引擎: {wr.get('snippet', '')[:80]}",
                })

    # 去重
    seen = set()
    unique = []
    for c in all_candidates:
        if c["url"] not in seen:
            seen.add(c["url"])
            unique.append(c)
    all_candidates = unique
    progress("去重合并", f"共 {len(all_candidates)} 个候选 URL")

    # AI 分析筛选
    if all_candidates:
        progress("AI 分析", f"分析 {len(all_candidates)} 个候选...")
        analyzed = ai_analyze_results(query, all_candidates)
        progress("AI 分析", f"筛选出 {len(analyzed)} 个有效视频")
    else:
        analyzed = []
        progress("搜索结束", "未找到候选 URL")

    # 按置信度排序
    analyzed.sort(key=lambda x: x.get("confidence", 0), reverse=True)

    return analyzed[:10]
