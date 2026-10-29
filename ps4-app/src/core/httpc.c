#include "httpc.h"

#include <stdio.h>
#include <string.h>

#include "../platform.h"

enum { PH_IDLE, PH_CONNECTING, PH_SENDING, PH_RECEIVING, PH_DONE, PH_FAILED };

void httpc_init(httpc_t *h)
{
    h->phase = PH_IDLE;
    h->fd = -1;
    h->error[0] = '\0';
}

int httpc_busy(const httpc_t *h)
{
    return h->phase == PH_CONNECTING || h->phase == PH_SENDING || h->phase == PH_RECEIVING;
}

void httpc_abort(httpc_t *h)
{
    if (h->fd >= 0)
        net_close(h->fd);
    h->fd = -1;
    h->phase = PH_IDLE;
}

static int failf(httpc_t *h, const char *msg)
{
    snprintf(h->error, sizeof(h->error), "%s", msg);
    if (h->fd >= 0)
        net_close(h->fd);
    h->fd = -1;
    h->phase = PH_FAILED;
    return HTTPC_FAIL;
}

int httpc_start(httpc_t *h, uint32_t ip, uint16_t port, const char *req, size_t len,
                uint64_t now, uint32_t connect_timeout_ms, uint32_t total_timeout_ms)
{
    httpc_abort(h);
    h->error[0] = '\0';
    if (len > sizeof(h->req))
        return failf(h, "requisição grande demais");
    memcpy(h->req, req, len);
    h->req_len = len;
    h->req_off = 0;
    http_resp_init(&h->resp, h->body, HTTPC_BODY_MAX);
    h->connect_deadline = now + connect_timeout_ms;
    h->deadline = now + total_timeout_ms;
    h->fd = net_tcp_connect(ip, port);
    if (h->fd < 0) {
        h->fd = -1;
        return failf(h, net_last_error());
    }
    h->phase = PH_CONNECTING;
    return HTTPC_PENDING;
}

static int finish_ok(httpc_t *h)
{
    h->body[h->resp.body_len] = '\0';
    net_close(h->fd);
    h->fd = -1;
    h->phase = PH_DONE;
    return HTTPC_OK;
}

int httpc_step(httpc_t *h, uint64_t now)
{
    if (h->phase == PH_DONE)
        return HTTPC_OK;
    if (h->phase == PH_FAILED || h->phase == PH_IDLE)
        return HTTPC_FAIL;
    if (now > h->deadline)
        return failf(h, "tempo esgotado esperando o PC");

    if (h->phase == PH_CONNECTING) {
        int r = net_wait(h->fd, 1, 0);
        if (r == NET_ERR)
            return failf(h, net_last_error());
        if (r == 0) {
            if (now > h->connect_deadline)
                return failf(h, "tempo esgotado conectando ao PC");
            return HTTPC_PENDING;
        }
        if (net_tcp_connected(h->fd) != 0)
            return failf(h, net_last_error());
        h->phase = PH_SENDING;
    }

    if (h->phase == PH_SENDING) {
        while (h->req_off < h->req_len) {
            long n = net_send(h->fd, h->req + h->req_off, h->req_len - h->req_off);
            if (n == NET_AGAIN)
                return HTTPC_PENDING;
            if (n < 0)
                return failf(h, net_last_error());
            h->req_off += (size_t)n;
        }
        h->phase = PH_RECEIVING;
    }

    /* PH_RECEIVING: lê o que houver (limite de voltas para não travar o quadro). */
    for (int loops = 0; loops < 32; loops++) {
        char buf[4096];
        long n = net_recv(h->fd, buf, sizeof(buf));
        int r;
        if (n == NET_AGAIN)
            return HTTPC_PENDING;
        if (n == NET_ERR)
            return failf(h, net_last_error());
        if (n == 0) {
            r = http_resp_finish(&h->resp);
            if (r == HTTP_COMPLETE)
                return finish_ok(h);
            return failf(h, h->resp.error ? h->resp.error : "resposta inválida");
        }
        r = http_resp_feed(&h->resp, buf, (size_t)n);
        if (r == HTTP_COMPLETE)
            return finish_ok(h);
        if (r == HTTP_ERROR)
            return failf(h, h->resp.error ? h->resp.error : "resposta inválida");
    }
    return HTTPC_PENDING;
}
