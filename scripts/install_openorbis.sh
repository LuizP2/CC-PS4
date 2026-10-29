#!/usr/bin/env bash
# install_openorbis.sh — instala o OpenOrbis PS4 Toolchain sem sudo.
#
# O que faz:
#   1. Consulta a API do GitHub (OpenOrbis/OpenOrbis-PS4-Toolchain) e escolhe o
#      asset de Linux da release mais recente (ou da tag em OO_VERSION).
#   2. Baixa o arquivo (reaproveita um download anterior se tamanho/sha256
#      batem) e confere o sha256 quando a API publica o "digest".
#   3. Extrai em ~/tools/OpenOrbis/PS4Toolchain (troca atômica: extrai numa
#      pasta temporária e só então substitui a instalação anterior).
#   4. Confere se create-fself, create-gp4 e PkgTool.Core executam e faz um
#      teste de fumaça gerando um .pkg de verdade (sfo_new + create-gp4 + pkg_build).
#   5. Se o pkg_build falhar por falta de OpenSSL 1.1 (o PkgTool.Core é um app
#      .NET Core 3.0 e não funciona com OpenSSL 3), baixa as libs libssl.so.1.1/
#      libcrypto.so.1.1 (pacote openssl-1.1 do Arch Linux Archive, sha256 fixado)
#      para ~/tools/OpenOrbis/openssl-1.1/lib. Os Makefiles do projeto acham essa
#      pasta sozinhos e passam LD_LIBRARY_PATH só para o PkgTool.Core.
#   6. Imprime a linha "export OO_PS4_TOOLCHAIN=..." para colar no shell.
#
# É idempotente: se a mesma release já estiver instalada (marcador
# .ccps4-release dentro da pasta), não baixa nada de novo — só revalida e
# imprime o export. Sem rede, uma instalação existente continua sendo aceita.
#
# Uso:
#   scripts/install_openorbis.sh            # instala/atualiza para a última release
#   scripts/install_openorbis.sh --force    # reinstala mesmo se já estiver instalada
#   scripts/install_openorbis.sh --check    # só valida a instalação existente
#
# Variáveis opcionais:
#   OO_TOOLS_DIR      raiz das ferramentas (padrão: ~/tools)
#   OO_VERSION        tag da release (padrão: latest), ex.: v0.5.4
#   OO_DOWNLOAD_DIR   onde guardar os arquivos baixados (padrão: ~/.cache/ccps4-openorbis)
#   GITHUB_TOKEN      token opcional, só para evitar o limite de requisições da API
#
# Observação: PkgTool.Core sem ICU instalado aborta ao iniciar; por isso ele é
# sempre chamado com DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 (aqui e nos Makefiles).

set -euo pipefail

REPO="OpenOrbis/OpenOrbis-PS4-Toolchain"
TOOLS_DIR="${OO_TOOLS_DIR:-$HOME/tools}"
DEST="$TOOLS_DIR/OpenOrbis/PS4Toolchain"
VERSION="${OO_VERSION:-latest}"
DOWNLOAD_DIR="${OO_DOWNLOAD_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/ccps4-openorbis}"
MARKER="$DEST/.ccps4-release"

# Libs de compatibilidade OpenSSL 1.1 (só usadas se o sistema não tiver libssl.so.1.1).
OPENSSL11_DIR="$TOOLS_DIR/OpenOrbis/openssl-1.1/lib"
OPENSSL11_PKG="openssl-1.1-1.1.1.w-2-x86_64.pkg.tar.zst"
OPENSSL11_URL="https://archive.archlinux.org/packages/o/openssl-1.1/$OPENSSL11_PKG"
OPENSSL11_SHA256="5fb600b48d28780d27d2f1a99626c6cf1bdf325167967b9ee1f81f9a172d6b63"

FORCE=0
CHECK_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        --check) CHECK_ONLY=1 ;;
        -h|--help)
            sed -n '2,37p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "argumento desconhecido: $arg" >&2; exit 2 ;;
    esac
done

log()  { printf '[install_openorbis] %s\n' "$*" >&2; }
fail() { printf '[install_openorbis] ERRO: %s\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || fail "comando '$1' não encontrado no PATH"; }
need curl
need tar
need python3
need sha256sum

# ---------------------------------------------------------------------------
# Validação das ferramentas
# ---------------------------------------------------------------------------
validate_tools() {
    local root="$1" ok=0 out
    local bindir="$root/bin/linux"

    for f in include/orbis/libkernel.h include/orbis/VideoOut.h include/orbis/Pad.h \
             lib/crt1.o lib/libkernel.so lib/libc.a link.x \
             samples/hello_world/sce_module/libc.prx samples/hello_world/sce_module/libSceFios2.prx \
             samples/hello_world/sce_sys/about/right.sprx; do
        [[ -e "$root/$f" ]] || { log "faltando: $root/$f"; ok=1; }
    done
    for t in create-fself create-gp4 PkgTool.Core readoelf; do
        [[ -f "$bindir/$t" ]] || { log "faltando: $bindir/$t"; ok=1; continue; }
        [[ -x "$bindir/$t" ]] || chmod +x "$bindir/$t"
    done
    [[ $ok -eq 0 ]] || return 1

    # create-fself (Go) exige OO_PS4_TOOLCHAIN definido e imprime o uso com --help.
    out="$(OO_PS4_TOOLCHAIN="$root" "$bindir/create-fself" --help 2>&1 || true)"
    if grep -qiE 'usage|eboot|fself|flag' <<<"$out"; then
        log "create-fself executa: OK"
    else
        log "create-fself não respondeu como esperado:"; printf '%s\n' "$out" | head -5 >&2; ok=1
    fi

    out="$("$bindir/create-gp4" --help 2>&1 || true)"
    if grep -qiE 'usage|gp4|content-id|flag' <<<"$out"; then
        log "create-gp4 executa: OK"
    else
        log "create-gp4 não respondeu como esperado:"; printf '%s\n' "$out" | head -5 >&2; ok=1
    fi

    out="$(DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 "$bindir/PkgTool.Core" 2>&1 || true)"
    if grep -qiE 'usage|pkg_build|sfo_new|verbs|command' <<<"$out"; then
        log "PkgTool.Core executa (DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1): OK"
    else
        log "PkgTool.Core não respondeu como esperado:"; printf '%s\n' "$out" | head -8 >&2; ok=1
    fi
    return $ok
}

# Gera um .pkg mínimo (eboot.bin falso + param.sfo) para provar que o pkg_build
# funciona neste host. $2 opcional: pasta extra para LD_LIBRARY_PATH.
SMOKE_LOG=""
pkg_smoke_test() {
    local root="$1" libdir="${2:-}" tmp rc=0
    local cid="IV0000-CCPS00000_00-SMOKETEST0000000"
    local -a envv=(env DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1)
    [[ -n "$libdir" ]] && envv+=("LD_LIBRARY_PATH=$libdir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}")
    local pt="$root/bin/linux/PkgTool.Core"

    tmp="$(mktemp -d "${TMPDIR:-/tmp}/ccps4-pkgsmoke.XXXXXX")"
    mkdir -p "$tmp/sce_sys"
    printf 'ccps4 smoke test\n' > "$tmp/eboot.bin"
    # Subshell dupla: se o PkgTool abortar (SIGABRT sem libssl), o aviso
    # "Abortado (core dumped)" do bash vai para /dev/null em vez do terminal.
    ( (
        ulimit -c 0
        cd "$tmp"
        "${envv[@]}" "$pt" sfo_new sce_sys/param.sfo
        "${envv[@]}" "$pt" sfo_setentry sce_sys/param.sfo CATEGORY --type Utf8 --maxsize 4 --value gd
        "${envv[@]}" "$pt" sfo_setentry sce_sys/param.sfo CONTENT_ID --type Utf8 --maxsize 48 --value "$cid"
        "${envv[@]}" "$pt" sfo_setentry sce_sys/param.sfo TITLE_ID --type Utf8 --maxsize 12 --value CCPS00000
        "$root/bin/linux/create-gp4" -out pkg.gp4 --content-id="$cid" --files "eboot.bin sce_sys/param.sfo"
        "${envv[@]}" "$pt" pkg_build pkg.gp4 .
    ) > "$tmp/log" 2>&1 ) 2>/dev/null || rc=$?
    if [[ $rc -eq 0 && -s "$tmp/$cid.pkg" ]]; then
        SMOKE_LOG="pkg de teste com $(stat -c %s "$tmp/$cid.pkg") bytes"
        rc=0
    else
        SMOKE_LOG="$(tail -5 "$tmp/log")"
        rc=1
    fi
    rm -rf "$tmp"
    return $rc
}

ensure_openssl11() {
    if [[ -f "$OPENSSL11_DIR/libssl.so.1.1" && -f "$OPENSSL11_DIR/libcrypto.so.1.1" ]]; then
        return 0
    fi
    need zstd
    mkdir -p "$DOWNLOAD_DIR"
    local archive="$DOWNLOAD_DIR/$OPENSSL11_PKG" got
    if [[ ! -f "$archive" ]] || [[ "$(sha256sum "$archive" | cut -d' ' -f1)" != "$OPENSSL11_SHA256" ]]; then
        log "baixando libs OpenSSL 1.1 para o PkgTool.Core: $OPENSSL11_URL"
        curl -fL --retry 3 --connect-timeout 15 -o "$archive.part" "$OPENSSL11_URL"
        mv -f "$archive.part" "$archive"
    fi
    got="$(sha256sum "$archive" | cut -d' ' -f1)"
    [[ "$got" == "$OPENSSL11_SHA256" ]] || fail "sha256 do $OPENSSL11_PKG não confere ($got)"
    log "sha256 do $OPENSSL11_PKG conferido"

    # Assinatura do Arch: só dá para conferir se o chaveiro do pacman estiver legível.
    if command -v gpgv >/dev/null 2>&1 && [[ -r /etc/pacman.d/gnupg/pubring.gpg ]]; then
        if curl -fsSL --retry 2 -o "$archive.sig" "$OPENSSL11_URL.sig"; then
            if gpgv --keyring /etc/pacman.d/gnupg/pubring.gpg "$archive.sig" "$archive" >/dev/null 2>&1; then
                log "assinatura do Arch Linux conferida (gpgv + chaveiro do pacman)"
            else
                fail "assinatura inválida em $archive"
            fi
        fi
    fi

    local stage
    stage="$(mktemp -d "$TOOLS_DIR/OpenOrbis/.ossl.XXXXXX")"
    tar --zstd -xf "$archive" -C "$stage" usr/lib/libssl.so.1.1 usr/lib/libcrypto.so.1.1
    mkdir -p "$OPENSSL11_DIR"
    mv -f "$stage/usr/lib/libssl.so.1.1" "$stage/usr/lib/libcrypto.so.1.1" "$OPENSSL11_DIR/"
    rm -rf "$stage"
    log "libs OpenSSL 1.1 instaladas em $OPENSSL11_DIR"
}

# Valida tudo e, se preciso, instala as libs OpenSSL 1.1 de compatibilidade.
finalize() {
    local root="$1"
    validate_tools "$root" || return 1
    if pkg_smoke_test "$root"; then
        log "pkg_build funciona direto neste host ($SMOKE_LOG)"
        return 0
    fi
    if grep -qi 'libssl' <<<"$SMOKE_LOG"; then
        log "pkg_build falhou por falta de OpenSSL 1.1 (o PkgTool.Core é .NET Core 3.0):"
        printf '    %s\n' "$SMOKE_LOG" >&2
        ensure_openssl11
        if pkg_smoke_test "$root" "$OPENSSL11_DIR"; then
            log "pkg_build funciona com LD_LIBRARY_PATH=$OPENSSL11_DIR ($SMOKE_LOG)"
            return 0
        fi
    fi
    log "o teste de pkg_build falhou:"
    printf '    %s\n' "$SMOKE_LOG" >&2
    return 1
}

print_export() {
    log "pronto. Para usar nos Makefiles do projeto:"
    if [[ -f "$OPENSSL11_DIR/libssl.so.1.1" ]]; then
        log "(PkgTool.Core usa $OPENSSL11_DIR; os Makefiles do projeto acham essa pasta sozinhos)"
    fi
    printf 'export OO_PS4_TOOLCHAIN=%s\n' "$DEST"
}

if [[ $CHECK_ONLY -eq 1 ]]; then
    [[ -d "$DEST" ]] || fail "nenhuma instalação em $DEST"
    finalize "$DEST" || fail "instalação em $DEST com problemas"
    print_export
    exit 0
fi

# ---------------------------------------------------------------------------
# 1. Descobrir a release e o asset de Linux pela API do GitHub
# ---------------------------------------------------------------------------
if [[ "$VERSION" == "latest" ]]; then
    API_URL="https://api.github.com/repos/$REPO/releases/latest"
else
    API_URL="https://api.github.com/repos/$REPO/releases/tags/$VERSION"
fi

curl_api=(curl -fsSL --retry 3 --connect-timeout 15 -H "Accept: application/vnd.github+json")
[[ -n "${GITHUB_TOKEN:-}" ]] && curl_api+=(-H "Authorization: Bearer $GITHUB_TOKEN")

log "consultando $API_URL"
if ! RELEASE_JSON="$("${curl_api[@]}" "$API_URL")"; then
    if [[ -f "$MARKER" ]] && finalize "$DEST"; then
        log "sem acesso à API do GitHub; mantendo a instalação existente ($(head -1 "$MARKER"))"
        print_export
        exit 0
    fi
    fail "não consegui consultar a API do GitHub e não há instalação válida em $DEST"
fi

# Escolha do asset (em ordem de preferência):
#   1. toolchain*.tar.gz / .tgz   (v0.5.4+: tarball único com bin/linux, bin/macos, bin/windows)
#   2. qualquer asset com "linux" no nome (.tar.gz / .tgz / .zip)
#   3. toolchain*.zip             (v0.5.3: zips por versão do LLVM; prefere o maior número)
#   4. v*.tar.gz                  (v0.5.2 e anteriores: tarball para Linux)
# Saída: tag, nome, url, tamanho, sha256 (vazio se a API não publicar), separados por TAB.
ASSET_INFO="$(python3 -I -c '
import json, re, sys
rel = json.load(sys.stdin)
assets = rel.get("assets") or []
def llvm_ver(a):
    m = re.search(r"llvm-(\d+)", a["name"])
    return int(m.group(1)) if m else 0
def pick():
    for rx in (r"^toolchain.*\.(tar\.gz|tgz)$",
               r"linux.*\.(tar\.gz|tgz|zip)$",
               r"^toolchain.*\.zip$",
               r"^v[0-9.]+\.tar\.gz$"):
        c = [a for a in assets if re.search(rx, a["name"], re.I)]
        if c:
            return sorted(c, key=llvm_ver)[-1]
    return None
a = pick()
if a is None:
    sys.exit("nenhum asset de Linux encontrado em " + rel.get("tag_name", "?") +
             ": " + ", ".join(x["name"] for x in assets))
digest = a.get("digest") or ""
sha = digest.split(":", 1)[1] if digest.startswith("sha256:") else ""
print("\t".join([rel["tag_name"], a["name"], a["browser_download_url"], str(a["size"]), sha]))
' <<<"$RELEASE_JSON")" || fail "falha ao escolher o asset da release"

IFS=$'\t' read -r TAG ASSET_NAME ASSET_URL ASSET_SIZE ASSET_SHA <<<"$ASSET_INFO"
log "release: $TAG  asset: $ASSET_NAME ($ASSET_SIZE bytes)"

# ---------------------------------------------------------------------------
# 2. Já instalado? (idempotência)
# ---------------------------------------------------------------------------
if [[ $FORCE -eq 0 && -f "$MARKER" ]] && [[ "$(head -1 "$MARKER")" == "$TAG $ASSET_NAME" ]]; then
    log "$TAG ($ASSET_NAME) já está instalado em $DEST"
    finalize "$DEST" || fail "a instalação existente falhou na validação; rode com --force"
    print_export
    exit 0
fi

# ---------------------------------------------------------------------------
# 3. Download (reaproveita arquivo já baixado se tamanho e sha256 conferem)
# ---------------------------------------------------------------------------
mkdir -p "$DOWNLOAD_DIR"
ARCHIVE="$DOWNLOAD_DIR/$TAG-$ASSET_NAME"

archive_ok() {
    [[ -f "$ARCHIVE" ]] || return 1
    [[ "$(stat -c %s "$ARCHIVE")" == "$ASSET_SIZE" ]] || return 1
    if [[ -n "$ASSET_SHA" ]]; then
        [[ "$(sha256sum "$ARCHIVE" | cut -d' ' -f1)" == "$ASSET_SHA" ]] || return 1
    fi
    return 0
}

if archive_ok; then
    log "reaproveitando download existente: $ARCHIVE"
else
    log "baixando $ASSET_URL"
    curl -fL --retry 3 --connect-timeout 15 -o "$ARCHIVE.part" "$ASSET_URL"
    mv -f "$ARCHIVE.part" "$ARCHIVE"
    archive_ok || fail "download corrompido (tamanho ou sha256 não conferem): $ARCHIVE"
fi
if [[ -n "$ASSET_SHA" ]]; then
    log "sha256 conferido: $ASSET_SHA"
else
    log "a API não publicou sha256 para este asset; conferido só o tamanho"
fi

# ---------------------------------------------------------------------------
# 4. Extração numa pasta temporária + troca atômica
# ---------------------------------------------------------------------------
mkdir -p "$TOOLS_DIR/OpenOrbis"
STAGE="$(mktemp -d "$TOOLS_DIR/OpenOrbis/.stage.XXXXXX")"
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

log "extraindo em $STAGE"
case "$ASSET_NAME" in
    *.zip)
        need unzip
        unzip -q "$ARCHIVE" -d "$STAGE" ;;
    *)
        tar -xzf "$ARCHIVE" -C "$STAGE" --no-same-owner ;;
esac

# A raiz do toolchain é a pasta que contém include/orbis e bin/linux.
ROOT_IN_ARCHIVE="$(find "$STAGE" -maxdepth 4 -type d -path '*/include/orbis' -print -quit)"
[[ -n "$ROOT_IN_ARCHIVE" ]] || fail "não achei include/orbis dentro do arquivo"
ROOT_IN_ARCHIVE="$(dirname "$(dirname "$ROOT_IN_ARCHIVE")")"
[[ -d "$ROOT_IN_ARCHIVE/bin/linux" ]] || fail "o arquivo não contém bin/linux (asset errado?)"

chmod +x "$ROOT_IN_ARCHIVE"/bin/linux/* 2>/dev/null || true
printf '%s %s\n%s\n' "$TAG" "$ASSET_NAME" "$ASSET_URL" > "$ROOT_IN_ARCHIVE/.ccps4-release"

finalize "$ROOT_IN_ARCHIVE" || fail "o toolchain extraído não passou na validação"

if [[ -e "$DEST" ]]; then
    rm -rf "$DEST.old"
    mv "$DEST" "$DEST.old"
fi
mv "$ROOT_IN_ARCHIVE" "$DEST"
rm -rf "$DEST.old"
log "instalado $TAG em $DEST"

print_export
