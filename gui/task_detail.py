"""任务详情面板 — 右侧视频信息和下载日志"""

import customtkinter as ctk
from models.task import DownloadTask, TaskStatus, STATUS_LABELS
from gui.styles import get_theme, status_color
from utils.helpers import format_bytes, format_time


class TaskDetailPanel(ctk.CTkFrame):
    """任务详情面板"""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=get_theme()["bg_secondary"], corner_radius=10, **kwargs)
        self.current_task: DownloadTask = None
        self._build()

    def _build(self):
        theme = get_theme()

        # 标题
        self.title_label = ctk.CTkLabel(
            self, text="选择一个任务查看详情",
            text_color=theme["text_muted"],
            font=ctk.CTkFont(size=14),
        )
        self.title_label.pack(padx=16, pady=(16, 12), anchor="w")

        # 视频信息区域
        self.info_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.info_frame.pack(fill="x", padx=16, pady=4)

        self.info_lines: list[ctk.CTkLabel] = []
        for _ in range(8):
            lbl = ctk.CTkLabel(
                self.info_frame, text="",
                text_color=theme["text_secondary"],
                font=ctk.CTkFont(size=12),
                anchor="w",
            )
            lbl.pack(fill="x", pady=2)
            self.info_lines.append(lbl)

        # 进度条区域
        self.progress_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.progress_frame.pack(fill="x", padx=16, pady=(12, 4))

        self.progress_bar = ctk.CTkProgressBar(
            self.progress_frame,
            height=12,
            progress_color=theme["accent"],
            fg_color=theme["progress_bg"],
        )
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", padx=4, pady=4)

        self.progress_label = ctk.CTkLabel(
            self.progress_frame, text="0%",
            text_color=theme["text_primary"],
            font=ctk.CTkFont(size=24, weight="bold"),
        )
        self.progress_label.pack(pady=4)

        self.speed_label = ctk.CTkLabel(
            self.progress_frame, text="",
            text_color=theme["text_secondary"],
            font=ctk.CTkFont(size=12),
        )
        self.speed_label.pack()

        # 日志区域
        log_header = ctk.CTkLabel(
            self, text="📋 下载日志",
            text_color=theme["text_primary"],
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        )
        log_header.pack(fill="x", padx=16, pady=(16, 4))

        self.log_text = ctk.CTkTextbox(
            self, height=150,
            fg_color=theme["bg_input"],
            text_color=theme["text_secondary"],
            font=ctk.CTkFont(size=11, family="Consolas"),
            wrap="word",
        )
        self.log_text.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        self.log_text.insert("end", "就绪。选择一个任务或开始嗅探。\n")

    def show_task(self, task: DownloadTask):
        self.current_task = task
        self._update_info()
        self._add_log(f"已选择任务: {task.title or task.url[:60]}")

    def update_progress(self, progress: float, speed: float, status: TaskStatus, downloaded: int = 0, total: int = 0):
        """从下载引擎接收进度更新"""
        self.progress_bar.set(progress / 100.0)
        self.progress_label.configure(text=f"{progress:.1f}%")

        if speed > 0:
            self.speed_label.configure(
                text=f"{format_bytes(int(speed * 1024))}/s — 剩余 {format_time(self.current_task.eta if self.current_task else 0)}"
            )
        elif status == TaskStatus.COMPLETED:
            self.speed_label.configure(text="✅ 下载完成")
        elif status == TaskStatus.FAILED:
            self.speed_label.configure(text="❌ 下载失败")
        elif status == TaskStatus.PAUSED:
            self.speed_label.configure(text="⏸ 已暂停")

        if self.current_task:
            self.current_task.progress = progress
            self.current_task.speed = speed
            self.current_task.status = status
            if downloaded > 0:
                self.current_task.downloaded_bytes = downloaded
            if total > 0:
                self.current_task.total_bytes = total

        self._update_info()

    def _update_info(self):
        if not self.current_task:
            return
        t = self.current_task
        theme = get_theme()

        info = [
            f"📌 标题: {t.title or '未知'}",
            f"🔗 类型: {t.video_type.value.upper()}",
            f"📊 状态: {STATUS_LABELS.get(t.status.value, '未知')}",
            f"📦 大小: {t.downloaded_str} / {t.size_str}",
            f"⚡ 速度: {f'{t.speed:.1f} KB/s' if t.speed > 0 else '--'}",
            f"📁 保存到: {t.save_path or '未设置'}",
            f"🕐 创建: {t.created_at[:19] if t.created_at else '--'}",
        ]
        if t.error_msg:
            info.append(f"⚠ 错误: {t.error_msg}")

        # 更新标题
        self.title_label.configure(
            text=t.title or "视频下载任务",
            text_color=theme["text_primary"],
        )

        # 更新信息行
        for i, line in enumerate(info):
            if i < len(self.info_lines):
                self.info_lines[i].configure(text=line)

    def add_log(self, msg: str):
        self._add_log(msg)

    def _add_log(self, msg: str):
        import time
        ts = time.strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {msg}\n")
        self.log_text.see("end")

    def clear(self):
        self.current_task = None
        self.title_label.configure(text="选择一个任务查看详情")
        self.progress_bar.set(0)
        self.progress_label.configure(text="0%")
        self.speed_label.configure(text="")
        for lbl in self.info_lines:
            lbl.configure(text="")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("end", "就绪。\n")
