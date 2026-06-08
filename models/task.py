"""下载任务数据模型"""

from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import uuid

from utils.helpers import format_bytes


class TaskStatus(Enum):
    WAITING = "waiting"
    DOWNLOADING = "downloading"
    MERGING = "merging"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class VideoType(Enum):
    DIRECT = "direct"       # 直链 (.mp4, .webm 等)
    M3U8 = "m3u8"          # HLS 流
    MPD = "mpd"            # DASH 流
    PLATFORM = "platform"  # 平台专属 (B站/YouTube 等)


@dataclass
class DownloadTask:
    url: str
    title: str = ""
    video_type: VideoType = VideoType.DIRECT
    status: TaskStatus = TaskStatus.WAITING
    progress: float = 0.0          # 0.0 - 100.0
    downloaded_bytes: int = 0
    total_bytes: int = 0
    speed: float = 0.0             # KB/s
    quality: str = ""
    duration: float = 0.0          # 秒
    save_path: str = ""
    temp_dir: str = ""
    error_msg: str = ""
    segments: list = field(default_factory=list)   # m3u8 分片列表
    segment_index: int = 0         # 当前下载的分片索引
    audio_url: str = ""            # DASH 格式的音频地址
    video_format: str = ""         # "m4s" / "ts" 等
    headers: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    retry_count: int = 0

    @property
    def is_active(self) -> bool:
        return self.status in (TaskStatus.DOWNLOADING, TaskStatus.MERGING)

    @property
    def is_finished(self) -> bool:
        return self.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)

    @property
    def eta(self) -> float:
        """剩余时间（秒）"""
        if self.speed <= 0 or self.total_bytes <= 0:
            return float("inf")
        remaining = self.total_bytes - self.downloaded_bytes
        return remaining / (self.speed * 1024)

    @property
    def size_str(self) -> str:
        return format_bytes(self.total_bytes)

    @property
    def downloaded_str(self) -> str:
        return format_bytes(self.downloaded_bytes)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "video_type": self.video_type.value,
            "status": self.status.value,
            "progress": self.progress,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "speed": self.speed,
            "quality": self.quality,
            "duration": self.duration,
            "save_path": self.save_path,
            "error_msg": self.error_msg,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DownloadTask":
        return cls(
            id=d.get("id", ""),
            url=d.get("url", ""),
            title=d.get("title", ""),
            video_type=VideoType(d.get("video_type", "direct")),
            status=TaskStatus(d.get("status", "waiting")),
            progress=d.get("progress", 0.0),
            downloaded_bytes=d.get("downloaded_bytes", 0),
            total_bytes=d.get("total_bytes", 0),
            speed=d.get("speed", 0.0),
            quality=d.get("quality", ""),
            duration=d.get("duration", 0.0),
            save_path=d.get("save_path", ""),
            error_msg=d.get("error_msg", ""),
            created_at=d.get("created_at", ""),
            completed_at=d.get("completed_at", ""),
        )


STATUS_LABELS = {
    "waiting": "等待中",
    "downloading": "下载中",
    "merging": "合并中",
    "paused": "已暂停",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
}
