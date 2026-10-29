/* Extras da plataforma de host (Linux), usados pelos testes. */
#ifndef CCPS4_PLATFORM_HOST_H
#define CCPS4_PLATFORM_HOST_H

#include "core/render.h"

/* Teclado simulado: available=0 faz plat_ime_open() falhar ("teclado
 * indisponível"); com 1, o próximo plat_ime_poll() devolve `text`
 * (cancel=1 simula o usuário fechando o teclado). */
void host_ime_script(int available, const char *text, int cancel);

/* Grava o canvas como PPM binário (P6, RGB). 0 se ok. */
int host_write_ppm(const canvas_t *c, const char *path);

#endif
