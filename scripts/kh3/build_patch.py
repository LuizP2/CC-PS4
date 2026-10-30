#!/usr/bin/env python3
"""Monta um PKG de patch falso do KH3 com um pak de mod (_P) e o pakchunklist com o nome dele.

Uso: uv run python scripts/kh3/build_patch.py <mod.pak> [--app-ver 01.01]
Entrada em kh3_work/ (fora do git): patch_base/param.sfo e icon0.png do jogo, pakchunklist.orig.
Saída: kh3_work/patch_pkg/<CONTENT_ID>-A0101-V0101.pkg (o nome segue o padrão do PkgTool).
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "kh3_work"
PKGTOOL = Path.home() / "tools/OpenOrbis/PS4Toolchain/bin/linux/PkgTool.Core"
MOD_NAME = "pakchunk99-ps4_P.pak"  # o _P dá prioridade sobre os paks do jogo

GP4 = """<?xml version="1.0"?>
<psproject xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" fmt="gp4" version="1000">
	<volume>
		<volume_type>pkg_ps4_patch</volume_type>
		<volume_id>PS4VOLUME</volume_id>
		<volume_ts>{ts}</volume_ts>
		<package content_id="{content_id}" passcode="00000000000000000000000000000000" storage_type="digital50" app_type="full" />
		<chunk_info chunk_count="1" scenario_count="1">
			<chunks>
				<chunk id="0" layer_no="0" label="Chunk #0" />
			</chunks>
			<scenarios default_id="0">
				<scenario id="0" type="sp" initial_chunk_count="1" label="Scenario #0">0</scenario>
			</scenarios>
		</chunk_info>
	</volume>
	<files img_no="0">
{files}
	</files>
	<rootdir>
		<dir targ_name="sce_sys" />
		<dir targ_name="tresgame">
			<dir targ_name="content">
				<dir targ_name="paks" />
			</dir>
		</dir>
	</rootdir>
</psproject>
"""


def pkgtool(*args: str) -> str:
    env = os.environ | {"DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1"}
    ssl = Path.home() / "tools/OpenOrbis/openssl-1.1/lib"
    if ssl.is_dir():  # o PkgTool.Core precisa da libcrypto 1.1 (scripts/install_openorbis.sh)
        env["LD_LIBRARY_PATH"] = f"{ssl}:{env.get('LD_LIBRARY_PATH', '')}"
    r = subprocess.run([str(PKGTOOL), *args], env=env, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"PkgTool {args[0]} falhou:\n{r.stdout}{r.stderr}")
    return r.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mod_pak", type=Path)
    ap.add_argument("--app-ver", default="01.01", help="precisa ser maior que o APP_VER do jogo (01.00)")
    args = ap.parse_args()

    proj = WORK / "patch"
    if proj.exists():
        shutil.rmtree(proj)
    (proj / "sce_sys").mkdir(parents=True)
    (proj / "tresgame/content/paks").mkdir(parents=True)

    sfo = proj / "sce_sys/param.sfo"
    shutil.copy(WORK / "patch_base/param.sfo", sfo)
    pkgtool("sfo_setentry", "--value", "gp", "--type", "utf8", "--maxsize", "4", sfo.as_posix(), "CATEGORY")
    pkgtool("sfo_setentry", "--value", args.app_ver, "--type", "utf8", "--maxsize", "8", sfo.as_posix(), "APP_VER")
    shutil.copy(WORK / "patch_base/icon0.png", proj / "sce_sys/icon0.png")

    chunklist = (WORK / "pakchunklist.orig").read_bytes()
    if MOD_NAME.encode() not in chunklist:
        chunklist += MOD_NAME.encode() + b"\r\n"
    (proj / "tresgame/pakchunklist").write_bytes(chunklist)
    shutil.copy(args.mod_pak, proj / "tresgame/content/paks" / MOD_NAME)

    sfo_text = pkgtool("sfo_listentries", sfo.as_posix())
    content_id = next(l.split("= ", 1)[1].strip() for l in sfo_text.splitlines() if l.startswith("CONTENT_ID"))
    assert "CATEGORY : Utf8(3/4) = gp" in sfo_text, sfo_text
    files = sorted(p.relative_to(proj).as_posix() for p in proj.rglob("*") if p.is_file())
    (WORK / "patch.gp4").write_text(GP4.format(
        ts=time.strftime("%Y-%m-%d %H:%M:%S"), content_id=content_id,
        files="\n".join(f'\t\t<file targ_path="{f}" orig_path="patch/{f}" />' for f in files)))

    out = WORK / "patch_pkg"
    out.mkdir(exist_ok=True)
    print(pkgtool("pkg_build", (WORK / "patch.gp4").as_posix(), out.as_posix()).strip())
    for p in out.glob("*.pkg"):
        print(p, p.stat().st_size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
