/* Uma transação HTTP/1.0 (uma requisição por conexão) sem bloquear o laço
 * de desenho: httpc_step() avança um pouco a cada quadro. */
#ifndef CCPS4_HTTPC_H
#define CCPS4_HTTPC_H

#include <stddef.h>
#include <stdint.h>

#include "http.h"

#define HTTPC_REQ_MAX  2048
#define HTTPC_BODY_MAX (64 * 1024)

enum { HTTPC_PENDING = 0, HTTPC_OK = 1, HTTPC_FAIL = -1 };

typedef struct {
    int phase;
    int fd;
    char req[HTTPC_REQ_MAX];
    size_t req_len, req_off;
    http_resp_t resp;
    char body[HTTPC_BODY_MAX + 1]; /* +1 para terminar em NUL */
    uint64_t connect_deadline, deadline;
    char error[128];
} httpc_t;

void httpc_init(httpc_t *h);
/* Começa a transação; req já montada (http_build_*). Retorna HTTPC_PENDING ou HTTPC_FAIL. */
int httpc_start(httpc_t *h, uint32_t ip, uint16_t port, const char *req, size_t len,
                uint64_t now, uint32_t connect_timeout_ms, uint32_t total_timeout_ms);
/* Avança sem bloquear. HTTPC_OK: h->resp.status e h->body/h->resp.body_len prontos. */
int httpc_step(httpc_t *h, uint64_t now);
void httpc_abort(httpc_t *h);
int httpc_busy(const httpc_t *h);

#endif
