"""统一日志模块"""

import logging
import sys

_logger: logging.Logger | None = None


def get_logger(name: str = "VideoSniffer") -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    _logger = logging.getLogger(name)
    _logger.setLevel(logging.DEBUG)

    # 控制台 handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    _logger.addHandler(ch)

    # 文件 handler（写入 .video_sniffer 目录）
    try:
        from config.settings import CONFIG_DIR
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(
            CONFIG_DIR / "debug.log", encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)s %(name)s:%(lineno)d: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        _logger.addHandler(fh)
    except Exception:
        pass

    return _logger
