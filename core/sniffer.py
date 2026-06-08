"""网络嗅探模块 — 基于 mitmproxy 的中间人代理"""

import re
import threading
import asyncio
from typing import Callable, Optional
from urllib.parse import urlparse

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)


class VideoSnifferAddon:
    """mitmproxy 插件 — 拦截并识别视频请求"""

    def __init__(self, callback: Callable[[str, dict], None]):
        self.callback = callback  # (url, headers) -> None
        self._filter_domains: list[str] = []
        self._filter_extensions: set[str] = set()
        self._filter_keywords: list[str] = []
        self._reload_filters()

    def _reload_filters(self):
        s = settings.load()
        self._filter_domains = s.get("sniffer", {}).get("filter_domains", [])
        self._filter_extensions = set(s.get("sniffer", {}).get("filter_extensions", []))
        self._filter_keywords = s.get("sniffer", {}).get("filter_keywords", [])

    def request(self, flow):
        """拦截 HTTP 请求"""
        url = flow.request.pretty_url
        if self._match(url):
            headers = dict(flow.request.headers)
            self.callback(url, headers)

    def response(self, flow):
        """拦截 HTTP 响应"""
        url = flow.request.pretty_url
        content_type = flow.response.headers.get("Content-Type", "")

        # 检查响应 Content-Type
        if "video/" in content_type or content_type == "application/vnd.apple.mpegurl":
            headers = dict(flow.request.headers)
            self.callback(url, headers)
            return

        # 检查 URL
        if self._match(url):
            headers = dict(flow.request.headers)
            self.callback(url, headers)

    def _match(self, url: str) -> bool:
        """检查 URL 是否匹配过滤规则"""
        url_lower = url.lower()

        # 扩展名匹配
        for ext in self._filter_extensions:
            if ext in url_lower:
                return True

        # 关键词匹配
        for kw in self._filter_keywords:
            if kw in url_lower:
                return True

        # 域名过滤
        if self._filter_domains:
            domain = urlparse(url).netloc
            for fd in self._filter_domains:
                if fd in domain:
                    return True
            return False

        return False


class Sniffer:
    """嗅探器管理器"""

    def __init__(self):
        self._running = False
        self._proxy_thread: Optional[threading.Thread] = None
        self._callbacks: list[Callable[[str, dict], None]] = []
        self._addon: Optional[VideoSnifferAddon] = None
        self._port = settings.get("network.proxy_port", 8080)
        self._host = settings.get("network.proxy_host", "127.0.0.1")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def port(self) -> int:
        return self._port

    @property
    def host(self) -> str:
        return self._host

    def add_url_callback(self, cb: Callable[[str, dict], None]):
        self._callbacks.append(cb)

    def remove_url_callback(self, cb: Callable[[str, dict], None]):
        if cb in self._callbacks:
            self._callbacks.remove(cb)

    def _on_video_url(self, url: str, headers: dict):
        for cb in self._callbacks:
            try:
                cb(url, headers)
            except Exception as exc:
                logger.debug(f"回调 {cb.__name__} 异常: {exc}")

    def start(self):
        """启动嗅探代理"""
        if self._running:
            return

        self._running = True
        self._addon = VideoSnifferAddon(self._on_video_url)
        self._proxy_thread = threading.Thread(target=self._run_proxy, daemon=True)
        self._proxy_thread.start()

    def stop(self):
        """停止嗅探代理"""
        self._running = False
        if hasattr(self, '_master') and self._master:
            try:
                self._master.shutdown()
            except Exception:
                pass

    def _run_proxy(self):
        """在后台线程运行 mitmproxy"""
        try:
            from mitmproxy import options
            from mitmproxy import master
            from mitmproxy.addons import default_addons

            opts = options.Options(
                listen_host=self._host,
                listen_port=self._port,
                ssl_insecure=True,
            )
            self._master = master.Master(opts)
            self._master.addons.add(*default_addons())
            self._master.addons.add(self._addon)
            self._master.run()
        except Exception as e:
            logger.error(f"嗅探代理启动失败: {e}")
            self._running = False

    def set_port(self, port: int):
        if self._running:
            self.stop()
        self._port = port
        settings.set_("network.proxy_port", port)
