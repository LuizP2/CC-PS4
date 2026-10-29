"""Fase 6: compilar homebrew com o OpenOrbis e instalar o .pkg no PS4.

A instalação usa o "Remote Package Installer" (flatz), que roda no console na porta 12800:
o PC serve o .pkg por HTTP e o console baixa e instala sozinho.
⚠️ API do RPI conferida em documentação de terceiros, não testada contra o console:
POST /api/install {"type":"direct","packages":[url]} → {"status":"success","task_id":N}
POST /api/get_task_progress {"task_id":N} → {"status":"success","length_total":…,"transferred_total":…,"error":0}
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse


class DeployError(Exception):
    pass


def build_homebrew(project_dir: Path, toolchain: str, timeout: int = 900) -> dict:
    if not (project_dir / "Makefile").exists():
        raise DeployError(f"{project_dir} não tem Makefile")
    if not toolchain or not Path(toolchain).is_dir():
        raise DeployError("OO_PS4_TOOLCHAIN não aponta para o toolchain OpenOrbis (rode scripts/install_openorbis.sh)")
    env = os.environ | {"OO_PS4_TOOLCHAIN": toolchain, "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1"}
    started = time.time()
    try:
        proc = subprocess.run(["make"], cwd=project_dir, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise DeployError(f"make passou de {timeout}s") from e
    pkgs = sorted(str(p) for p in project_dir.rglob("*.pkg") if p.stat().st_mtime >= started - 1)
    output = (proc.stdout + proc.stderr).splitlines()
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "pkg_files": pkgs, "output_tail": "\n".join(output[-60:])}


def local_ip_for(target_ip: str) -> str:
    """IP do PC na interface que alcança o console (nenhum pacote é enviado)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((target_ip, 9))
        return s.getsockname()[0]


def _lenient_json(raw: bytes) -> dict:
    text = raw.decode("utf-8", "replace")
    # O RPI às vezes devolve números hexadecimais sem aspas (ex.: "error_code": 0x80990088).
    return json.loads(re.sub(r":\s*(0x[0-9a-fA-F]+)", r': "\1"', text))


def _post(url: str, payload: dict, timeout: float = 15.0) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _lenient_json(resp.read())
    except (urllib.error.URLError, OSError) as e:
        raise DeployError(
            f"não falei com o Remote Package Installer em {url} ({e}). Ele está aberto no PS4?"
        ) from e


class _FileServer:
    """Servidor HTTP de um arquivo só, com Range (o downloader do PS4 pede pedaços)."""

    def __init__(self, path: Path, allowed: set[str], port: int = 0):
        self.path, self.allowed = path, allowed
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _serve(self, body: bool) -> None:
                if self.client_address[0] not in server.allowed:
                    self.send_error(403)
                    return
                if unquote(urlparse(self.path).path).lstrip("/") != server.path.name:
                    self.send_error(404)
                    return
                size = server.path.stat().st_size
                start, end = 0, size - 1
                rng = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
                if rng and (rng.group(1) or rng.group(2)):
                    if rng.group(1):
                        start = int(rng.group(1))
                        end = min(int(rng.group(2)), size - 1) if rng.group(2) else size - 1
                    else:
                        start = max(0, size - int(rng.group(2)))
                    if start > end:
                        self.send_error(416)
                        return
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                else:
                    self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                if not body:
                    return
                with server.path.open("rb") as fh:
                    fh.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = fh.read(min(1 << 20, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)

            def do_GET(self):
                self._serve(True)

            def do_HEAD(self):
                self._serve(False)

        self.httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def install_pkg(ps4_ip: str, pkg: Path, rpi_port: int = 12800, timeout: float = 1800,
                pc_ip: str | None = None, poll_s: float = 2.0) -> dict:
    if not pkg.is_file() or pkg.suffix.lower() != ".pkg":
        raise DeployError(f"{pkg} não é um arquivo .pkg")
    pc_ip = pc_ip or local_ip_for(ps4_ip)
    server = _FileServer(pkg, allowed={ps4_ip, "127.0.0.1"})
    try:
        url = f"http://{pc_ip}:{server.port}/{quote(pkg.name)}"
        api = f"http://{ps4_ip}:{rpi_port}/api"
        resp = _post(f"{api}/install", {"type": "direct", "packages": [url]})
        if resp.get("status") != "success":
            raise DeployError(f"o Remote Package Installer recusou: {resp}")
        task_id = resp.get("task_id")
        deadline = time.monotonic() + timeout
        progress: dict = {}
        while time.monotonic() < deadline:
            progress = _post(f"{api}/get_task_progress", {"task_id": task_id})
            if progress.get("status") != "success":
                raise DeployError(f"falha ao consultar o progresso: {progress}")
            if str(progress.get("error", 0)) not in ("0", "0x0"):
                raise DeployError(f"o console reportou erro na instalação: {progress}")
            total = int(progress.get("length_total") or progress.get("length") or 0)
            done = int(progress.get("transferred_total") or progress.get("transferred") or 0)
            if total and done >= total:
                break
            time.sleep(poll_s)
        else:
            raise DeployError(f"instalação não terminou em {timeout}s; último progresso: {progress}")
        return {"task_id": task_id, "title": resp.get("title"), "url": url, "bytes": total,
                "note": "download concluído; o console termina a instalação e avisa por notificação"}
    finally:
        server.stop()
