"""任务列表面板 — 左侧下载任务列表"""

import customtkinter as ctk
from typing import Callable, Optional
from models.task import DownloadTask, TaskStatus, STATUS_LABELS
from gui.styles import get_theme, status_color


class TaskListItem(ctk.CTkFrame):
    """单个任务列表项"""

    def __init__(self, master, task: DownloadTask, on_select: Callable = None, **kwargs):
        super().__init__(master, fg_color=get_theme()["bg_secondary"], corner_radius=8, **kwargs)
        self.task = task
        self.on_select = on_select
        self._build()

    def _build(self):
        theme = get_theme()

        # 状态指示点
        self.status_dot = ctk.CTkLabel(
            self, text="●", text_color=status_color(self.task.status.value),
            font=ctk.CTkFont(size=14), width=20,
        )
        self.status_dot.pack(side="left", padx=(8, 4), pady=8)

        # 中间信息
        info_frame = ctk.CTkFrame(self, fg_color="transparent")
        info_frame.pack(side="left", fill="both", expand=True, padx=4, pady=6)

        title = self.task.title or "未命名视频"
        if len(title) > 30:
            title = title[:27] + "..."

        self.title_label = ctk.CTkLabel(
            info_frame, text=title,
            text_color=theme["text_primary"],
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        )
        self.title_label.pack(fill="x")

        # 状态和进度行
        status_frame = ctk.CTkFrame(info_frame, fg_color="transparent")
        status_frame.pack(fill="x", pady=(2, 0))

        status_text = STATUS_LABELS.get(self.task.status.value, "未知")

        self.status_label = ctk.CTkLabel(
            status_frame, text=status_text,
            text_color=status_color(self.task.status.value),
            font=ctk.CTkFont(size=11),
        )
        self.status_label.pack(side="left")

        if self.task.speed > 0:
            speed_text = f"{self.task.speed:.1f} KB/s"
        elif self.task.status == TaskStatus.COMPLETED:
            speed_text = self.task.size_str
        else:
            speed_text = ""

        if speed_text:
            self.speed_label = ctk.CTkLabel(
                status_frame, text=speed_text,
                text_color=theme["text_muted"],
                font=ctk.CTkFont(size=10),
            )
            self.speed_label.pack(side="right")

        # 进度条
        if self.task.status in (TaskStatus.DOWNLOADING, TaskStatus.MERGING):
            self.progress_bar = ctk.CTkProgressBar(
                self, width=80, height=6,
                progress_color=theme["accent"],
                fg_color=theme["progress_bg"],
            )
            self.progress_bar.set(self.task.progress / 100.0)
            self.progress_bar.pack(side="right", padx=8, pady=8)

        # 百分比标签
        if self.task.progress > 0 and self.task.progress < 100:
            self.pct_label = ctk.CTkLabel(
                self, text=f"{self.task.progress:.1f}%",
                text_color=theme["text_secondary"],
                font=ctk.CTkFont(size=10), width=40,
            )
            self.pct_label.pack(side="right", padx=(0, 4), pady=8)

        # 绑定点击事件
        self.bind("<Button-1>", self._on_click)
        for child in self.winfo_children():
            child.bind("<Button-1>", self._on_click)

    def _on_click(self, event=None):
        if self.on_select:
            self.on_select(self.task)

    def update_info(self, progress: float = None, speed: float = None, status: TaskStatus = None):
        theme = get_theme()

        if status:
            self.task.status = status
            self.status_label.configure(text=STATUS_LABELS.get(status.value, "未知"))
            self.status_dot.configure(text_color=status_color(status.value))

        if progress is not None:
            self.task.progress = progress
            if hasattr(self, "progress_bar"):
                self.progress_bar.set(progress / 100.0)
            if hasattr(self, "pct_label"):
                self.pct_label.configure(text=f"{progress:.1f}%")

        if speed is not None:
            self.task.speed = speed
            if hasattr(self, "speed_label") and speed > 0:
                self.speed_label.configure(text=f"{speed:.1f} KB/s")


class TaskListPanel(ctk.CTkScrollableFrame):
    """任务列表面板"""

    def __init__(self, master, on_task_select: Callable = None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.on_task_select = on_task_select
        self.items: dict[str, TaskListItem] = {}
        self._build_header()

    def _build_header(self):
        theme = get_theme()
        header = ctk.CTkLabel(
            self, text="📥 下载任务",
            text_color=theme["text_primary"],
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
        )
        header.pack(fill="x", padx=12, pady=(8, 4))

    def add_task(self, task: DownloadTask) -> TaskListItem:
        item = TaskListItem(self, task, on_select=self.on_task_select)
        item.pack(fill="x", padx=8, pady=3)
        self.items[task.id] = item
        return item

    def remove_task(self, task_id: str):
        if task_id in self.items:
            self.items[task_id].destroy()
            del self.items[task_id]

    def update_task(self, task_id: str, progress: float = None, speed: float = None, status: TaskStatus = None):
        if task_id in self.items:
            self.items[task_id].update_info(progress=progress, speed=speed, status=status)

    def clear(self):
        for item in self.items.values():
            item.destroy()
        self.items.clear()
