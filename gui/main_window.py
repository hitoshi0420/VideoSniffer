"""主窗口 — 整合所有 GUI 组件"""

import os
import re
import asyncio
import threading
import customtkinter as ctk
from tkinter import messagebox, filedialog
from typing import Optional

from models.task import DownloadTask, TaskStatus, VideoType
from models.database import init_db, save_task as db_save_task, get_all_tasks as db_get_all_tasks
from gui.styles import get_theme, set_theme, status_color as _status_color
from gui.task_list import TaskListPanel
from gui.task_detail import TaskDetailPanel
from gui.settings_dialog import SettingsDialog
from core.downloader import engine as download_engine, ProgressInfo
from core.parser import parse_url
from core.sniffer import Sniffer
from core.upscaler import upscale_video, check_esrgan, UpscaleProgress
from config import settings
from utils.helpers import is_video_url, sanitize_filename, format_bytes
from utils.logger import get_logger

logger = get_logger(__name__)


class MainWindow(ctk.CTk):
    """主窗口"""

    def __init__(self):
        super().__init__()

        self.title("VideoSniffer — 视频嗅探下载工具")
        self.geometry("1100x680")
        self.minsize(900, 500)

        # 加载主题
        theme = settings.get("ui.theme", "dark")
        set_theme(theme)
        ctk.set_appearance_mode(theme)
        ctk.set_default_color_theme("blue")

        self._sniffer = Sniffer()
        self._sniffer_active = False
        self._search_mode = False
        self._searching = False
        self._current_task: Optional[DownloadTask] = None
        self._async_loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None

        self._build_ui()
        self._start_async_loop()
        self._load_history()

        # 注册下载回调
        download_engine.add_callback(self._on_download_progress)

        # 窗口关闭处理
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _load_history(self):
        """从数据库加载下载历史"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            history = loop.run_until_complete(db_get_all_tasks())
            loop.close()
            for item in history:
                task = DownloadTask.from_dict(item)
                self.task_list.add_task(task)
        except Exception:
            pass  # 首次运行或数据库损坏时静默跳过

    def _build_ui(self):
        theme = get_theme()
        self.configure(fg_color=theme["bg_primary"])

        # ============ 顶部导航栏 ============
        self.navbar = ctk.CTkFrame(self, height=44, fg_color=theme["bg_secondary"], corner_radius=0)
        self.navbar.pack(fill="x")

        # Logo
        ctk.CTkLabel(
            self.navbar, text="🎬 VideoSniffer",
            text_color=theme["accent"],
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(side="left", padx=16, pady=8)

        # URL 输入框
        self.url_entry = ctk.CTkEntry(
            self.navbar, width=350, height=32,
            placeholder_text="粘贴视频 URL 或输入片名搜索...",
            fg_color=theme["bg_input"],
        )
        self.url_entry.pack(side="left", padx=12, pady=6)
        self.url_entry.bind("<Return>", lambda e: self._smart_enter())
        # 后备：内部 tk Entry 直接绑定
        try:
            self.url_entry._entry.bind("<Return>", lambda e: self._smart_enter(), add="+")
        except Exception:
            pass
        # 全局兜底：根窗口绑定 Return 键
        self.bind_all("<Return>", lambda e: self._smart_enter())

        # 提交按钮
        self.btn_submit = ctk.CTkButton(
            self.navbar, text="解析下载", width=90, height=32,
            fg_color=theme["accent"], text_color="white",
            command=self._smart_enter,
        )
        self.btn_submit.pack(side="left", padx=4)

        # 嗅探按钮
        self.btn_sniff = ctk.CTkButton(
            self.navbar, text="🔴 开始嗅探", width=100, height=32,
            fg_color=theme["bg_card"], text_color=theme["text_primary"],
            command=self._toggle_sniffer,
        )
        self.btn_sniff.pack(side="left", padx=4)

        # AI 找片按钮
        self.btn_ai_search = ctk.CTkButton(
            self.navbar, text="🤖 AI找片", width=90, height=32,
            fg_color=theme["bg_card"], text_color=theme["text_primary"],
            command=self._ai_search,
        )
        self.btn_ai_search.pack(side="left", padx=4)

        # AI 画质增强开关
        self._ai_upscale_enabled = False
        def _toggle_ai():
            self._ai_upscale_enabled = not self._ai_upscale_enabled
        self.chk_ai_upscale = ctk.CTkCheckBox(
            self.navbar, text="AI增强", checkbox_width=18, checkbox_height=18,
            command=_toggle_ai,
            fg_color=theme["accent"], text_color=theme["text_secondary"],
            font=ctk.CTkFont(size=11),
        )
        self.chk_ai_upscale.pack(side="left", padx=6)

        # 右侧按钮
        self.btn_settings = ctk.CTkButton(
            self.navbar, text="⚙ 设置", width=80, height=32,
            fg_color="transparent", text_color=theme["text_secondary"],
            command=self._open_settings,
        )
        self.btn_settings.pack(side="right", padx=8)

        # ============ AI 搜索结果区（默认隐藏）============
        self.search_results_frame = ctk.CTkFrame(self, fg_color=theme["bg_secondary"], corner_radius=0)

        ctk.CTkLabel(
            self.search_results_frame, text="搜索结果",
            text_color=theme["text_primary"],
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="left", padx=12, pady=6)

        self.search_close_btn = ctk.CTkButton(
            self.search_results_frame, text="✕", width=28, height=22,
            fg_color="transparent", text_color=theme["text_secondary"],
            command=self._hide_search_results,
        )
        self.search_close_btn.pack(side="right", padx=6, pady=4)

        self.search_results_list = ctk.CTkScrollableFrame(
            self.search_results_frame, height=160, fg_color="transparent",
        )
        self.search_results_list.pack(fill="x", padx=12, pady=(0, 8))

        # ============ 主内容区 ============
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=4, pady=4)

        # --- 左侧任务列表 ---
        left_frame = ctk.CTkFrame(main_frame, fg_color=theme["bg_secondary"], corner_radius=10)
        left_frame.pack(side="left", fill="both", padx=(0, 4), expand=False)
        left_frame.configure(width=320)

        self.task_list = TaskListPanel(left_frame, on_task_select=self._on_task_select)
        self.task_list.pack(fill="both", expand=True, padx=4, pady=4)

        # --- 右侧详情面板 ---
        right_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        right_frame.pack(side="right", fill="both", expand=True, padx=(4, 0))

        self.task_detail = TaskDetailPanel(right_frame)
        self.task_detail.pack(fill="both", expand=True)

        # ============ 底部状态栏 ============
        self.statusbar = ctk.CTkFrame(self, height=28, fg_color=theme["bg_secondary"], corner_radius=0)
        self.statusbar.pack(fill="x")

        self.status_label = ctk.CTkLabel(
            self.statusbar, text="就绪",
            text_color=theme["text_secondary"],
            font=ctk.CTkFont(size=11),
        )
        self.status_label.pack(side="left", padx=12, pady=4)

        # 解析进度条（不确定模式）
        self.parse_progress = ctk.CTkProgressBar(
            self.statusbar, width=120, height=8,
            progress_color=theme["info"],
            fg_color=theme["progress_bg"],
            mode="indeterminate",
        )

        self.stats_label = ctk.CTkLabel(
            self.statusbar, text="任务: 0 | 总速度: 0 KB/s",
            text_color=theme["text_muted"],
            font=ctk.CTkFont(size=11),
        )
        self.stats_label.pack(side="right", padx=12, pady=4)

    def _start_async_loop(self):
        """启动异步事件循环"""
        self._async_loop = asyncio.new_event_loop()

        def run_loop():
            asyncio.set_event_loop(self._async_loop)
            self._async_loop.run_forever()

        self._loop_thread = threading.Thread(target=run_loop, daemon=True)
        self._loop_thread.start()
        download_engine.start(self._async_loop)

    def _submit_url(self):
        """提交 URL 进行解析和下载"""
        url = self.url_entry.get().strip()
        if not url:
            return

        # 清理输入
        url = url.strip('"').strip("'")
        if not url.startswith("http"):
            messagebox.showwarning("提示", "请输入有效的 HTTP/HTTPS 地址")
            return

        # 禁用按钮防止重复点击，显示进度
        self.btn_submit.configure(text="解析中...", state="disabled", fg_color="#555555")
        self.parse_progress.pack(side="left", padx=8, pady=4)
        self.parse_progress.start()
        self._set_status("正在解析视频地址...", "info")
        self.task_detail.add_log(f"开始解析: {url[:80]}")

        def _parse_thread():
            # 识别站点
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc
            except Exception:
                domain = ""
            if "bilibili" in domain:
                self.after(0, lambda: self._set_status("B站 — 调用 API 获取视频地址...", "info"))
            elif "missav" in domain:
                self.after(0, lambda: self._set_status("MissAV — 解密视频地址...", "info"))

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                self.after(0, lambda: self.task_detail.add_log("请求服务器..."))
                info = loop.run_until_complete(parse_url(url))
            except Exception as e:
                import traceback
                logger.error(traceback.format_exc())
                err_msg = str(e)
                self.after(0, lambda msg=err_msg: self._set_status(f"解析异常: {msg}", "error"))
                self.after(0, lambda msg=err_msg: self.task_detail.add_log(f"ERROR: {msg}"))
                self.after(0, lambda: self._finish_parse())
                return
            finally:
                loop.close()

            if info.error:
                self.after(0, lambda: self._set_status(f"解析失败: {info.error[:60]}", "error"))
                self.after(0, lambda: self.task_detail.add_log(f"FAIL: {info.error}"))
                self.after(0, lambda: self._finish_parse())
                return

            self.after(0, lambda: self.task_detail.add_log(
                f"OK: {info.title or 'video'} | 画质:{info.quality or '?'} | "
                f"分片:{len(info.segments)} | DASH:{'Y' if info.audio_url else 'N'}"
            ))
            self.after(0, lambda: self._set_status(
                f"解析成功: {info.title or '视频'} ({info.quality or info.video_type.value})", "success"
            ))

            # 多画质选项 → 弹出选择窗
            if info.quality_options and len(info.quality_options) > 1:
                self.after(0, lambda: self._show_quality_picker(info))
            else:
                task = DownloadTask(
                    url=info.url,
                    title=info.title or sanitize_filename(info.url.rsplit("/", 1)[-1].split("?")[0]),
                    video_type=info.video_type,
                    segments=info.segments,
                    headers=info.headers,
                    duration=info.duration,
                    quality=info.quality,
                    audio_url=info.audio_url,
                    video_format=info.video_format,
                )
                self.after(0, lambda t=task: self._start_download(t))
                self.after(0, lambda: self._finish_parse())

        threading.Thread(target=_parse_thread, daemon=True).start()

    def _set_status(self, text: str, level: str = ""):
        theme = get_theme()
        colors = {"error": theme["error"], "info": theme["info"], "success": theme["success"]}
        self.status_label.configure(text=text, text_color=colors.get(level, theme["text_secondary"]))

    def _finish_parse(self):
        theme = get_theme()
        self._searching = False
        self.btn_submit.configure(text="解析下载", state="normal", fg_color=theme["accent"], command=self._smart_enter)
        self.parse_progress.stop()
        self.parse_progress.pack_forget()

    def _show_quality_picker(self, info):
        """显示画质选择对话框（支持 DASH 分离流 + 合并流）"""
        theme = get_theme()

        dialog = ctk.CTkToplevel(self)
        dialog.title("选择下载画质")
        dialog.geometry("450x400")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        # 标题区
        title_text = info.title[:50] if info.title else "视频"
        ctk.CTkLabel(
            dialog, text=title_text,
            text_color=theme["text_primary"],
            font=ctk.CTkFont(size=14, weight="bold"),
            wraplength=410,
        ).pack(padx=20, pady=(16, 4))

        ctk.CTkLabel(
            dialog, text=f"默认画质: {info.quality}  —  请选择下载画质:",
            text_color=theme["text_secondary"],
            font=ctk.CTkFont(size=12),
        ).pack(padx=20, pady=(0, 10))

        # 可滚动选项区
        options_frame = ctk.CTkScrollableFrame(
            dialog, height=210,
            fg_color=theme["bg_card"],
        )
        options_frame.pack(fill="both", expand=True, padx=20, pady=4)

        selected_idx = [0]  # 可变容器追踪选中项索引
        radio_buttons = []

        def _on_select(idx):
            selected_idx[0] = idx

        for i, opt in enumerate(info.quality_options):
            size_str = format_bytes(opt.get("filesize", 0))
            is_dash = opt.get("dash")
            label = opt["label"]

            if is_dash:
                text = f"  {label}    (DASH 视频流+音频合并)"
            else:
                text = f"  {label}    (合并流)"
            if size_str != "0 B":
                text = f"  {label}    ({size_str})" + (" [DASH]" if is_dash else " [合并]")

            rb = ctk.CTkRadioButton(
                options_frame, text=text,
                font=ctk.CTkFont(size=13),
                text_color=theme["text_primary"],
                fg_color=theme["accent"],
                command=lambda idx=i: _on_select(idx),
            )
            rb.pack(anchor="w", padx=12, pady=5)
            radio_buttons.append(rb)

        # 默认选中第一项（最高画质）
        if radio_buttons:
            radio_buttons[0].select()

        def _on_confirm():
            dialog.destroy()
            opt = info.quality_options[selected_idx[0]]
            self._create_task_from_option(info, opt)
            self._finish_parse()

        def _on_cancel():
            dialog.destroy()
            # 使用默认（最高画质）
            opt = info.quality_options[0]
            self._create_task_from_option(info, opt)
            self._finish_parse()

        # 按钮区
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(10, 16))

        ctk.CTkButton(
            btn_frame, text="取消 (使用最高画质)", width=160, height=32,
            fg_color=theme["bg_card"], text_color=theme["text_secondary"],
            command=_on_cancel,
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            btn_frame, text="确认下载", width=100, height=32,
            fg_color=theme["accent"], text_color="white",
            command=_on_confirm,
        ).pack(side="right", padx=4)

    def _create_task_from_option(self, info, opt):
        """根据选中的画质选项创建 DownloadTask"""
        is_dash = opt.get("dash")
        if is_dash:
            url = opt["video_url"]
            audio_url = opt.get("audio_url", "")
            video_format = "m4s"
        else:
            url = opt.get("url", opt.get("video_url", ""))
            audio_url = ""
            video_format = ""

        task = DownloadTask(
            url=url,
            title=info.title or sanitize_filename(url.rsplit("/", 1)[-1].split("?")[0]),
            video_type=info.video_type,
            segments=info.segments,
            headers=info.headers,
            duration=info.duration,
            quality=opt["label"],
            audio_url=audio_url,
            video_format=video_format,
        )
        self._start_download(task)

    def _start_download(self, task: DownloadTask):
        """开始下载"""
        task_id = download_engine.submit(task)
        self.task_list.add_task(task)
        self.status_label.configure(text=f"📥 已添加任务: {task.title or task.url[:50]}")
        self._current_task = task
        self.task_detail.show_task(task)

    def _toggle_sniffer(self):
        """切换嗅探器状态"""
        if self._sniffer_active:
            self._sniffer.stop()
            self._sniffer_active = False
            theme = get_theme()
            self.btn_sniff.configure(
                text="🔴 开始嗅探",
                fg_color=theme["bg_card"],
            )
            self.status_label.configure(text="嗅探已停止")
        else:
            # 检查证书
            from utils.certificate import ensure_certificate
            if not ensure_certificate():
                # 尝试生成
                pass

            self._sniffer.add_url_callback(self._on_sniffed_url)
            self._sniffer.start()
            self._sniffer_active = True
            theme = get_theme()
            self.btn_sniff.configure(
                text="🟢 停止嗅探",
                fg_color=theme["success"],
            )
            self.status_label.configure(
                text=f"🔍 嗅探中 — 代理: {self._sniffer.host}:{self._sniffer.port}"
            )

    def _on_sniffed_url(self, url: str, headers: dict):
        """嗅探到视频 URL 的回调"""
        if not is_video_url(url):
            return
        # 放到主线程处理
        self.after(0, lambda: self._handle_sniffed(url, headers))

    def _handle_sniffed(self, url: str, headers: dict):
        """在主线程处理嗅探到的 URL"""
        self.status_label.configure(text=f"🎯 发现视频: {url[:80]}...")
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, url)
        self._submit_url()

    def _on_task_select(self, task: DownloadTask):
        """选择任务查看详情"""
        self._current_task = task
        self.task_detail.show_task(task)

    def _on_download_progress(self, info: ProgressInfo):
        """下载进度回调（来自下载引擎的工作线程）"""
        self.after(0, lambda: self._update_ui_from_progress(info))
        # 任务终态时异步保存到数据库
        if info.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            if self._current_task and self._current_task.id == info.task_id:
                task_dict = self._current_task.to_dict()
                if self._async_loop:
                    asyncio.run_coroutine_threadsafe(db_save_task(task_dict), self._async_loop)

    def _update_ui_from_progress(self, info: ProgressInfo):
        """在主线程更新 UI"""
        self.task_list.update_task(info.task_id, info.progress, info.speed, info.status)

        if self._current_task and self._current_task.id == info.task_id:
            self.task_detail.update_progress(
                info.progress, info.speed, info.status,
                info.downloaded, info.total,
            )

        # 更新状态栏
        active = download_engine.active_count
        queue = download_engine.queue_count
        total_speed = download_engine.total_speed
        self.stats_label.configure(
            text=f"任务: {active + queue} (活跃: {active}) | 总速度: {total_speed:.1f} KB/s"
        )

        if info.status == TaskStatus.COMPLETED:
            self.status_label.configure(text=f"✅ 下载完成: {self._current_task.title if self._current_task else ''}")
            if self._ai_upscale_enabled and self._current_task and self._current_task.save_path:
                self._start_ai_upscale(self._current_task)
            elif settings.get("ui.notify_complete", True):
                self._show_notification("下载完成", self._current_task.title if self._current_task else "")

    def _smart_enter(self):
        """智能 Enter：自动判断是 URL 还是搜索"""
        # 仅当输入框有焦点时才处理
        focused = self.focus_get()
        if focused is not None:
            try:
                if focused is not self.url_entry._entry and focused is not self.url_entry:
                    return
            except Exception:
                pass
        text = self.url_entry.get().strip()
        if not text:
            return
        if text.startswith("http://") or text.startswith("https://"):
            self._submit_url()
        else:
            self._ai_search()

    def _toggle_search_mode(self):
        """切换 AI 找片 / URL 输入模式"""
        theme = get_theme()
        self._search_mode = not self._search_mode
        logger.debug(f"_toggle_search_mode -> _search_mode={self._search_mode}")

        if self._search_mode:
            self.btn_ai_search.configure(text="🤖 AI找片", fg_color=theme["accent"], border_width=2, border_color=theme["accent"])
            self.url_entry.configure(placeholder_text="输入片名搜索，Enter 确认...")
            self._set_status("AI 找片模式 — 输入片名后按 Enter", "info")
        else:
            self.btn_ai_search.configure(text="🤖 AI找片", fg_color=theme["bg_card"], border_width=0)
            self.url_entry.configure(placeholder_text="粘贴视频 URL 或输入片名搜索...")
            self._hide_search_results()
            self._set_status("就绪")
        self.url_entry.focus_set()
        self.update_idletasks()

    def _hide_search_results(self):
        self.search_results_frame.pack_forget()
        for w in self.search_results_list.winfo_children():
            w.destroy()

    def _show_search_results(self, results: list):
        """展示搜索结果为可点击按钮列表"""
        self._hide_search_results()
        if not results:
            return

        theme = get_theme()
        self.search_results_frame.pack(fill="x", after=self.navbar)

        for i, r in enumerate(results[:8]):
            title = r.get("title", "?")[:40]
            conf = r.get("confidence", 0)
            platform = r.get("platform", "")
            badge = {"bilibili": "B站", "vqq": "腾讯", "missav": "MissAV", "youtube": "YT"}.get(platform, "")
            conf_text = f"{conf:.0%}" if conf > 0 else ""

            row = ctk.CTkFrame(self.search_results_list, fg_color="transparent")
            row.pack(fill="x", pady=1)

            info_text = f"{badge} {conf_text} | {title}"
            if r.get("verified"):
                info_text = f"✅ {info_text}"

            btn = ctk.CTkButton(
                row, text=info_text, anchor="w",
                fg_color=theme["bg_card"], text_color=theme["text_primary"],
                font=ctk.CTkFont(size=11), height=28,
                command=lambda url=r["url"]: self._on_search_result_click(url),
            )
            btn.pack(fill="x", padx=4, pady=1)

    def _on_search_result_click(self, url: str):
        """点击搜索结果 → 填入URL并开始下载"""
        self._hide_search_results()
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, url)
        self._set_status(f"已选择: {url[:80]}...", "info")
        self._submit_url()

    def _ai_search(self):
        """AI 找片入口（在后台线程执行）"""
        if self._searching:
            return
        query = self.url_entry.get().strip()
        logger.debug(f"_ai_search called, query='{query}'")
        if not query:
            self._set_status("请输入片名或关键词", "error")
            return

        self._searching = True
        self.btn_submit.configure(text="搜索中...", state="disabled")
        self.parse_progress.pack(side="left", padx=8, pady=4)
        self.parse_progress.start()
        self._set_status(f"AI 正在搜索: {query}...", "info")
        self.task_detail.add_log(f"===== AI 找片: {query} =====")
        self._hide_search_results()
        self.update_idletasks()

        # 使用队列实现线程安全的 UI 更新
        import queue
        progress_queue = queue.Queue()

        def _poll_queue():
            """在主线程轮询队列，处理进度更新"""
            try:
                msg_count = 0
                while True:
                    msg = progress_queue.get_nowait()
                    msg_count += 1
                    if msg["type"] == "progress":
                        logger.debug(f"progress: {msg['step']}")
                        self._set_status(f"[{msg['step']}] {msg['detail']}", "info")
                        self.task_detail.add_log(f"  [{msg['step']}] {msg['detail']}")
                    elif msg["type"] == "error":
                        logger.debug(f"error: {msg['detail'][:60]}")
                        self._set_status(f"搜索异常: {msg['detail']}", "error")
                        self.task_detail.add_log(f"  ERROR: {msg['detail']}")
                        self._finish_parse()
                        return
                    elif msg["type"] == "no_results":
                        logger.debug("no_results")
                        self._set_status("未找到匹配的视频资源", "error")
                        self.task_detail.add_log("  未找到匹配的视频资源")
                        self._finish_parse()
                        return
                    elif msg["type"] == "results":
                        results = msg["data"]
                        logger.debug(f"results: {len(results)} items")
                        self.task_detail.add_log(f"===== 找到 {len(results)} 个候选视频 =====")
                        for r in results:
                            self.task_detail.add_log(
                                f"  {r.get('platform','?')} | 置信度{r.get('confidence',0):.0%} | {r.get('title','')[:50]}"
                            )
                        self._show_search_results(results)
                        self._set_status(f"找到 {len(results)} 个视频 — 点击结果自动下载", "success")
                        self._finish_parse()
                        return
                if msg_count > 0:
                    logger.debug(f"processed {msg_count} messages")
            except queue.Empty:
                pass
            # 继续轮询
            self.after(150, _poll_queue)

        def _search_thread():
            logger.debug("_search_thread started")
            try:
                from core.ai_search import search_video
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                def _on_progress(step, detail):
                    logger.debug(f"_on_progress: {step}")
                    progress_queue.put({"type": "progress", "step": step, "detail": detail})

                logger.debug("calling search_video...")
                results = loop.run_until_complete(
                    search_video(query, on_progress=_on_progress)
                )
                loop.close()
                logger.debug(f"search_video returned {len(results)} results")

                if not results:
                    progress_queue.put({"type": "no_results"})
                else:
                    progress_queue.put({"type": "results", "data": results})
            except Exception as e:
                import traceback
                traceback.print_exc()
                err_msg = str(e)
                logger.debug(f"search exception: {err_msg}")
                progress_queue.put({"type": "error", "detail": err_msg})

        logger.debug("starting search + poll")
        threading.Thread(target=_search_thread, daemon=True).start()
        self.after(150, _poll_queue)
        logger.debug("thread and poll started")

    def _open_settings(self):
        SettingsDialog(self)

    def _show_error(self, msg: str):
        messagebox.showerror("错误", msg)
        self.status_label.configure(text=f"❌ {msg}")

    def _show_notification(self, title: str, msg: str):
        try:
            self.after(200, lambda: messagebox.showinfo(title, msg))
        except Exception:
            pass

    def _start_ai_upscale(self, task):
        """下载完成后启动 AI 画质增强"""
        if not check_esrgan():
            self.status_label.configure(text="⚠ Real-ESRGAN 未安装，跳过 AI 增强")
            self.task_detail.add_log("WARN: Real-ESRGAN 未找到，跳过 AI 增强")
            return

        save_path = task.save_path
        self.status_label.configure(text="🎨 AI 画质增强中... 0%")
        self.task_detail.add_log("===== AI 画质增强 (Real-ESRGAN 4x) =====")
        self.task_detail.add_log(f"输入: {os.path.basename(save_path)}")
        self._upscaling = True

        def _upscale_thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                async def _progress_cb(p: UpscaleProgress):
                    self.after(0, lambda: self._on_upscale_progress(p))

                result = loop.run_until_complete(
                    upscale_video(save_path, scale=4, on_progress=_progress_cb)
                )
                self.after(0, lambda: self._on_upscale_done(result))
            except Exception as e:
                import traceback
                self.after(0, lambda: self._on_upscale_done("", str(e)))
                logger.error(f"AI 增强失败: {traceback.format_exc()}")
            finally:
                loop.close()

        threading.Thread(target=_upscale_thread, daemon=True).start()

    def _on_upscale_progress(self, p: UpscaleProgress):
        """AI 增强进度更新"""
        stage_text = {"extracting": "拆帧", "upscaling": "AI超分", "merging": "合成", "done": "完成"}.get(p.stage, p.stage)
        if p.stage == "upscaling" and p.total_frames > 0:
            self.status_label.configure(
                text=f"🎨 AI超分: {p.frame}/{p.total_frames} 帧 ({p.progress:.0f}%)"
            )
            self.task_detail.add_log(
                f"  🎨 {stage_text}: {p.frame}/{p.total_frames} 帧 ({p.progress:.0f}%)"
            )
        else:
            self.status_label.configure(text=f"🎨 AI增强 [{stage_text}]: {p.progress:.0f}%")

    def _on_upscale_done(self, output_path: str, error: str = ""):
        """AI 增强完成"""
        self._upscaling = False
        if error:
            self.status_label.configure(text=f"❌ AI 增强失败: {error[:60]}")
            self.task_detail.add_log(f"ERROR: AI 增强失败: {error}")
        else:
            self.status_label.configure(text=f"✅ AI 增强完成: {os.path.basename(output_path)}")
            self.task_detail.add_log(f"===== AI 增强完成 =====")
            self.task_detail.add_log(f"输出: {output_path}")
            self._show_notification("AI 增强完成", f"已保存: {os.path.basename(output_path)}")

    def _on_close(self):
        """关闭窗口"""
        if self._sniffer_active:
            self._sniffer.stop()
        download_engine.stop()
        if self._async_loop:
            self._async_loop.call_soon_threadsafe(self._async_loop.stop)
        self.destroy()


def run():
    """启动 GUI"""
    app = MainWindow()
    app.mainloop()
