/*
 * Vídeo mínimo (mesmo caminho do sample "graphics" do OpenOrbis):
 * memória direta WC (garlic) mapeada CPU+GPU, 2 buffers lineares A8R8G8B8,
 * flip com VSYNC e espera pelo evento de flip.
 */
#include <stdint.h>
#include <string.h>

#include <orbis/libkernel.h>
#include <orbis/VideoOut.h>

#include "video.h"

#define FB_W        1920
#define FB_H        1080
#define FB_COUNT    2
#define FB_ALIGN    0x200000u            /* 2 MiB */
#define PIXEL_FMT_A8R8G8B8_SRGB 0x80000000u /* formato usado pelo sample graphics */
#define MEM_TYPE_WC_GARLIC      3
#define PROT_CPU_GPU_RW         0x33

static int g_video = -1;
static OrbisKernelEqueue g_flip_queue;
static uint32_t *g_fb[FB_COUNT];
static int g_cur;
static int64_t g_flip_arg;

static size_t align_up(size_t v, size_t a)
{
    return (v + a - 1) / a * a;
}

int video_init(void)
{
    OrbisVideoOutBufferAttribute attr;
    size_t fb_size = align_up((size_t)FB_W * FB_H * 4, FB_ALIGN);
    size_t total = fb_size * FB_COUNT;
    off_t phys = 0;
    void *mem = NULL;

    g_video = sceVideoOutOpen(ORBIS_VIDEO_USER_MAIN, ORBIS_VIDEO_OUT_BUS_MAIN, 0, 0);
    if (g_video < 0)
        return -1;
    if (sceKernelCreateEqueue(&g_flip_queue, "ccps4 crashtest flip") < 0)
        return -2;
    sceVideoOutAddFlipEvent(g_flip_queue, g_video, 0);

    if (sceKernelAllocateDirectMemory(0, sceKernelGetDirectMemorySize(), total, FB_ALIGN,
                                      MEM_TYPE_WC_GARLIC, &phys) < 0)
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
    g_cur = 0;
    return 0;
}

static void fill_rect(uint32_t *fb, int x, int y, int w, int h, uint32_t argb)
{
    for (int yy = y; yy < y + h && yy < FB_H; yy++) {
        if (yy < 0)
            continue;
        uint32_t *row = fb + (size_t)yy * FB_W;
        for (int xx = x; xx < x + w && xx < FB_W; xx++)
            if (xx >= 0)
                row[xx] = argb;
    }
}

static void flip_and_wait(void)
{
    OrbisKernelEvent ev;
    int count = 0;
    int64_t arg = ++g_flip_arg;

    sceVideoOutSubmitFlip(g_video, g_cur, ORBIS_VIDEO_OUT_FLIP_VSYNC, arg);
    for (int guard = 0; guard < 8; guard++) {
        OrbisVideoOutFlipStatus st;
        OrbisKernelUseconds timeout = 100000; /* 100 ms: nunca trava o tick */
        memset(&st, 0, sizeof(st));
        sceVideoOutGetFlipStatus(g_video, &st);
        if (st.flipArg >= arg)
            break;
        if (sceKernelWaitEqueue(g_flip_queue, &ev, 1, &count, &timeout) != 0)
            break;
    }
    g_cur = (g_cur + 1) % FB_COUNT;
}

void video_draw_progress(int tick, int total)
{
    if (g_video < 0)
        return;
    uint32_t *fb = g_fb[g_cur];
    const int margin = 160, bar_y = 480, bar_h = 120;
    const int bar_w = FB_W - 2 * margin;

    if (tick < 0) {
        fill_rect(fb, 0, 0, FB_W, FB_H, 0xFF8B0000u); /* vermelho escuro: crash agora */
        fill_rect(fb, margin, bar_y, bar_w, bar_h, 0xFFFF3B30u);
        flip_and_wait();
        return;
    }

    fill_rect(fb, 0, 0, FB_W, FB_H, 0xFF101820u);           /* fundo */
    fill_rect(fb, margin - 8, bar_y - 8, bar_w + 16, bar_h + 16, 0xFF3A4A5Au);
    fill_rect(fb, margin, bar_y, bar_w, bar_h, 0xFF1E2A36u);
    for (int i = 0; i < total; i++) {
        int x0 = margin + (bar_w * i) / total;
        int x1 = margin + (bar_w * (i + 1)) / total;
        uint32_t c = (i < tick) ? ((total - i <= 10) ? 0xFFFFB300u : 0xFF2ECC71u) : 0xFF26323Eu;
        fill_rect(fb, x0 + 2, bar_y + 6, x1 - x0 - 4, bar_h - 12, c);
    }
    flip_and_wait();
}
