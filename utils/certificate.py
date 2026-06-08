"""HTTPS 根证书管理 — 用于 mitmproxy 中间人代理"""

import os
import subprocess
import sys
from pathlib import Path
from config.settings import CONFIG_DIR

CERT_DIR = CONFIG_DIR / "certs"
CA_CERT = CERT_DIR / "mitmproxy-ca-cert.pem"
CA_KEY = CERT_DIR / "mitmproxy-ca-key.pem"


def ensure_certificate() -> bool:
    """确保 CA 证书存在，不存在则生成"""
    if CA_CERT.exists() and CA_KEY.exists():
        return True
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    return _generate_certificate()


def _generate_certificate() -> bool:
    """使用 mitmproxy 生成 CA 证书"""
    try:
        import mitmproxy.certs
        result = mitmproxy.certs.dummy_cert_dir(CERT_DIR)
        if result and CA_CERT.exists():
            return True
    except Exception:
        pass

    # 回退方案：用命令生成
    try:
        subprocess.run(
            [sys.executable, "-m", "mitmproxy", "--set", f"confdir={CERT_DIR}"],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass

    return CA_CERT.exists()


def get_cert_paths() -> tuple[str, str]:
    return str(CA_CERT), str(CA_KEY)


def install_cert_windows():
    """Windows 下安装证书到系统信任存储"""
    try:
        subprocess.run(
            ["certutil", "-addstore", "Root", str(CA_CERT)],
            capture_output=True, check=True,
        )
        return True
    except Exception:
        return False


def install_cert_macos():
    """macOS 下安装证书到系统钥匙串"""
    try:
        subprocess.run(
            ["security", "add-trusted-cert", "-d", "-p", "ssl", "-k",
             "/Library/Keychains/System.keychain", str(CA_CERT)],
            capture_output=True, check=True,
        )
        return True
    except Exception:
        return False


def install_cert_linux():
    """Linux 下安装证书"""
    import shutil
    dest = Path("/usr/local/share/ca-certificates/mitmproxy-ca.crt")
    try:
        shutil.copy2(str(CA_CERT), str(dest))
        subprocess.run(["update-ca-certificates"], capture_output=True)
        return True
    except Exception:
        return False


def install_certificate() -> tuple[bool, str]:
    """跨平台安装证书，返回 (成功, 提示信息)"""
    ensure_certificate()
    system = sys.platform
    if system == "win32":
        ok = install_cert_windows()
        if ok:
            return True, "证书已安装到 Windows 受信任的根证书颁发机构"
        return False, f"证书安装失败。请手动安装: {CA_CERT}\n右键 → 安装证书 → 受信任的根证书颁发机构"
    elif system == "darwin":
        ok = install_cert_macos()
        return ok, "证书已安装" if ok else f"证书安装失败，请手动安装: {CA_CERT}"
    else:
        ok = install_cert_linux()
        return ok, "证书已安装" if ok else f"证书安装失败，请手动安装: {CA_CERT}"
