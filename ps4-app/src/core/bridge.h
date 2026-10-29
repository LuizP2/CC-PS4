/*
 * Protocolo "bridge v1" entre o PC (ps4_mcp/bridge.py) e o app no PS4.
 * Aqui só há parsing/validação, sem rede:
 *   - datagrama de descoberta UDP  "CCPS4-BRIDGE v1 http=<porta>\n"
 *   - /data/ccps4/viewer.cfg       "host=<ip>", "port=<porta>", ...
 *   - GET /v1/feed  -> linhas "<seq>\t<unix_ts>\t<tipo>\t<texto>\n"
 *   - GET /v1/state -> linhas "chave=valor\n"
 *   - POST /v1/message -> "ok <id>\n"
 */
#ifndef CCPS4_BRIDGE_H
#define CCPS4_BRIDGE_H

#include <stddef.h>
#include <stdint.h>

#define BRIDGE_DEFAULT_HTTP_PORT 8765
#define BRIDGE_DISCOVERY_PORT    8766
#define BRIDGE_TEXT_MAX          300  /* bytes por texto do feed */
#define BRIDGE_MSG_MAX           512  /* bytes por mensagem enviada */
#define BRIDGE_CONFIG_PATH       "/data/ccps4/viewer.cfg"

typedef enum {
    KIND_TOOL, KIND_RESULT, KIND_EVENT, KIND_STATUS, KIND_USER, KIND_INFO, KIND_ERROR,
    KIND_COUNT
} feed_kind_t;

typedef struct {
    uint64_t seq;      /* >= 1 vindo do PC; 0 = entrada local do app */
    int64_t ts;        /* unix timestamp (segundos) */
    int kind;          /* feed_kind_t */
    uint16_t text_len;
    char text[BRIDGE_TEXT_MAX + 1];
} feed_entry_t;

typedef struct {
    int lines;      /* linhas não vazias */
    int accepted;   /* entradas entregues ao callback */
    int malformed;  /* linhas descartadas por formato inválido */
    uint64_t max_seq;
} feed_stats_t;

enum { AGENT_UNKNOWN = 0, AGENT_IDLE, AGENT_WORKING, AGENT_WAITING_EVENT };

typedef struct {
    int agent;                /* AGENT_* */
    int ps4debug;             /* -1 desconhecido, 0 desconectado, 1 conectado */
    long attached_pid;        /* -1 nenhum */
    char process[64];
    int paused;               /* -1 desconhecido, 0, 1 */
    long pending_events;      /* -1 desconhecido */
    char last_tool[64];
    long messages_pending;    /* -1 desconhecido */
} bridge_state_t;

typedef struct {
    char host[64];            /* vazio = usar descoberta UDP */
    uint16_t port;            /* porta HTTP quando host está definido */
    uint16_t udp_port;        /* porta da descoberta */
    int utc_offset_min;       /* fuso para mostrar HH:MM:SS (padrão -180, Brasília) */
    int font_scale;           /* 2 ou 3 */
} viewer_config_t;

const char *bridge_kind_name(int kind);
/* "tool" -> KIND_TOOL...; desconhecido -> -1 */
int bridge_kind_parse(const char *s, size_t n);

/* 1 se o datagrama é um anúncio v1 válido (porta em *http_port); 0 caso contrário. */
int bridge_parse_discovery(const char *buf, size_t len, uint16_t *http_port);

void bridge_config_defaults(viewer_config_t *cfg);
/* Aplica as linhas do viewer.cfg. Retorna o número de chaves reconhecidas e
 * válidas; *bad (se não for NULL) recebe o número de linhas inválidas. */
int bridge_parse_config(viewer_config_t *cfg, const char *text, size_t len, int *bad);

typedef void (*feed_entry_cb)(void *ctx, const feed_entry_t *e);
/* Faz o parse do corpo de /v1/feed. Linhas malformadas são puladas e contadas.
 * Tipo desconhecido vira "info"; texto > 300 bytes é cortado sem quebrar UTF-8;
 * TAB/controle dentro do texto viram espaço; seq precisa ser crescente dentro
 * da resposta (repetido/fora de ordem conta como malformado). */
void bridge_parse_feed(const char *body, size_t len, feed_entry_cb cb, void *ctx, feed_stats_t *st);

void bridge_state_reset(bridge_state_t *s);
/* Faz o parse de /v1/state; chaves desconhecidas são ignoradas. Retorna o
 * número de chaves conhecidas encontradas. */
int bridge_parse_state(const char *body, size_t len, bridge_state_t *s);

/* "ok <id>" -> 1 e *id; senão 0. */
int bridge_parse_ok(const char *body, size_t len, long *id);

/* 0 se a mensagem pode ser enviada; senão texto do motivo (pt-BR). */
const char *bridge_check_message(const char *msg, size_t len);

#endif
