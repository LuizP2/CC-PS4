/*
 * Camada de plataforma do PS4 (OpenOrbis): VideoOut (framebuffer + flip),
 * controle (scePad), teclado do sistema (sceImeDialog), relógio, log e o
 * laço principal. A rede é net_bsd.c (sockets BSD da libkernel), o mesmo
 * código testado no host.
 *
 * Nada aqui foi executado num PS4 de verdade ainda: os nomes e as assinaturas
 * vêm dos headers do OpenOrbis v0.5.4 e dos samples oficiais (graphics, input,
 * keyboard, dialogs). Ver README.md, seção "O que foi validado".
 */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <orbis/CommonDialog.h>
#include <orbis/ImeDialog.h>
#include <orbis/Pad.h>
#include <orbis/Sysmodule.h>
#include <orbis/UserService.h>
#include <orbis/VideoOut.h>
#include <orbis/libkernel.h>

#include "core/bridge.h"
#include "core/render.h"
#include "core/utf8.h"
#include "core/viewer.h"
#include "platform.h"

#define FB_W 1920
#define FB_H 1080
#define FB_COUNT 2
#define FB_ALIGN 0x200000u
#define PIXEL_FMT_A8R8G8B8_SRGB 0x80000000u /* o mesmo do sample graphics */
#define MEM_TYPE_WC_GARLIC 3
#define PROT_CPU_GPU_RW 0x33

/* ---------------------------------------------------------------- tempo/log */

uint64_t plat_now_ms(void)
{
    return sceKernelGetProcessTime() / 1000u; /* µs desde o início do processo */
}

void plat_log(const char *fmt, ...)
{
    char buf[512];
    va_list ap;
    size_t n;

    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf) - 2, fmt, ap);
    va_end(ap);
    n = strlen(buf);
    buf[n] = '\n';
    buf[n + 1] = '\0';
    sceKernelDebugOutText(0, buf);
    fputs(buf, stdout);
}

long plat_read_file(const char *path, char *buf, size_t cap)
{
    FILE *f = fopen(path, "rb");
    size_t n;
    if (!f || cap == 0)
        return -1;
    n = fread(buf, 1, cap - 1, f);
    fclose(f);
    buf[n] = '\0';
    return (long)n;
}

/* ---------------------------------------------------------------- vídeo */

static int g_video = -1;
static OrbisKernelEqueue g_flip_queue;
static uint32_t *g_fb[FB_COUNT];
static int g_fb_cur;
static int64_t g_flip_arg;

static int video_init(void)
{
    OrbisVideoOutBufferAttribute attr;
    size_t fb_size = ((size_t)FB_W * FB_H * 4 + FB_ALIGN - 1) / FB_ALIGN * FB_ALIGN;
    size_t total = fb_size * FB_COUNT;
    off_t phys = 0;
    void *mem = NULL;

    g_video = sceVideoOutOpen(ORBIS_VIDEO_USER_MAIN, ORBIS_VIDEO_OUT_BUS_MAIN, 0, 0);
    if (g_video < 0)
        return -1;
    if (sceKernelCreateEqueue(&g_flip_queue, "ccps4 viewer flip") < 0)
        return -2;
    sceVideoOutAddFlipEvent(g_flip_queue, g_video, 0);
    if (sceKernelAllocateDirectMemory(0, sceKernelGetDirectMemorySize(), total, FB_ALIGN, MEM_TYPE_WC_GARLIC,
                                      &phys) < 0)
        return -3;
    if (sceKernelMapDirectMemory(&mem, total, PROT_CPU_GPU_RW, 0, phys, FB_ALIGN) < 0) {
        sceKernelReleaseDirectMemory(phys, total);
        return -4;
    }
    for (int i = 0; i < FB_COUNT; i++)
        g_fb[i] = (uint32_t *)((uint8_t *)mem + fb_size * (size_t)i);
    memset(&attr, 0, sizeof(attr));
    sceVideoOutSetBufferAttribute(&attr, PIXEL_FMT_A8R8G8B8_SRGB, ORBIS_VIDEO_OUT_TILING_MODE_LINEAR,
                                  ORBIS_VIDEO_OUT_ASPECT_RATIO_16_9, FB_W, FB_H, FB_W);
    if (sceVideoOutRegisterBuffers(g_video, 0, (void *const *)g_fb, FB_COUNT, &attr) != 0)
        return -5;
    sceVideoOutSetFlipRate(g_video, ORBIS_VIDEO_OUT_FLIP_60HZ);
    return 0;
}

/* Copia o quadro (RAM) para o buffer de vídeo livre, faz o flip e espera. */
static void video_present(const canvas_t *c)
{
    OrbisKernelEvent ev;
    int count = 0;
    int64_t arg = ++g_flip_arg;

    memcpy(g_fb[g_fb_cur], c->px, (size_t)FB_W * FB_H * 4);
    sceVideoOutSubmitFlip(g_video, g_fb_cur, ORBIS_VIDEO_OUT_FLIP_VSYNC, arg);
    for (int guard = 0; guard < 8; guard++) {
        OrbisVideoOutFlipStatus st;
        OrbisKernelUseconds timeout = 50000;
        memset(&st, 0, sizeof(st));
        sceVideoOutGetFlipStatus(g_video, &st);
        if (st.flipArg >= arg)
            break;
        if (sceKernelWaitEqueue(g_flip_queue, &ev, 1, &count, &timeout) != 0)
            break;
    }
    g_fb_cur = (g_fb_cur + 1) % FB_COUNT;
}

/* ---------------------------------------------------------------- controle */

static int g_user_id = -1;
static int g_pad = -1;
static uint64_t g_pad_retry_at;

/* Buffer maior que OrbisPadData: se a struct real do firmware for maior que a
 * do header, scePadReadState não escreve fora do nosso buffer. */
static union {
    OrbisPadData data;
    uint8_t raw[512];
} g_pad_buf;

static void pad_try_open(uint64_t now)
{
    if (g_pad >= 0 || now < g_pad_retry_at)
        return;
    g_pad_retry_at = now + 2000;
    if (g_user_id < 0 && sceUserServiceGetInitialUser(&g_user_id) < 0) {
        g_user_id = -1;
        return;
    }
    g_pad = scePadOpen(g_user_id, 0, 0, NULL);
    if (g_pad < 0) {
        plat_log("[viewer] scePadOpen falhou: 0x%08x", (unsigned)g_pad);
        g_pad = -1;
    }
}

static uint32_t pad_read(void)
{
    uint32_t b, out = 0;

    if (g_pad < 0)
        return 0;
    memset(&g_pad_buf, 0, sizeof(g_pad_buf));
    if (scePadReadState(g_pad, &g_pad_buf.data) < 0 || !g_pad_buf.data.connected)
        return 0;
    b = g_pad_buf.data.buttons;
    if (b & ORBIS_PAD_BUTTON_UP) out |= VB_UP;
    if (b & ORBIS_PAD_BUTTON_DOWN) out |= VB_DOWN;
    if (b & ORBIS_PAD_BUTTON_LEFT) out |= VB_LEFT;
    if (b & ORBIS_PAD_BUTTON_RIGHT) out |= VB_RIGHT;
    if (b & ORBIS_PAD_BUTTON_CROSS) out |= VB_CROSS;
    if (b & ORBIS_PAD_BUTTON_CIRCLE) out |= VB_CIRCLE;
    if (b & ORBIS_PAD_BUTTON_TRIANGLE) out |= VB_TRIANGLE;
    if (b & ORBIS_PAD_BUTTON_SQUARE) out |= VB_SQUARE;
    if (b & ORBIS_PAD_BUTTON_L1) out |= VB_L1;
    if (b & ORBIS_PAD_BUTTON_R1) out |= VB_R1;
    if (b & ORBIS_PAD_BUTTON_OPTIONS) out |= VB_OPTIONS;
    return out;
}

/* ---------------------------------------------------------------- teclado (IME) */

/* No OpenOrbis wchar_t tem 16 bits (UTF-16), como espera o sistema. */
static uint16_t g_ime_title[96];
static uint16_t g_ime_text[VIEWER_IME_CHARS + 1];
static int g_ime_module = 0; /* 0 não tentado, 1 ok, -1 indisponível */
static int g_ime_open = 0;

int plat_ime_open(const char *title_utf8, const char *initial_utf8, int max_chars)
{
    OrbisImeDialogSetting p;
    int r;

    if (g_ime_module == 0) {
        r = sceSysmoduleLoadModule(ORBIS_SYSMODULE_IME_DIALOG);
        g_ime_module = r < 0 ? -1 : 1;
        plat_log("[viewer] carregar libSceImeDialog: 0x%08x", (unsigned)r);
    }
    if (g_ime_module < 0 || g_ime_open)
        return -1;
    if (max_chars > VIEWER_IME_CHARS)
        max_chars = VIEWER_IME_CHARS;

    utf8_to_utf16(title_utf8, strlen(title_utf8), g_ime_title, sizeof(g_ime_title) / 2);
    memset(g_ime_text, 0, sizeof(g_ime_text));
    if (initial_utf8)
        utf8_to_utf16(initial_utf8, strlen(initial_utf8), g_ime_text, (size_t)max_chars + 1);

    memset(&p, 0, sizeof(p));
    p.userId = (uint32_t)(g_user_id >= 0 ? g_user_id : 0xFE);
    p.type = ORBIS_TYPE_DEFAULT;
    p.supportedLanguages = 0;
    p.enterLabel = ORBIS_BUTTON_LABEL_SEND;
    p.inputMethod = ORBIS__DEFAULT;
    p.filter = NULL;
    p.option = 0;
    p.maxTextLength = (uint32_t)max_chars;
    p.inputTextBuffer = (wchar_t *)g_ime_text;
    p.posx = FB_W / 2.0f;
    p.posy = FB_H / 2.0f;
    p.horizontalAlignment = ORBIS_H_CENTER;
    p.verticalAlignment = ORBIS_V_CENTER;
    p.placeholder = NULL;
    p.title = (const wchar_t *)g_ime_title;

    r = sceImeDialogInit(&p, NULL);
    if (r < 0) {
        plat_log("[viewer] sceImeDialogInit falhou: 0x%08x", (unsigned)r);
        return -1;
    }
    g_ime_open = 1;
    return 0;
}

int plat_ime_poll(char *out, size_t cap)
{
    OrbisDialogStatus st;
    OrbisDialogResult res;

    if (!g_ime_open)
        return IME_FAILED;
    st = sceImeDialogGetStatus();
    if (st == ORBIS_DIALOG_STATUS_RUNNING)
        return IME_RUNNING;
    g_ime_open = 0;
    if (st != ORBIS_DIALOG_STATUS_STOPPED) { /* sumiu sem terminar */
        sceImeDialogTerm();
        return IME_FAILED;
    }
    memset(&res, 0, sizeof(res));
    sceImeDialogGetResult(&res);
    sceImeDialogTerm();
    if (res.endstatus != ORBIS_DIALOG_OK)
        return IME_CANCELED;
    g_ime_text[VIEWER_IME_CHARS] = 0;
    utf16_to_utf8(g_ime_text, VIEWER_IME_CHARS + 1, out, cap);
    return IME_DONE;
}

/* ---------------------------------------------------------------- configuração */

static void load_config(viewer_config_t *cfg)
{
    static char text[4096];
    long n;
    int bad = 0, applied;

    bridge_config_defaults(cfg);
    sceKernelMkdir("/data/ccps4", 0777); /* facilita colocar o viewer.cfg por FTP */
    n = plat_read_file(BRIDGE_CONFIG_PATH, text, sizeof(text));
    if (n < 0) {
        plat_log("[viewer] %s não existe; usando descoberta UDP", BRIDGE_CONFIG_PATH);
        FILE *f = fopen("/data/ccps4/viewer.cfg.exemplo", "wb");
        if (f) {
            fputs("# Copie para viewer.cfg e descomente para fixar o PC (desliga a descoberta UDP)\n"
                  "#host=192.168.0.10\n#port=8765\n# fuso para os horários do feed, em minutos (Brasília = -180)\n"
                  "#utc_offset=-180\n# tamanho do texto: 3 (padrão) ou 2 (cabe mais coisa)\n#font_scale=3\n",
                  f);
            fclose(f);
        }
        return;
    }
    applied = bridge_parse_config(cfg, text, (size_t)n, &bad);
    plat_log("[viewer] %s: %d chave(s) aplicada(s), %d linha(s) inválida(s), host=%s", BRIDGE_CONFIG_PATH,
             applied, bad, cfg->host[0] ? cfg->host : "(descoberta)");
}

/* ---------------------------------------------------------------- main */

static viewer_t g_viewer;

int main(void)
{
    OrbisUserServiceInitializeParams up;
    viewer_config_t cfg;
    canvas_t canvas;
    uint64_t last_present = 0;
    int video_ok;

    setvbuf(stdout, NULL, _IONBF, 0);
    plat_log("[viewer] CCPS4 Agent Viewer iniciando");
    net_init();

    memset(&up, 0, sizeof(up));
    up.priority = ORBIS_KERNEL_PRIO_FIFO_LOWEST;
    sceUserServiceInitialize(&up);
    if (scePadInit() != 0)
        plat_log("[viewer] scePadInit falhou");
    sceCommonDialogInitialize(); /* ignorado se já inicializado */

    video_ok = video_init() == 0;
    if (!video_ok)
        plat_log("[viewer] video_init falhou; seguindo sem tela (só log)");

    canvas.w = FB_W;
    canvas.h = FB_H;
    canvas.stride = FB_W;
    canvas.px = malloc((size_t)FB_W * FB_H * 4);
    if (!canvas.px) {
        plat_log("[viewer] sem memória para o quadro");
        for (;;)
            sceKernelUsleep(1000000);
    }

    load_config(&cfg);
    viewer_init(&g_viewer, &cfg, plat_now_ms());

    for (;;) {
        uint64_t now = plat_now_ms();
        pad_try_open(now);
        viewer_input(&g_viewer, g_viewer.ime_active ? 0 : pad_read(), now);
        viewer_tick(&g_viewer, now);

        /* Redesenha só quando algo mudou (no mínimo 1x/s, por causa dos contadores). */
        if (video_ok && (g_viewer.dirty || now - last_present >= 1000)) {
            ui_render(&canvas, &g_viewer, now);
            g_viewer.dirty = 0;
            video_present(&canvas); /* espera o vsync (~16 ms) */
            last_present = now;
        } else {
            sceKernelUsleep(16000);
        }
    }
    return 0;
}
