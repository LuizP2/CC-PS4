#include "wrap.h"

#include "utf8.h"

static int is_space(char c)
{
    return c == ' ' || c == '\t';
}

static void emit(wrap_span_t *out, int max_out, int *n, size_t a, size_t b)
{
    if (*n < max_out && out) {
        out[*n].start = (uint32_t)a;
        out[*n].end = (uint32_t)b;
    }
    (*n)++;
}

int wrap_utf8(const char *s, size_t len, int cols, wrap_span_t *out, int max_out)
{
    int n = 0;
    size_t pos = 0;

    if (cols < 1)
        cols = 1;
    if (len == 0) {
        emit(out, max_out, &n, 0, 0);
        return n;
    }
    while (pos < len) {
        size_t start, p, brk_end = 0, brk_next = 0;
        int c = 0, have_brk = 0, forced = 0;

        if (n > 0)
            while (pos < len && is_space(s[pos]))
                pos++;
        if (pos >= len)
            break;
        start = p = pos;
        while (p < len) {
            uint32_t cp;
            if (s[p] == '\n') {
                emit(out, max_out, &n, start, p);
                pos = p + 1;
                forced = 1;
                break;
            }
            if (c == cols)
                break;
            if (is_space(s[p])) {
                have_brk = 1;
                brk_end = p;
                brk_next = p + 1;
            }
            utf8_next(s, len, &p, &cp); /* inválido avança 1 byte = 1 coluna */
            c++;
        }
        if (forced)
            continue;
        if (p >= len) {
            emit(out, max_out, &n, start, len);
            pos = len;
        } else if (is_space(s[p])) {
            emit(out, max_out, &n, start, p);
            pos = p + 1;
        } else if (have_brk && brk_end > start) {
            emit(out, max_out, &n, start, brk_end);
            pos = brk_next;
        } else {
            emit(out, max_out, &n, start, p); /* palavra longa: corte forçado */
            pos = p;
        }
    }
    if (n == 0)
        emit(out, max_out, &n, len, len);
    return n;
}
