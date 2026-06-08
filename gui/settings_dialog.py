"""设置对话框"""

import customtkinter as ctk
from config import settings
from gui.styles import get_theme, set_theme


class SettingsDialog(ctk.CTkToplevel):
    """设置对话框"""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.title("设置")
        self.geometry("550x520")
        self.resizable(False, False)
        self._build()
        self.grab_set()

    def _build(self):
        theme = get_theme()
        self.configure(fg_color=theme["bg_primary"])

        # TabView
        self.tabview = ctk.CTkTabview(self)
        self.tabview.pack(fill="both", expand=True, padx=12, pady=12)

        self.tab_network = self.tabview.add("网络设置")
        self.tab_download = self.tabview.add("下载设置")
        self.tab_sniffer = self.tabview.add("嗅探设置")
        self.tab_ui = self.tabview.add("界面设置")
        self.tab_site = self.tabview.add("站点设置")
        self.tab_ai = self.tabview.add("AI设置")

        self._build_network_tab()
        self._build_download_tab()
        self._build_sniffer_tab()
        self._build_ui_tab()
        self._build_site_tab()
        self._build_ai_tab()

        # 底部按钮
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=(0, 12))

        ctk.CTkButton(
            btn_frame, text="恢复默认", fg_color="transparent",
            border_width=1, border_color=theme["border"],
            text_color=theme["text_secondary"], width=100,
            command=self._reset_defaults,
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            btn_frame, text="取消", fg_color="transparent",
            border_width=1, border_color=theme["border"],
            text_color=theme["text_secondary"], width=80,
            command=self.destroy,
        ).pack(side="right", padx=4)

        ctk.CTkButton(
            btn_frame, text="保存", fg_color=theme["accent"],
            text_color="white", width=80,
            command=self._save,
        ).pack(side="right", padx=4)

    def _build_network_tab(self):
        theme = get_theme()
        cfg = settings.load()

        # 代理主机
        self._add_label(self.tab_network, "代理设置")
        row1 = ctk.CTkFrame(self.tab_network, fg_color="transparent")
        row1.pack(fill="x", padx=8, pady=4)

        ctk.CTkLabel(row1, text="主机:", font=ctk.CTkFont(size=12), text_color=theme["text_secondary"]).pack(side="left", padx=4)
        self.proxy_host = ctk.CTkEntry(row1, width=120)
        self.proxy_host.insert(0, cfg["network"]["proxy_host"])
        self.proxy_host.pack(side="left", padx=4)

        ctk.CTkLabel(row1, text="端口:", font=ctk.CTkFont(size=12), text_color=theme["text_secondary"]).pack(side="left", padx=4)
        self.proxy_port = ctk.CTkEntry(row1, width=80)
        self.proxy_port.insert(0, str(cfg["network"]["proxy_port"]))
        self.proxy_port.pack(side="left", padx=4)

        # 上游代理（访问外网）
        self._add_label(self.tab_network, "上游代理（访问外网，如 Clash/V2Ray）")
        row1b = ctk.CTkFrame(self.tab_network, fg_color="transparent")
        row1b.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(row1b, text="代理地址:", font=ctk.CTkFont(size=12), text_color=theme["text_secondary"]).pack(side="left", padx=4)
        self.upstream_proxy = ctk.CTkEntry(row1b, width=280, placeholder_text="http://127.0.0.1:7897")
        self.upstream_proxy.insert(0, cfg["network"].get("upstream_proxy", ""))
        self.upstream_proxy.pack(side="left", padx=4)

        # 超时和重试
        self._add_label(self.tab_network, "网络参数")
        row2 = ctk.CTkFrame(self.tab_network, fg_color="transparent")
        row2.pack(fill="x", padx=8, pady=4)

        ctk.CTkLabel(row2, text="超时(秒):", font=ctk.CTkFont(size=12), text_color=theme["text_secondary"]).pack(side="left", padx=4)
        self.timeout = ctk.CTkEntry(row2, width=60)
        self.timeout.insert(0, str(cfg["network"]["timeout"]))
        self.timeout.pack(side="left", padx=4)

        ctk.CTkLabel(row2, text="重试次数:", font=ctk.CTkFont(size=12), text_color=theme["text_secondary"]).pack(side="left", padx=4)
        self.retry_count = ctk.CTkEntry(row2, width=60)
        self.retry_count.insert(0, str(cfg["network"]["retry_count"]))
        self.retry_count.pack(side="left", padx=4)

    def _build_download_tab(self):
        theme = get_theme()
        cfg = settings.load()

        self._add_label(self.tab_download, "下载设置")

        # 线程数
        row1 = ctk.CTkFrame(self.tab_download, fg_color="transparent")
        row1.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(row1, text="并发线程:", font=ctk.CTkFont(size=12), text_color=theme["text_secondary"]).pack(side="left", padx=4)
        self.threads = ctk.CTkEntry(row1, width=60)
        self.threads.insert(0, str(cfg["network"]["download_threads"]))
        self.threads.pack(side="left", padx=4)

        # 速度限制
        row2 = ctk.CTkFrame(self.tab_download, fg_color="transparent")
        row2.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(row2, text="限速(KB/s, 0=不限):", font=ctk.CTkFont(size=12), text_color=theme["text_secondary"]).pack(side="left", padx=4)
        self.speed_limit = ctk.CTkEntry(row2, width=60)
        self.speed_limit.insert(0, str(cfg["network"]["speed_limit"]))
        self.speed_limit.pack(side="left", padx=4)

        # 下载路径
        row3 = ctk.CTkFrame(self.tab_download, fg_color="transparent")
        row3.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(row3, text="保存路径:", font=ctk.CTkFont(size=12), text_color=theme["text_secondary"]).pack(side="left", padx=4)
        self.save_path = ctk.CTkEntry(row3, width=300)
        self.save_path.insert(0, cfg["video"]["download_path"])
        self.save_path.pack(side="left", padx=4)

        # 下载选项
        self._add_label(self.tab_download, "下载选项")
        self.auto_merge = ctk.CTkSwitch(
            self.tab_download, text="自动合并分片",
            onvalue=True, offvalue=False,
        )
        self.auto_merge.pack(fill="x", padx=12, pady=4)
        if cfg["video"]["auto_merge"]:
            self.auto_merge.select()

        self.keep_temp = ctk.CTkSwitch(
            self.tab_download, text="保留临时文件",
            onvalue=True, offvalue=False,
        )
        self.keep_temp.pack(fill="x", padx=12, pady=4)
        if cfg["video"]["keep_temp_files"]:
            self.keep_temp.select()

    def _build_sniffer_tab(self):
        theme = get_theme()
        cfg = settings.load()

        # 域名过滤
        self._add_label(self.tab_sniffer, "域名过滤（每行一个，留空则不过滤）")
        self.filter_domains = ctk.CTkTextbox(
            self.tab_sniffer, height=60,
            fg_color=theme["bg_input"], text_color=theme["text_primary"],
            font=ctk.CTkFont(size=12),
        )
        self.filter_domains.insert(
            "1.0", "\n".join(cfg.get("sniffer", {}).get("filter_domains", []))
        )
        self.filter_domains.pack(fill="x", padx=12, pady=(0, 8))

        # 扩展名过滤
        self._add_label(self.tab_sniffer, "文件扩展名过滤（逗号分隔）")
        self.filter_extensions = ctk.CTkEntry(
            self.tab_sniffer, width=420,
            placeholder_text=".mp4, .m3u8, .ts, .flv, ...",
        )
        exts = cfg.get("sniffer", {}).get("filter_extensions", [])
        self.filter_extensions.insert(0, ", ".join(exts))
        self.filter_extensions.pack(fill="x", padx=12, pady=(0, 8))

        # 关键词过滤
        self._add_label(self.tab_sniffer, "URL 关键词过滤（逗号分隔）")
        self.filter_keywords = ctk.CTkEntry(
            self.tab_sniffer, width=420,
            placeholder_text="video, stream, m3u8, ...",
        )
        kws = cfg.get("sniffer", {}).get("filter_keywords", [])
        self.filter_keywords.insert(0, ", ".join(kws))
        self.filter_keywords.pack(fill="x", padx=12, pady=(0, 8))

    def _build_ui_tab(self):
        theme = get_theme()
        cfg = settings.load()

        self._add_label(self.tab_ui, "主题设置")

        # 主题选择
        themes_frame = ctk.CTkFrame(self.tab_ui, fg_color="transparent")
        themes_frame.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(themes_frame, text="主题:", font=ctk.CTkFont(size=12), text_color=theme["text_secondary"]).pack(side="left", padx=4)

        self.theme_var = ctk.StringVar(value=cfg["ui"]["theme"])
        self.theme_dark = ctk.CTkRadioButton(
            themes_frame, text="暗色", variable=self.theme_var, value="dark",
        )
        self.theme_dark.pack(side="left", padx=8)
        self.theme_light = ctk.CTkRadioButton(
            themes_frame, text="亮色", variable=self.theme_var, value="light",
        )
        self.theme_light.pack(side="left", padx=8)

        # 通知设置
        self._add_label(self.tab_ui, "通知设置")
        self.notify = ctk.CTkSwitch(
            self.tab_ui, text="下载完成通知",
            onvalue=True, offvalue=False,
        )
        self.notify.pack(fill="x", padx=12, pady=4)
        if cfg["ui"]["notify_complete"]:
            self.notify.select()

    def _add_label(self, parent, text: str):
        theme = get_theme()
        ctk.CTkLabel(
            parent, text=text,
            text_color=theme["text_primary"],
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(fill="x", padx=8, pady=(12, 4), anchor="w")

    def _build_site_tab(self):
        theme = get_theme()
        cfg = settings.load()

        self._add_label(self.tab_site, "B站 (bilibili.com) — Cookie 配置")
        ctk.CTkLabel(
            self.tab_site,
            text="登录 B站后，按 F12 → Application → Cookies → bilibili.com\n"
                 "找到 SESSDATA，复制整行 Value 填入下方（格式: SESSDATA=xxxx）",
            font=ctk.CTkFont(size=11),
            text_color=theme["text_muted"],
            justify="left",
        ).pack(fill="x", padx=12, pady=(0, 6))

        self.bili_cookie = ctk.CTkEntry(
            self.tab_site, width=420, height=32,
            placeholder_text="例如: SESSDATA=abc123def456%2Cxyz...",
        )
        self.bili_cookie.insert(0, cfg.get("site_cookies", {}).get("bilibili", ""))
        self.bili_cookie.pack(fill="x", padx=12, pady=4)

        ctk.CTkLabel(
            self.tab_site,
            text="提示: 有 Cookie 可解锁 1080P/4K，无 Cookie 仅 480P",
            font=ctk.CTkFont(size=10),
            text_color="#f0a040",
        ).pack(fill="x", padx=12, pady=(4, 0))

    def _build_ai_tab(self):
        theme = get_theme()
        cfg = settings.load()

        self._add_label(self.tab_ai, "AI 搜索配置 (DeepSeek)")

        # API Key
        ctk.CTkLabel(
            self.tab_ai, text="API Key:",
            font=ctk.CTkFont(size=12), text_color=theme["text_secondary"],
        ).pack(fill="x", padx=12, pady=(8, 2))
        self.ai_api_key = ctk.CTkEntry(self.tab_ai, width=350, show="*", placeholder_text="sk-xxxx")
        self.ai_api_key.insert(0, cfg.get("ai", {}).get("api_key", ""))
        self.ai_api_key.pack(fill="x", padx=12, pady=(0, 6))

        # Base URL
        ctk.CTkLabel(
            self.tab_ai, text="API 地址:",
            font=ctk.CTkFont(size=12), text_color=theme["text_secondary"],
        ).pack(fill="x", padx=12, pady=(4, 2))
        self.ai_base_url = ctk.CTkEntry(self.tab_ai, width=350)
        self.ai_base_url.insert(0, cfg.get("ai", {}).get("base_url", "https://api.deepseek.com/v1"))
        self.ai_base_url.pack(fill="x", padx=12, pady=(0, 6))

        # Model
        ctk.CTkLabel(
            self.tab_ai, text="模型名称:",
            font=ctk.CTkFont(size=12), text_color=theme["text_secondary"],
        ).pack(fill="x", padx=12, pady=(4, 2))
        self.ai_model = ctk.CTkEntry(self.tab_ai, width=200)
        self.ai_model.insert(0, cfg.get("ai", {}).get("model", "deepseek-chat"))
        self.ai_model.pack(fill="x", padx=12, pady=(0, 6))

        ctk.CTkLabel(
            self.tab_ai,
            text="DeepSeek API 注册地址: https://platform.deepseek.com\n"
                 "新用户通常有免费额度，模型选 deepseek-chat",
            font=ctk.CTkFont(size=10),
            text_color=theme["text_muted"],
            justify="left",
        ).pack(fill="x", padx=12, pady=(8, 0))

    def _save(self):
        try:
            settings.set_("network.proxy_host", self.proxy_host.get())
            settings.set_("network.proxy_port", int(self.proxy_port.get()))
            settings.set_("network.upstream_proxy", self.upstream_proxy.get().strip())
            settings.set_("network.download_threads", int(self.threads.get()))
            settings.set_("network.speed_limit", int(self.speed_limit.get()))
            settings.set_("network.timeout", int(self.timeout.get()))
            settings.set_("network.retry_count", int(self.retry_count.get()))
            settings.set_("video.download_path", self.save_path.get())
            settings.set_("video.auto_merge", self.auto_merge.get())
            settings.set_("video.keep_temp_files", self.keep_temp.get())
            settings.set_("sniffer.filter_domains",
                          [d.strip() for d in self.filter_domains.get("1.0", "end").strip().split("\n") if d.strip()])
            settings.set_("sniffer.filter_extensions",
                          [e.strip() for e in self.filter_extensions.get().split(",") if e.strip()])
            settings.set_("sniffer.filter_keywords",
                          [k.strip() for k in self.filter_keywords.get().split(",") if k.strip()])
            settings.set_("ui.theme", self.theme_var.get())
            settings.set_("ui.notify_complete", self.notify.get())
            settings.set_("site_cookies.bilibili", self.bili_cookie.get())
            settings.set_("ai.api_key", self.ai_api_key.get())
            settings.set_("ai.base_url", self.ai_base_url.get())
            settings.set_("ai.model", self.ai_model.get())
            settings.save()
            set_theme(self.theme_var.get())
        except ValueError as e:
            pass
        self.destroy()

    def _reset_defaults(self):
        settings.reset()
        self.destroy()
        SettingsDialog(self.master)
