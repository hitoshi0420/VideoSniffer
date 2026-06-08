"""视频下载引擎 — 异步多线程下载、断点续传、队列管理"""

import os
import shutil
import subprocess
import time
import asyncio
import aiohttp
import aiofiles
import threading
import traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional
from dataclasses import dataclass

import requests as req

from Crypto.Cipher import AES
from models.task import DownloadTask, TaskStatus, VideoType
from utils.helpers import ensure_dir, sanitize_filename, extract_domain
from utils.m3u8 import parse_m3u8, fetch_m3u8, M3u8Segment
from core.merger import merge_with_ffmpeg, check_ffmpeg, merge_ts_files
from config import settings
from utils.helpers import get_proxy
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ProgressInfo:
    task_id: str
    progress: float
    downloaded: int
    total: int
    speed: float
    status: TaskStatus
    error: str = ""


class DownloadEngine:
    """下载引擎 — 管理所有下载任务"""

    def __init__(self):
        self._queue: list[DownloadTask] = []
        self._active: dict[str, asyncio.Task] = {}
        self._callbacks: list[Callable[[ProgressInfo], None]] = []
        self._running = True
        self._max_concurrent = settings.get("network.download_threads", 4)
        self._speed_limit = settings.get("network.speed_limit", 0)  # KB/s
        self._save_dir = settings.get("video.download_path", "")
        self._loop: asyncio.AbstractEventLoop = None
        self._total_speed = 0.0
        self._lock = threading.Lock()

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def queue_count(self) -> int:
        return len(self._queue)

    @property
    def total_speed(self) -> float:
        return self._total_speed

    def add_callback(self, cb: Callable[[ProgressInfo], None]):
        self._callbacks.append(cb)

    def remove_callback(self, cb: Callable[[ProgressInfo], None]):
        if cb in self._callbacks:
            self._callbacks.remove(cb)

    def _notify(self, info: ProgressInfo):
        for cb in self._callbacks:
            try:
                cb(info)
            except Exception as exc:
                logger.debug(f"通知回调异常: {exc}")

    def submit(self, task: DownloadTask) -> str:
        """提交下载任务"""
        with self._lock:
            # 设置保存路径
            if not task.save_path:
                domain = sanitize_filename(extract_domain(task.url))
                name = sanitize_filename(task.title or "untitled")
                ext = ".mp4"
                if task.video_type == VideoType.M3U8:
                    ext = ".mp4"
                task.save_path = os.path.join(self._save_dir, domain, f"{name}{ext}")
            ensure_dir(os.path.dirname(task.save_path))
            # 为 m3u8 设置临时目录
            if task.video_type == VideoType.M3U8:
                task.temp_dir = task.save_path + ".tmp"
                ensure_dir(task.temp_dir)

            self._queue.append(task)
            self._process_queue()
            return task.id

    def _process_queue(self):
        """处理下载队列"""
        while len(self._active) < self._max_concurrent and self._queue:
            task = self._queue.pop(0)
            if self._loop and self._loop.is_running():
                async_task = asyncio.run_coroutine_threadsafe(self._download_task(task), self._loop)
                self._active[task.id] = async_task

    def start(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._running = True

    def stop(self):
        self._running = False
        for task_id, async_task in list(self._active.items()):
            async_task.cancel()
        self._active.clear()

    def pause_task(self, task_id: str):
        """暂停任务"""
        with self._lock:
            if task_id in self._active:
                self._active[task_id].cancel()
                del self._active[task_id]
            for task in self._queue:
                if task.id == task_id:
                    task.status = TaskStatus.PAUSED
                    self._notify(ProgressInfo(
                        task_id=task.id, progress=task.progress,
                        downloaded=task.downloaded_bytes, total=task.total_bytes,
                        speed=0, status=TaskStatus.PAUSED,
                    ))

    def resume_task(self, task: DownloadTask):
        """继续暂停的任务"""
        task.status = TaskStatus.WAITING
        self.submit(task)

    def cancel_task(self, task_id: str):
        """取消任务"""
        with self._lock:
            if task_id in self._active:
                self._active[task_id].cancel()
                del self._active[task_id]
            self._queue = [t for t in self._queue if t.id != task_id]

    async def _download_task(self, task: DownloadTask):
        """执行单个下载任务"""
        try:
            if task.video_type == VideoType.M3U8:
                await self._download_m3u8(task)
            else:
                await self._download_direct(task)
        except asyncio.CancelledError:
            task.status = TaskStatus.PAUSED
            self._notify(ProgressInfo(
                task_id=task.id, progress=task.progress,
                downloaded=task.downloaded_bytes, total=task.total_bytes,
                speed=0, status=TaskStatus.PAUSED,
            ))
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error_msg = str(e)
            logger.error(f"下载任务失败 [{task.title}]: {e}")
            self._notify(ProgressInfo(
                task_id=task.id, progress=task.progress,
                downloaded=task.downloaded_bytes, total=task.total_bytes,
                speed=0, status=TaskStatus.FAILED, error=str(e),
            ))
        finally:
            with self._lock:
                self._active.pop(task.id, None)
            self._process_queue()

    async def _download_direct(self, task: DownloadTask):
        """下载直链文件 — 支持断点续传; DASH 格式则下载视频+音频并合并"""
        # DASH 格式：同时下载视频和音频然后合并
        if task.video_format == "m4s" and task.audio_url:
            await self._download_dash(task)
            return
        await self._download_single(task)

    async def _download_single(self, task: DownloadTask):
        """下载单个直链文件，支持断点续传重试"""
        task.status = TaskStatus.DOWNLOADING
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": task.url,
            "Accept-Encoding": "identity",
        }
        if task.headers:
            headers.update(task.headers)

        proxy = get_proxy()

        # 获取文件大小（HEAD 可能不被 CDN 支持，失败不阻塞下载）
        if task.total_bytes == 0:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.head(task.url, headers=headers, proxy=proxy,
                                            timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status == 200:
                            task.total_bytes = int(resp.headers.get("Content-Length", 0))
            except Exception:
                pass

        last_error = None
        for attempt in range(4):
            # 断点续传（仅在已知总大小且文件未完成时续传）
            existing_size = 0
            if os.path.exists(task.save_path):
                existing_size = os.path.getsize(task.save_path)
                if existing_size == task.total_bytes and task.total_bytes > 0:
                    task.status = TaskStatus.COMPLETED
                    task.progress = 100.0
                    task.downloaded_bytes = task.total_bytes
                    self._notify(ProgressInfo(
                        task_id=task.id, progress=100, downloaded=task.total_bytes,
                        total=task.total_bytes, speed=0, status=TaskStatus.COMPLETED,
                    ))
                    return
                elif task.total_bytes > 0 and existing_size < task.total_bytes:
                    headers["Range"] = f"bytes={existing_size}-"
                    task.downloaded_bytes = existing_size
                else:
                    os.remove(task.save_path)
                    headers.pop("Range", None)
                    task.downloaded_bytes = 0
            else:
                headers.pop("Range", None)
                task.downloaded_bytes = 0

            try:
                connector = aiohttp.TCPConnector(limit=0, force_close=False)
                session_timeout = aiohttp.ClientTimeout(total=settings.get("network.timeout", 30), connect=10, sock_read=60)
                async with aiohttp.ClientSession(connector=connector, timeout=session_timeout) as session:
                    async with session.get(task.url, headers=headers, proxy=proxy,
                                           timeout=aiohttp.ClientTimeout(total=3600, sock_read=30)) as resp:
                        if resp.status not in (200, 206):
                            raise aiohttp.ClientResponseError(
                                resp.request_info, resp.history,
                                status=resp.status, message=f"HTTP {resp.status}",
                                headers=resp.headers,
                            )
                        mode = "ab" if existing_size > 0 else "wb"
                        async with aiofiles.open(task.save_path, mode) as f:
                            start_time = time.time()
                            session_bytes = 0
                            last_notify = 0

                            async for chunk in resp.content.iter_chunked(1 * 1024 * 1024):
                                await f.write(chunk)
                                task.downloaded_bytes += len(chunk)
                                session_bytes += len(chunk)

                                if self._speed_limit > 0:
                                    elapsed = time.time() - start_time
                                    expected = session_bytes / (self._speed_limit * 1024)
                                    if elapsed < expected:
                                        await asyncio.sleep(expected - elapsed)

                                elapsed = time.time() - start_time
                                now = time.time()
                                if now - last_notify >= 0.3:
                                    last_notify = now
                                    task.speed = session_bytes / elapsed / 1024 if elapsed > 0 else 0
                                    if task.total_bytes > 0:
                                        task.progress = (task.downloaded_bytes / task.total_bytes) * 100
                                    self._notify(ProgressInfo(
                                        task_id=task.id, progress=task.progress,
                                        downloaded=task.downloaded_bytes, total=task.total_bytes,
                                        speed=task.speed, status=TaskStatus.DOWNLOADING,
                                    ))

                # 下载成功
                task.status = TaskStatus.COMPLETED
                task.progress = 100.0
                self._notify(ProgressInfo(
                    task_id=task.id, progress=100, downloaded=task.downloaded_bytes,
                    total=task.total_bytes, speed=0, status=TaskStatus.COMPLETED,
                ))
                return

            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                last_error = e
                if attempt < 3:
                    await asyncio.sleep(3 * (attempt + 1))
                    continue
                raise

        raise last_error

    async def _download_dash(self, task: DownloadTask):
        """下载 DASH 格式（B站等）- 视频+音频分开下载后用 ffmpeg 合并"""

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
            "Accept-Encoding": "identity",
        }
        if task.headers:
            headers.update(task.headers)

        video_tmp = task.save_path + ".video.m4s"
        audio_tmp = task.save_path + ".audio.m4s"

        task.status = TaskStatus.DOWNLOADING
        task._start_time = time.time()
        total_downloaded = 0

        async def _download_file(session, url, save_path, label, start_progress, end_progress):
            """异步下载单个文件，带重试和断点续传"""
            nonlocal total_downloaded
            last_exc = None
            for attempt in range(3):
                try:
                    resume_size = 0
                    if os.path.exists(save_path):
                        resume_size = os.path.getsize(save_path)
                    headers_resume = dict(headers)
                    if resume_size > 0:
                        headers_resume["Range"] = f"bytes={resume_size}-"

                    async with session.get(url, headers=headers_resume, proxy=proxy,
                                           timeout=aiohttp.ClientTimeout(connect=15, sock_read=300)) as resp:
                        resp.raise_for_status()
                        expected_size = int(resp.headers.get("Content-Length", 0))
                        downloaded = resume_size
                        last_notify = 0
                        mode = "ab" if resume_size > 0 else "wb"
                        with open(save_path, mode) as f:
                            async for chunk in resp.content.iter_chunked(1024 * 1024):
                                f.write(chunk)
                                downloaded += len(chunk)
                                now = time.time()
                                if now - last_notify >= 0.3:
                                    last_notify = now
                                    task.downloaded_bytes = total_downloaded + downloaded - resume_size
                                    if task.total_bytes > 0:
                                        task.progress = (task.downloaded_bytes / task.total_bytes) * 100
                                    elapsed = max(1, time.time() - task._start_time)
                                    task.speed = task.downloaded_bytes / 1024 / elapsed
                                    # 对 206 响应，Content-Length 仅包含本次范围大小
                                    range_total = expected_size + resume_size if resp.status == 206 else expected_size
                                    progress_pct = start_progress
                                    if range_total > 0:
                                        progress_pct = start_progress + (downloaded / range_total) * (end_progress - start_progress)
                                    self._notify(ProgressInfo(
                                        task_id=task.id, progress=progress_pct,
                                        downloaded=task.downloaded_bytes, total=task.total_bytes,
                                        speed=task.speed, status=TaskStatus.DOWNLOADING,
                                    ))
                        # 完整文件大小 = 已续传量 + Content-Length（206）或 Content-Length（200）
                        if resp.status == 206:
                            file_total = resume_size + expected_size
                        else:
                            file_total = expected_size
                        if file_total > 0 and downloaded < file_total * 0.95:
                            raise IOError(f"{label}不完整: 下载 {downloaded} / 预期 {file_total} 字节，重试...")
                        return downloaded - resume_size  # 返回本次新下载的字节数
                except (aiohttp.ClientError, IOError, asyncio.TimeoutError) as e:
                    last_exc = e
                    if attempt < 2:
                        await asyncio.sleep(3 * (attempt + 1))
            raise last_exc

        connector = aiohttp.TCPConnector(limit=0, force_close=False)
        timeout = aiohttp.ClientTimeout(total=settings.get("network.timeout", 30), connect=10)
        proxy = get_proxy()
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            # 预估总大小（HEAD 可能不被 CDN 支持，失败不阻塞下载）
            try:
                async with session.head(task.url, headers=headers, proxy=proxy,
                                        timeout=aiohttp.ClientTimeout(total=15)) as h:
                    video_size = int(h.headers.get("Content-Length", 0))
                audio_size = 0
                if task.audio_url:
                    async with session.head(task.audio_url, headers=headers, proxy=proxy,
                                            timeout=aiohttp.ClientTimeout(total=15)) as h2:
                        audio_size = int(h2.headers.get("Content-Length", 0))
                task.total_bytes = video_size + audio_size
            except Exception:
                pass  # HEAD 失败不阻塞，直接下载

            # 下载视频流
            self._notify(ProgressInfo(task_id=task.id, progress=5, downloaded=0,
                                       total=task.total_bytes, speed=0, status=TaskStatus.DOWNLOADING))
            try:
                video_downloaded = await _download_file(session, task.url, video_tmp, "视频流", 5, 50)
                total_downloaded += video_downloaded
            except Exception as e:
                task.status = TaskStatus.FAILED
                task.error_msg = f"视频流下载失败: {e}"
                logger.error(f"DASH 视频流下载失败 [{task.title}]: {e}")
                self._notify(ProgressInfo(task_id=task.id, progress=0, downloaded=0,
                                          total=0, speed=0, status=TaskStatus.FAILED, error=task.error_msg))
                return

            # 下载音频流
            if task.audio_url:
                try:
                    audio_downloaded = await _download_file(session, task.audio_url, audio_tmp, "音频流", 50, 95)
                    total_downloaded += audio_downloaded
                except Exception as e:
                    task.status = TaskStatus.FAILED
                    task.error_msg = f"音频流下载失败: {e}"
                    logger.error(f"DASH 音频流下载失败 [{task.title}]: {e}")
                    self._notify(ProgressInfo(task_id=task.id, progress=50, downloaded=total_downloaded,
                                              total=0, speed=0, status=TaskStatus.FAILED, error=task.error_msg))
                    return

        # ffmpeg 合并（在线程池执行避免阻塞事件循环）
        task.status = TaskStatus.MERGING
        self._notify(ProgressInfo(task_id=task.id, progress=95, downloaded=total_downloaded,
                                   total=total_downloaded, speed=0, status=TaskStatus.MERGING))

        if check_ffmpeg():
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._run_ffmpeg_merge, video_tmp, audio_tmp, task.save_path
            )
            if result is True:
                task.status = TaskStatus.COMPLETED
                task.progress = 100.0
                task.downloaded_bytes = total_downloaded
                task.total_bytes = total_downloaded
            else:
                task.status = TaskStatus.FAILED
                task.error_msg = f"ffmpeg 合并失败: {result}"
        else:
            shutil.move(video_tmp, task.save_path)
            task.status = TaskStatus.COMPLETED
            task.progress = 100.0

        # 清理临时文件
        for tmp in [video_tmp, audio_tmp]:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception as exc:
                    logger.debug(f"清理临时文件失败 {tmp}: {exc}")

        self._notify(ProgressInfo(
            task_id=task.id, progress=100 if task.status == TaskStatus.COMPLETED else 95,
            downloaded=task.downloaded_bytes, total=task.total_bytes,
            speed=0, status=task.status, error=task.error_msg,
        ))

    @staticmethod
    def _run_ffmpeg_merge(video_tmp, audio_tmp, output_path):
        """在线程池中运行 ffmpeg 合并（同步调用）"""
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", video_tmp, "-i", audio_tmp,
            "-c", "copy", output_path,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=300)
            if result.returncode == 0:
                return True
            return result.stderr.decode()[:200]
        except subprocess.TimeoutExpired:
            return "ffmpeg 合并超时"
        except Exception as e:
            return str(e)

    async def _download_m3u8(self, task: DownloadTask):
        """下载 m3u8 流 — 使用 requests + 线程池避免 aiohttp DNS 问题"""

        if not task.segments:
            content = await fetch_m3u8(task.url, task.headers)
            playlist = parse_m3u8(content, task.url)
            task.segments = [{"url": s.url, "duration": s.duration, "key": s.key} for s in playlist.segments]
            task.total_bytes = len(task.segments) * 500_000

        ts_dir = task.temp_dir or (task.save_path + ".tmp")
        ensure_dir(ts_dir)

        task.status = TaskStatus.DOWNLOADING
        total = len(task.segments)
        downloaded_files = []

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": task.url,
            "Accept-Encoding": "identity",
        }
        if task.headers:
            headers.update(task.headers)

        def _dl_segment(i: int, seg: dict):
            url = seg["url"]
            seg_key = seg.get("key")
            ts_path = os.path.join(ts_dir, f"seg_{i:06d}.ts")
            if os.path.exists(ts_path) and os.path.getsize(ts_path) > 0:
                return (i, ts_path, os.path.getsize(ts_path))
            for attempt in range(4):
                try:
                    r = req.get(url, headers=headers, timeout=(10, 30))
                    if r.status_code == 429:
                        time.sleep(min(8, 1.5 ** attempt))
                        continue
                    if r.status_code == 200 and len(r.content) > 0:
                        data = r.content
                        # AES 解密
                        if seg_key and "AES" in seg_key.get("method", "").upper():
                            key_uri = seg_key.get("uri")
                            if key_uri:
                                try:
                                    key_resp = req.get(key_uri, headers=headers, timeout=(10, 15))
                                    key_data = key_resp.content
                                except Exception:
                                    key_data = None
                                if key_data:
                                    iv_str = seg_key.get("iv")
                                    if iv_str:
                                        iv = bytes.fromhex(iv_str)
                                    else:
                                        iv = i.to_bytes(16, "big")
                                    cipher = AES.new(key_data, AES.MODE_CBC, iv)
                                    data = cipher.decrypt(data)
                                    pad_len = data[-1]
                                    if pad_len <= 16:
                                        data = data[:-pad_len]
                        with open(ts_path, "wb") as f:
                            f.write(data)
                        return (i, ts_path, len(data))
                    if len(r.content) == 0 and attempt < 3:
                        time.sleep(1)
                        continue
                except Exception:
                    if attempt < 3:
                        time.sleep(0.5 * (attempt + 1))
                        continue
                return (i, None, 0)
            return (i, None, 0)

        # 使用线程池并发下载（m3u8 分片通常需要保守并发）
        _cfg_threads = settings.get("network.download_threads", 20)
        max_workers = min(_cfg_threads, 16)  # 上限 16 避免被服务器 429
        completed_count = 0
        total_downloaded = 0
        start_time = time.time()
        last_notify = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_dl_segment, i, seg): i
                       for i, seg in enumerate(task.segments)}
            for future in as_completed(futures):
                seg_idx, ts_path, seg_size = future.result()
                if ts_path:
                    downloaded_files.append((seg_idx, ts_path))
                completed_count += 1
                total_downloaded += seg_size
                elapsed = time.time() - start_time
                task.segment_index = completed_count
                task.progress = (completed_count / total) * 90
                task.downloaded_bytes = total_downloaded
                task.speed = (total_downloaded / elapsed / 1024) if elapsed > 0.5 else 0
                # 每 0.3 秒通知一次 GUI
                if time.time() - last_notify >= 0.3 or completed_count == total:
                    last_notify = time.time()
                    self._notify(ProgressInfo(
                        task_id=task.id, progress=task.progress,
                        downloaded=task.downloaded_bytes, total=task.total_bytes,
                        speed=task.speed, status=TaskStatus.DOWNLOADING,
                    ))

        # 检查下载完整性（允许少量缺失，ffmpeg 可处理）
        missing = total - len(downloaded_files)
        if missing > total * 0.1:
            task.status = TaskStatus.FAILED
            task.error_msg = f"下载不完整: {len(downloaded_files)}/{total} 分片成功，{missing} 个失败"
            self._notify(ProgressInfo(
                task_id=task.id, progress=task.progress,
                downloaded=task.downloaded_bytes, total=task.total_bytes,
                speed=0, status=TaskStatus.FAILED, error=task.error_msg,
            ))
            return

        # 合并分片（按索引排序）
        downloaded_files.sort(key=lambda x: x[0])
        ts_paths = [p for _, p in downloaded_files]
        logger.info(f"合并 {len(ts_paths)}/{total} 个分片 (缺失 {missing})")

        task.status = TaskStatus.MERGING
        self._notify(ProgressInfo(
            task_id=task.id, progress=90, downloaded=task.downloaded_bytes,
            total=task.total_bytes, speed=0, status=TaskStatus.MERGING,
        ))

        ensure_dir(os.path.dirname(task.save_path))
        if check_ffmpeg():
            merge_with_ffmpeg(ts_paths, task.save_path)
        else:
            merge_ts_files(ts_paths, task.save_path)

        # 清理临时文件
        if not settings.get("video.keep_temp_files", False):
            try:
                shutil.rmtree(ts_dir)
            except Exception as exc:
                logger.debug(f"清理 m3u8 临时目录失败 {ts_dir}: {exc}")

        task.status = TaskStatus.COMPLETED
        task.progress = 100.0
        task.downloaded_bytes = task.total_bytes
        self._notify(ProgressInfo(
            task_id=task.id, progress=100, downloaded=task.total_bytes,
            total=task.total_bytes, speed=0, status=TaskStatus.COMPLETED,
        ))

    def get_all_tasks(self) -> list[DownloadTask]:
        """获取所有任务（活跃 + 队列中）"""
        with self._lock:
            return list(self._active.keys()) + [t for t in self._queue]


# 全局单例
engine = DownloadEngine()
