/*
 * Interface entre o núcleo (src/core, independente de plataforma) e as camadas
 * finas de plataforma:
 *   - net_bsd.c        sockets BSD (o MESMO código roda no Linux e no PS4)
 *   - platform_ps4.c   VideoOut, scePad, teclado IME, relógio, arquivo, log
 *   - platform_host.c  Linux: relógio, arquivo, log, IME simulado, quadros em PPM
 */
#ifndef CCPS4_PLATFORM_H
#define CCPS4_PLATFORM_H

#include <stddef.h>
#include <stdint.h>

/* ---------------------------------------------------------------- tempo/log */
uint64_t plat_now_ms(void); /* relógio monotônico em ms */
void plat_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

/* Lê um arquivo inteiro (até cap-1 bytes, termina em NUL). Retorna o tamanho ou -1. */
long plat_read_file(const char *path, char *buf, size_t cap);

/* ---------------------------------------------------------------- teclado */
enum { IME_RUNNING = 0, IME_DONE = 1, IME_CANCELED = 2, IME_FAILED = -1 };

/* Abre o teclado do sistema. initial_utf8 pode ser NULL. Retorna 0 se abriu,
 * <0 se indisponível (o app mostra "teclado indisponível" e segue). */
int plat_ime_open(const char *title_utf8, const char *initial_utf8, int max_chars);
/* Consulta o teclado aberto. Em IME_DONE o texto digitado vai para out (UTF-8). */
int plat_ime_poll(char *out, size_t cap);

/* ---------------------------------------------------------------- rede */
#define NET_AGAIN (-1) /* operação ainda não pode prosseguir (não bloqueante) */
#define NET_ERR   (-2)

int net_init(void); /* ignora SIGPIPE etc. Retorna 0. */
/* Socket UDP não bloqueante em 0.0.0.0:port (SO_REUSEADDR). fd ou NET_ERR. */
int net_udp_bind(uint16_t port);
/* Bytes recebidos (>=0), NET_AGAIN ou NET_ERR. IP em ordem do host. */
long net_udp_recv(int fd, char *buf, size_t cap, uint32_t *src_ip, uint16_t *src_port);
/* Inicia connect() não bloqueante. fd ou NET_ERR. */
int net_tcp_connect(uint32_t ip, uint16_t port);
/* Espera o fd ficar pronto (escrita se for_write, senão leitura).
 * 1 pronto, 0 ainda não, NET_ERR erro. */
int net_wait(int fd, int for_write, int timeout_ms);
/* Depois de pronto para escrita: 0 se conectou, NET_ERR se falhou. */
int net_tcp_connected(int fd);
long net_send(int fd, const void *p, size_t n); /* >=0, NET_AGAIN, NET_ERR */
long net_recv(int fd, void *p, size_t n);       /* >0, 0 (EOF), NET_AGAIN, NET_ERR */
void net_close(int fd);
int net_parse_ipv4(const char *s, uint32_t *ip);
void net_format_ipv4(uint32_t ip, char *out, size_t cap);
const char *net_last_error(void); /* descrição do último erro de socket */

#endif
