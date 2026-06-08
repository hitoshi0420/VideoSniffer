"""视频分片合并模块 — AES 解密 + ffmpeg 合并"""

import os
import asyncio
import hashlib
import subprocess
from pathlib import Path
from Crypto.Cipher import AES
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


def decrypt_aes128(data: bytes, key: bytes, iv: bytes = None) -> bytes:
    """AES-128-CBC 解密"""
    if iv is None:
        iv = bytes(16)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(data)
    # 移除 PKCS7 填充
    pad_len = decrypted[-1]
    if pad_len <= 16:
        return decrypted[:-pad_len]
    return decrypted


async def fetch_key(key_uri: str, headers: dict = None) -> bytes:
    """获取解密密钥"""
    import aiohttp
    _headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    if headers:
        _headers.update(headers)
    async with aiohttp.ClientSession() as session:
        async with session.get(key_uri, headers=_headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            return await resp.read()


def get_key_iv(segment_key: dict, sequence: int) -> tuple[Optional[bytes], Optional[bytes]]:
    """从 segment key 信息获取 key 和 iv"""
    if not segment_key:
        return None, None
    method = segment_key.get("method", "")
    if "AES" not in method.upper():
        return None, None

    # iv 来自 segment key 或使用序列号
    iv_str = segment_key.get("iv")
    if iv_str:
        iv = bytes.fromhex(iv_str)
    else:
        iv = (sequence).to_bytes(16, "big")

    return None, iv  # key 需要异步获取


async def decrypt_segment(filepath: str, key_uri: str, iv: bytes = None, headers: dict = None) -> str:
    """解密单个 ts 分片"""
    key_data = await fetch_key(key_uri, headers)
    if not key_data:
        return filepath

    if iv is None:
        iv = bytes(16)

    with open(filepath, "rb") as f:
        encrypted = f.read()

    decrypted = decrypt_aes128(encrypted, key_data, iv)

    outpath = filepath.replace(".ts", ".dec.ts")
    with open(outpath, "wb") as f:
        f.write(decrypted)

    return outpath


def merge_with_ffmpeg(ts_files: list[str], output: str, quiet: bool = True) -> bool:
    """使用 ffmpeg 合并 ts 分片为 mp4"""
    concat_file = str(Path(output).with_suffix(".concat.txt"))
    with open(concat_file, "w", encoding="utf-8") as f:
        for ts in ts_files:
            escaped = ts.replace("\\", "\\\\").replace("'", "\\'")
            f.write(f"file '{escaped}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
    ]
    if quiet:
        cmd.extend(["-loglevel", "error"])
    cmd.append(output)

    try:
        subprocess.run(cmd, check=True, capture_output=quiet, timeout=600)
        if os.path.exists(concat_file):
            os.remove(concat_file)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"ffmpeg 合并失败: {e}")
        if os.path.exists(concat_file):
            os.remove(concat_file)
        return False


def check_ffmpeg() -> bool:
    """检查 ffmpeg 是否可用"""
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


def merge_ts_files(ts_files: list[str], output: str) -> bool:
    """直接拼接 ts 文件（不使用 ffmpeg，作为回退方案）"""
    try:
        with open(output, "wb") as out:
            for ts in ts_files:
                if os.path.exists(ts):
                    with open(ts, "rb") as f:
                        out.write(f.read())
        return True
    except Exception as e:
        logger.error(f"合并失败: {e}")
        return False
