"""AI 画质增强模块 — 使用 Real-ESRGAN ncnn-vulkan 超分辨率"""

import os
import re
import shutil
import asyncio
from pathlib import Path
from dataclasses import dataclass, field

from utils.logger import get_logger

logger = get_logger(__name__)

_TOOLS_DIR = Path(__file__).parent.parent / "tools" / "realesrgan"
_ESRGAN_EXE = str(_TOOLS_DIR / "realesrgan-ncnn-vulkan.exe")
_MODELS_DIR = str(_TOOLS_DIR / "models")

MODELS = {
    4: "realesrgan-x4plus",
    2: "realesrgan-x4plus-anime",
}

MIN_FRAME_SIZE = 1024  # 小于 1KB 的帧视为损坏


@dataclass
class UpscaleProgress:
    """增强进度"""
    stage: str = ""           # extracting / upscaling / merging / done
    progress: float = 0.0     # 0-100
    frame: int = 0
    total_frames: int = 0
    error: str = ""           # 错误信息


def check_esrgan() -> bool:
    return os.path.exists(_ESRGAN_EXE)


async def upscale_video(
    input_path: str,
    output_path: str = "",
    scale: int = 4,
    on_progress=None,
    keep_temp: bool = False,
) -> str:
    """AI 超分辨率增强视频

    Args:
        input_path: 输入视频路径
        output_path: 输出路径（默认在原文件名后加 _Nx）
        scale: 缩放倍数（2 或 4）
        on_progress: 进度回调 async def callback(UpscaleProgress)
        keep_temp: 保留临时目录（调试用）

    Returns:
        增强后的视频路径
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"输入视频不存在: {input_path}")

    if not output_path:
        stem = os.path.splitext(input_path)[0]
        output_path = f"{stem}_{scale}x.mp4"

    temp_dir = output_path + ".esrgan_tmp"
    frames_dir = os.path.join(temp_dir, "frames_in")
    frames_out_dir = os.path.join(temp_dir, "frames_out")
    audio_file = os.path.join(temp_dir, "audio.aac")

    os.makedirs(frames_dir, exist_ok=True)
    os.makedirs(frames_out_dir, exist_ok=True)

    error_occurred = False

    try:
        # ===== 1. 获取视频信息 + 提取音频 =====
        await _notify(on_progress, UpscaleProgress(stage="extracting", progress=0))
        fps = await _get_fps(input_path)
        total_frames = await _get_frame_count(input_path)
        logger.info(f"视频: {input_path}, fps={fps:.2f}, 估帧数={total_frames}")
        await _extract_audio(input_path, audio_file)

        # ===== 2. 提取全部帧 =====
        await _notify(on_progress, UpscaleProgress(stage="extracting", progress=1, total_frames=total_frames))
        await _extract_all_frames(input_path, frames_dir)

        frame_files = sorted([f for f in os.listdir(frames_dir) if f.endswith(".png")])
        actual_frames = len(frame_files)
        logger.info(f"提取帧: {actual_frames} 个 PNG")

        if actual_frames == 0:
            raise RuntimeError("ffmpeg 未能从视频中提取到任何帧")
        if total_frames <= 0:
            total_frames = actual_frames

        # 验证提取的帧是否有效
        first_size = os.path.getsize(os.path.join(frames_dir, frame_files[0]))
        logger.info(f"首帧大小: {first_size} bytes, 示例: {frame_files[0]}")

        # ===== 3. AI 超分 =====
        model_name = MODELS.get(scale, "realesrgan-x4plus")
        logger.info(f"启动 ESRGAN: model={model_name}, scale={scale}")

        await _run_esrgan(
            frames_dir, frames_out_dir, scale, model_name, on_progress,
            start_pct=2, end_pct=94,
            total_frames=actual_frames,
        )

        out_frames = sorted([f for f in os.listdir(frames_out_dir) if f.endswith(".png")])
        logger.info(f"ESRGAN 输出: {len(out_frames)} 帧, 示例: {out_frames[:5]}")

        if not out_frames:
            raise RuntimeError(
                f"ESRGAN 未生成任何输出帧。请检查:\n"
                f"  1. GPU 驱动是否支持 Vulkan\n"
                f"  2. 输入帧是否有效\n"
                f"  3. 临时目录: {temp_dir}"
            )

        # ===== 4. 验证输出帧质量 =====
        bad_frames = 0
        total_size = 0
        for name in out_frames:
            sz = os.path.getsize(os.path.join(frames_out_dir, name))
            total_size += sz
            if sz < MIN_FRAME_SIZE:
                bad_frames += 1

        avg_size = total_size / len(out_frames) if out_frames else 0
        logger.info(f"帧验证: {len(out_frames)} 帧, 平均 {avg_size:.0f} bytes, 损坏 {bad_frames}")

        if bad_frames > len(out_frames) * 0.5:
            raise RuntimeError(
                f"超过半数输出帧异常小 (< {MIN_FRAME_SIZE} bytes)，"
                f"ESRGAN 可能未正常工作。临时目录: {temp_dir}"
            )

        # ===== 5. 重命名帧为统一格式 =====
        renamed_dir = os.path.join(temp_dir, "frames_renamed")
        os.makedirs(renamed_dir, exist_ok=True)
        for i, old_name in enumerate(out_frames):
            shutil.move(
                os.path.join(frames_out_dir, old_name),
                os.path.join(renamed_dir, f"frame_{i:06d}.png"),
            )
        logger.info(f"帧重命名完成: {len(out_frames)} → {renamed_dir}")

        # ===== 6. 合并为视频 =====
        await _notify(on_progress, UpscaleProgress(
            stage="merging", progress=95, total_frames=total_frames,
        ))
        await _merge_frames_to_video(renamed_dir, output_path, fps,
                                     audio_file if _audio_valid(audio_file) else "")

        out_size_mb = os.path.getsize(output_path) / (1024 * 1024) if os.path.exists(output_path) else 0
        logger.info(f"输出视频: {output_path}, {out_size_mb:.1f} MB")

        await _notify(on_progress, UpscaleProgress(
            stage="done", progress=100, frame=total_frames, total_frames=total_frames,
        ))
        return output_path

    except Exception as e:
        error_occurred = True
        logger.error(f"增强失败: {e}")
        await _notify(on_progress, UpscaleProgress(stage="done", progress=0, error=str(e)))
        raise

    finally:
        if not keep_temp and not error_occurred:
            _rmdir(temp_dir)
        elif keep_temp or error_occurred:
            logger.info(f"临时目录已保留: {temp_dir}")


# ====== 工具函数 ======

def _audio_valid(path: str) -> bool:
    return os.path.exists(path) and os.path.getsize(path) > 0


def _rmdir(path: str):
    """安全删除目录"""
    if os.path.exists(path):
        try:
            shutil.rmtree(path)
        except Exception as e:
            logger.debug(f"清理失败 {path}: {e}")


async def _notify(callback, progress: UpscaleProgress):
    if callback is None:
        return
    try:
        result = callback(progress)
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        pass


# ====== ffprobe ======

async def _get_frame_count(video_path: str) -> int:
    """获取视频总帧数，多重回退"""
    # 方法1: nb_read_frames
    try:
        cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-count_frames", "-show_entries", "stream=nb_read_frames",
            "-of", "default=nokey=1:noprint_wrappers=1", video_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        count = int(stdout.decode().strip() or 0)
        if count > 0:
            return count
    except Exception:
        pass

    # 方法2: duration * fps
    try:
        cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate,duration",
            "-of", "default=nokey=1:noprint_wrappers=1", video_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        lines = stdout.decode().strip().splitlines()
        fps_str = lines[0] if lines else "30/1"
        dur_str = lines[1] if len(lines) > 1 else "0"
        num, den = fps_str.split("/") if "/" in fps_str else (fps_str, "1")
        fps = float(num) / float(den) if float(den) != 0 else 30
        duration = float(dur_str) if dur_str else 0
        return max(1, int(duration * fps))
    except Exception:
        return 0


async def _get_fps(video_path: str) -> float:
    """获取视频帧率"""
    try:
        cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate",
            "-of", "default=nokey=1:noprint_wrappers=1", video_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        fps_str = stdout.decode().strip()
        if "/" in fps_str:
            num, den = fps_str.split("/")
            return float(num) / float(den)
        return float(fps_str) if fps_str else 30.0
    except Exception:
        return 30.0


# ====== 音视频处理 ======

async def _extract_audio(video_path: str, audio_path: str):
    """提取音频轨"""
    try:
        cmd = [
            "ffmpeg", "-y", "-loglevel", "warning",
            "-i", video_path, "-vn", "-acodec", "copy", audio_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if stderr:
            logger.debug(f"提取音频: {stderr.decode(errors='replace')[:200]}")
    except Exception as e:
        logger.debug(f"提取音频失败: {e}")


async def _extract_all_frames(video_path: str, frames_dir: str):
    """提取视频所有帧为 PNG"""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "warning",
        "-i", video_path,
        "-start_number", "0",
        os.path.join(frames_dir, "frame_%06d.png"),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if stderr:
        text = stderr.decode(errors="replace")
        if "error" in text.lower():
            logger.error(f"拆帧错误: {text[:500]}")
        else:
            logger.info(f"拆帧: {text.strip()[:200]}")


# ====== Real-ESRGAN ======

async def _run_esrgan(input_dir: str, output_dir: str, scale: int, model: str,
                      on_progress, start_pct: float, end_pct: float,
                      total_frames: int):
    """运行 Real-ESRGAN，同时监控输出目录帧数反映真实进度"""
    cmd = [
        _ESRGAN_EXE,
        "-i", input_dir,
        "-o", output_dir,
        "-s", str(scale),
        "-n", model,
        "-m", _MODELS_DIR,
        "-f", "png",
        "-j", "8:8:8",
    ]
    logger.info(f"ESRGAN 命令: {' '.join(cmd)}")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    prog_re = re.compile(rb"(\d+\.?\d*)%")
    stdout_pct = 0.0

    async def _read_stdout():
        nonlocal stdout_pct
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            match = prog_re.search(line)
            if match:
                stdout_pct = float(match.group(1))

    stdout_task = asyncio.ensure_future(_read_stdout())

    last_frames_done = -1
    last_notify = 0.0

    try:
        while proc.returncode is None:
            try:
                frames_done = len([f for f in os.listdir(output_dir)
                                   if f.endswith(".png")])
            except Exception:
                frames_done = 0

            dir_pct = (frames_done / total_frames * 100) if total_frames > 0 else 0
            current_pct = max(dir_pct, stdout_pct)

            if current_pct - last_notify >= 1.0 and frames_done != last_frames_done:
                last_notify = current_pct
                last_frames_done = frames_done
                overall = start_pct + (current_pct / 100.0) * (end_pct - start_pct)
                await _notify(on_progress, UpscaleProgress(
                    stage="upscaling", progress=min(overall, end_pct),
                    frame=min(frames_done, total_frames),
                    total_frames=total_frames,
                ))

            await asyncio.sleep(0.5)

    finally:
        stdout_task.cancel()
        try:
            await stdout_task
        except asyncio.CancelledError:
            pass

    await proc.wait()

    if proc.returncode != 0:
        logger.error(f"ESRGAN 退出码={proc.returncode}")
    else:
        logger.info("ESRGAN 进程正常退出")


# ====== 合并 ======

async def _merge_frames_to_video(frames_dir: str, output_path: str, fps: float,
                                 audio_path: str = ""):
    """帧序列合并为视频，可选合成音频"""
    input_pattern = os.path.join(frames_dir, "frame_%06d.png")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "warning",
        "-start_number", "0",
        "-r", str(fps),
        "-i", input_pattern,
    ]
    if audio_path:
        cmd += ["-i", audio_path]
    cmd += [
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
    ]
    if audio_path:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd += [output_path]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if stderr:
        text = stderr.decode(errors="replace")
        logger.info(f"合并: {text.strip()[:300]}")
    if proc.returncode != 0:
        logger.error(f"ffmpeg 合并失败, 退出码={proc.returncode}")
