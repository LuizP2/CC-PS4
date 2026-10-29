/*
 * Estado e lógica do CCPS4 Agent Viewer (sem nada de plataforma):
 * descoberta do PC, ciclo de polling (state + feed ~1x/s), envio de mensagens,
 * reconexão (3 s), feed em anel, rolagem, frases prontas, teclado e avisos.
 */
#ifndef CCPS4_VIEWER_H
#define CCPS4_VIEWER_H

#include <stddef.h>
#include <stdint.h>

#include "bridge.h"
#include "httpc.h"

/* Botões abstratos (a plataforma converte os do controle para estes). */
enum {
    VB_UP = 1u << 0, VB_DOWN = 1u << 1, VB_LEFT = 1u << 2, VB_RIGHT = 1u << 3,
    VB_CROSS = 1u << 4, VB_CIRCLE = 1u << 5, VB_TRIANGLE = 1u << 6, VB_SQUARE = 1u << 7,
    VB_L1 = 1u << 8, VB_R1 = 1u << 9, VB_OPTIONS = 1u << 10,
};

typedef enum { CONN_DISCOVERING, CONN_CONNECTING, CONN_ONLINE, CONN_OFFLINE } viewer_conn_t;
enum { REQ_NONE, REQ_STATE, REQ_FEED, REQ_MESSAGE };
enum { TOAST_INFO, TOAST_OK, TOAST_ERROR };

#define VIEWER_POLL_MS     1000
#define VIEWER_RETRY_MS    3000
#define VIEWER_TOAST_MS    3500
#define VIEWER_CONNECT_MS  2000
#define VIEWER_REQUEST_MS  5000
#define VIEWER_IME_CHARS   256
#define FEED_CAP           400

typedef struct {
    feed_entry_t items[FEED_CAP];
    int head;  /* índice da entrada mais antiga */
    int count;
} feed_ring_t;

typedef struct viewer {
    viewer_config_t cfg;
    int host_configured;

    viewer_conn_t conn;
    uint32_t pc_ip;
    uint16_t pc_port;
    char pc_label[32];        /* "192.168.0.10:8765" */
    int udp_fd;
    uint64_t udp_retry_at;
    uint64_t retry_at, next_poll_at, last_ok_at;
    char last_error[128];
    int was_online;

    httpc_t http;
    int req;
    int feed_due;             /* depois de um STATE ok, vem um FEED */
    int resync;               /* próximo FEED usa since=0 (após (re)conexão) */

    bridge_state_t state;
    int have_state;
    feed_ring_t feed;
    uint64_t max_seq;
    unsigned malformed_total;
    unsigned polls_ok, sent_ok;

    char out_msg[BRIDGE_MSG_MAX + 1];
    size_t out_len;
    int out_pending;
    int out_from_ime;

    int scroll;               /* linhas acima do fim (0 = acompanha o mais novo) */
    int unseen;               /* linhas novas chegadas enquanto rolado para cima */
    int phrase;
    char toast[240];
    int toast_kind;
    uint64_t toast_until;

    int ime_active;
    char ime_last[BRIDGE_MSG_MAX + 1]; /* texto digitado que ainda não foi entregue */
    uint32_t held;
    int latch;                /* espera soltar todos os botões (após o teclado) */
    uint64_t repeat_at;
    uint64_t last_sec;
    int dirty;

    /* Preenchidos por ui_render() (layout do último quadro). */
    int feed_cols;            /* colunas do texto do feed */
    int feed_rows;            /* linhas visíveis */
    int feed_total_lines;
} viewer_t;

extern const char *const viewer_phrases[];
extern const int viewer_phrase_count;

void viewer_init(viewer_t *v, const viewer_config_t *cfg, uint64_t now);
void viewer_shutdown(viewer_t *v);
/* buttons: bits VB_* atualmente pressionados. */
void viewer_input(viewer_t *v, uint32_t buttons, uint64_t now);
/* Avança rede/temporizadores sem bloquear. Chamar a cada quadro. */
void viewer_tick(viewer_t *v, uint64_t now);
/* Enfileira uma mensagem para o PC. 0 se aceitou; <0 se recusou (com aviso na tela). */
int viewer_send(viewer_t *v, const char *utf8, size_t len, uint64_t now);

/* Acesso ao feed: i = 0 é a entrada mais antiga. */
const feed_entry_t *viewer_feed_at(const viewer_t *v, int i);
/* Entrada local (seq 0), ex.: "conectado ao PC". */
void viewer_add_local(viewer_t *v, int kind, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
void viewer_toast(viewer_t *v, int kind, uint64_t now, const char *fmt, ...) __attribute__((format(printf, 4, 5)));

#endif
