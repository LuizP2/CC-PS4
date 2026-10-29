/*
 * CCPS4 Crash Test — homebrew alvo da fase 3 do projeto CC-PS4.
 *
 * Roteiro (sempre o mesmo, para o teste ser reproduzível):
 *   1. Loga um banner com os endereços em tempo de execução dos símbolos que o
 *      agente vai usar (breakpoint, watchpoint, busca de texto).
 *   2. Durante 90 s, a cada 1 s: incrementa g_watch_counter (alvo de watchpoint
 *      de hardware), recopia a legenda para buffers no heap e loga "tick N/90".
 *   3. Chama crash_here(NULL), que lê p->value e gera SIGSEGV (ponteiro nulo).
 *
 * Os logs vão para o klog via sceKernelDebugOutText(0, ...) e também para o
 * stdout (printf). Os offsets relativos à base da imagem (para o Ghidra e para
 * "eboot.bin+0x<offset>") são gerados no build em build/offsets.txt.
 */
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <uchar.h>

#include <orbis/libkernel.h>

#include "video.h"

#define TAG          "[CCPS4-CRASHTEST] "
#define TOTAL_TICKS  90
#define TICK_USEC    1000000u

/* Estrutura usada no crash: value fica no offset 8, então o acesso com p == NULL
 * tenta ler o endereço 0x8. */
struct thing {
    uint64_t magic;
    uint32_t value;
};

/* Alvo do watchpoint de hardware (escrita de 4 bytes, uma vez por tick). */
__attribute__((used)) volatile uint32_t g_watch_counter = 0;

/* A mesma legenda em UTF-8 e em UTF-16LE (char16_t). Ficam em .rodata (somente
 * leitura); as cópias no heap ficam em memória gravável. */
__attribute__((used)) const char g_subtitle_utf8[] =
    "CCPS4 legenda de teste: Olá, ação, coração!";
__attribute__((used)) const char16_t g_subtitle_utf16[] =
    u"CCPS4 legenda de teste: Olá, ação, coração!";

/* Cópias no heap (recopiadas a cada tick). Ponteiros globais para o agente
 * conseguir achá-las também lendo memória a partir do símbolo. */
__attribute__((used)) char *volatile g_heap_copy = NULL;
__attribute__((used)) char16_t *volatile g_heap_copy16 = NULL;

/* Definido pelo link.x do OpenOrbis: primeiro byte de .text, que fica no
 * endereço de link 0. Em tempo de execução, &__text_start == base do eboot.bin. */
extern char __text_start[];

static void log_line(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

static void log_line(const char *fmt, ...)
{
    char buf[768];
    size_t n = (size_t)snprintf(buf, sizeof(buf), "%s", TAG);
    va_list ap;

    va_start(ap, fmt);
    vsnprintf(buf + n, sizeof(buf) - n - 1, fmt, ap); /* reserva 1 byte para '\n' */
    va_end(ap);

    n = strlen(buf);
    if (n == 0 || buf[n - 1] != '\n') {
        buf[n] = '\n';
        buf[n + 1] = '\0';
    }
    sceKernelDebugOutText(0, buf); /* klog */
    fputs(buf, stdout);            /* stdout (sem buffer, ver setvbuf em main) */
}

static void notify(const char *text)
{
    OrbisNotificationRequest req;

    memset(&req, 0, sizeof(req));
    req.type = NotificationRequest;
    req.targetId = -1;
    req.useIconImageUri = 1;
    snprintf(req.message, sizeof(req.message), "%s", text);
    snprintf(req.iconUri, sizeof(req.iconUri), "%s", "cxml://psnotification/tex_icon_system");
    sceKernelSendNotificationRequest(0, &req, sizeof(req), 0);
}

static unsigned long off_of(const void *p)
{
    return (unsigned long)((uintptr_t)p - (uintptr_t)__text_start);
}

/* O crash proposital. noinline + leitura volatile: o compilador não pode remover
 * nem antecipar o acesso; com -O0 a função fica com prólogo normal (rbp). */
__attribute__((noinline, used)) int crash_here(struct thing *p)
{
    volatile uint32_t *field = &p->value; /* p == NULL -> endereço 0x8 */
    return (int)*field;                   /* SIGSEGV aqui */
}

static void refresh_heap_copies(void)
{
    memcpy(g_heap_copy, g_subtitle_utf8, sizeof(g_subtitle_utf8));
    memcpy(g_heap_copy16, g_subtitle_utf16, sizeof(g_subtitle_utf16));
}

int main(void)
{
    /* Ponteiro nulo guardado num volatile: o valor só é conhecido em tempo de
     * execução, então o compilador não transforma a chamada em trap/ud2. */
    struct thing *volatile victim = NULL;
    int video_ok;

    setvbuf(stdout, NULL, _IONBF, 0);

    g_heap_copy = (char *)malloc(sizeof(g_subtitle_utf8));
    g_heap_copy16 = (char16_t *)malloc(sizeof(g_subtitle_utf16));
    if (g_heap_copy == NULL || g_heap_copy16 == NULL) {
        log_line("malloc falhou; abortando o teste");
        return 1;
    }
    refresh_heap_copies();

    log_line("crash_here=%p g_watch_counter=%p g_subtitle_utf8=%p g_subtitle_utf16=%p heap_copy=%p",
             (void *)crash_here, (void *)&g_watch_counter, (void *)g_subtitle_utf8,
             (void *)g_subtitle_utf16, (void *)g_heap_copy);
    log_line("image_base=%p (eboot.bin, __text_start) heap_copy16=%p main=%p",
             (void *)__text_start, (void *)g_heap_copy16, (void *)main);
    log_line("offsets: crash_here=0x%lx g_watch_counter=0x%lx g_subtitle_utf8=0x%lx "
             "g_subtitle_utf16=0x%lx main=0x%lx",
             off_of((const void *)crash_here), off_of((const void *)&g_watch_counter),
             off_of(g_subtitle_utf8), off_of(g_subtitle_utf16), off_of((const void *)main));
    log_line("legenda utf8 (%u bytes): %s", (unsigned)(sizeof(g_subtitle_utf8) - 1), g_subtitle_utf8);

    video_ok = (video_init() == 0);
    log_line("video: %s", video_ok ? "ok (barra de progresso na tela)" : "indisponivel (seguindo sem video)");
    notify("CCPS4 Crash Test: crash proposital em 90 s");

    uint64_t next_tick = sceKernelGetProcessTime(); /* microssegundos desde o início do processo */
    for (int tick = 1; tick <= TOTAL_TICKS; tick++) {
        g_watch_counter = g_watch_counter + 1; /* escrita de 4 bytes: dispara o watchpoint */
        refresh_heap_copies();
        log_line("tick %d/%d counter=%u", tick, TOTAL_TICKS, (unsigned)g_watch_counter);
        if (video_ok)
            video_draw_progress(tick, TOTAL_TICKS);

        /* Agenda fixa de 1 s (desconta o tempo gasto desenhando/logando). Se o
         * processo ficou pausado no debugger, não tenta "compensar" os ticks. */
        next_tick += TICK_USEC;
        uint64_t now = sceKernelGetProcessTime();
        if (now < next_tick)
            sceKernelUsleep((uint32_t)(next_tick - now));
        else
            next_tick = now;
    }

    log_line("chamando crash_here(NULL) agora (leitura de p->value, offset 8)");
    if (video_ok)
        video_draw_progress(-1, TOTAL_TICKS);

    int v = crash_here(victim);

    /* Só chega aqui se o acesso não falhar (não deveria acontecer). */
    log_line("inesperado: crash_here retornou %d", v);
    for (;;)
        sceKernelUsleep(TICK_USEC);
    return 0;
}
