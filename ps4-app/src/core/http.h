/* HTTP/1.0 mínimo: montagem de requisição e parser incremental de resposta. */
#ifndef CCPS4_HTTP_H
#define CCPS4_HTTP_H

#include <stddef.h>

#define HTTP_LINE_MAX    1024  /* linha de status/cabeçalho */
#define HTTP_HEADERS_MAX 8192  /* soma de todos os cabeçalhos */

enum { HTTP_NEED_MORE = 0, HTTP_COMPLETE = 1, HTTP_ERROR = -1 };

typedef struct {
    int phase;              /* interno */
    int status;             /* código HTTP (200, 400, ...) */
    long content_length;    /* -1 = sem Content-Length (corpo vai até o EOF) */
    char line[HTTP_LINE_MAX];
    size_t line_len;
    size_t header_bytes;
    char *body;             /* buffer do chamador */
    size_t body_cap;
    size_t body_len;
    size_t extra_bytes;     /* bytes recebidos além do Content-Length (ignorados) */
    const char *error;      /* mensagem curta em pt-BR quando HTTP_ERROR */
} http_resp_t;

/* GET: retorna o tamanho escrito em out, ou -1 se não couber. */
int http_build_get(char *out, size_t cap, const char *host_port, const char *path);

/* POST com corpo text/plain UTF-8 e Content-Length. -1 se não couber. */
int http_build_post(char *out, size_t cap, const char *host_port, const char *path,
                    const char *body, size_t body_len);

void http_resp_init(http_resp_t *r, char *body_buf, size_t body_cap);

/* Alimenta bytes recebidos (qualquer fatiamento, inclusive 1 byte por vez).
 * Retorna HTTP_NEED_MORE, HTTP_COMPLETE (corpo completo pelo Content-Length)
 * ou HTTP_ERROR (r->error explica). Depois de COMPLETE, bytes extras são
 * contados em extra_bytes e ignorados. */
int http_resp_feed(http_resp_t *r, const char *data, size_t len);

/* Chamar quando a conexão fecha (EOF). Sem Content-Length, o corpo termina
 * aqui (semântica HTTP/1.0) e o resultado é COMPLETE; com Content-Length
 * incompleto, ou cabeçalhos pela metade, é HTTP_ERROR. */
int http_resp_finish(http_resp_t *r);

#endif
