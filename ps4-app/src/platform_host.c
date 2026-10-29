/*
 * Plataforma de host (Linux): relógio, log, leitura de arquivo, teclado
 * simulado e gravação de quadros em PPM. A rede é a mesma do PS4 (net_bsd.c).
 */
#define _POSIX_C_SOURCE 200809L
#include "platform_host.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "platform.h"

uint64_t plat_now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

void plat_log(const char *fmt, ...)
{
    va_list ap;
    if (getenv("CCPS4_QUIET"))
        return;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
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

/* ---------------------------------------------------------------- IME simulado */

static int g_ime_available;
static int g_ime_cancel;
static char g_ime_text[1024];
static int g_ime_open;

void host_ime_script(int available, const char *text, int cancel)
{
    g_ime_available = available;
    g_ime_cancel = cancel;
    snprintf(g_ime_text, sizeof(g_ime_text), "%s", text ? text : "");
}

int plat_ime_open(const char *title_utf8, const char *initial_utf8, int max_chars)
{
    (void)title_utf8;
    (void)initial_utf8;
    (void)max_chars;
    if (!g_ime_available)
        return -1;
    g_ime_open = 1;
    return 0;
}

int plat_ime_poll(char *out, size_t cap)
{
    if (!g_ime_open)
        return IME_FAILED;
    g_ime_open = 0;
    if (g_ime_cancel)
        return IME_CANCELED;
    snprintf(out, cap, "%s", g_ime_text);
    return IME_DONE;
}

/* ---------------------------------------------------------------- quadros */

int host_write_ppm(const canvas_t *c, const char *path)
{
    FILE *f = fopen(path, "wb");
    unsigned char *row;
    if (!f)
        return -1;
    row = malloc((size_t)c->w * 3);
    if (!row) {
        fclose(f);
        return -1;
    }
    fprintf(f, "P6\n%d %d\n255\n", c->w, c->h);
    for (int y = 0; y < c->h; y++) {
        const uint32_t *p = c->px + (size_t)y * (size_t)c->stride;
        for (int x = 0; x < c->w; x++) {
            row[x * 3 + 0] = (unsigned char)(p[x] >> 16);
            row[x * 3 + 1] = (unsigned char)(p[x] >> 8);
            row[x * 3 + 2] = (unsigned char)p[x];
        }
        fwrite(row, 1, (size_t)c->w * 3, f);
    }
    free(row);
    return fclose(f) == 0 ? 0 : -1;
}
