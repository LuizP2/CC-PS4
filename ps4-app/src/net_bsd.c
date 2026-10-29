/*
 * Sockets BSD não bloqueantes. Compila igual no Linux (glibc) e no PS4
 * (headers BSD do OpenOrbis; as funções socket/connect/poll/... vêm da
 * libkernel). Diferenças tratadas aqui:
 *   - SIGPIPE: Linux usa MSG_NOSIGNAL; no PS4 (FreeBSD) usa SO_NOSIGPIPE
 *     (0x0800, não definido nos headers do OpenOrbis) + SIGPIPE ignorado.
 *   - não bloqueante via fcntl(F_SETFL, O_NONBLOCK) (FIONBIO dos headers do
 *     OpenOrbis tem o valor do Linux, por isso não é usado).
 */
#include "platform.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#if defined(__linux__)
#define SEND_FLAGS MSG_NOSIGNAL
#else
#define SEND_FLAGS 0
#ifndef SO_NOSIGPIPE
#define SO_NOSIGPIPE 0x0800 /* FreeBSD */
#endif
#endif

static char g_err[96];

static void set_err(const char *what)
{
    snprintf(g_err, sizeof(g_err), "%s: %s (errno %d)", what, strerror(errno), errno);
}

const char *net_last_error(void)
{
    return g_err;
}

int net_init(void)
{
    signal(SIGPIPE, SIG_IGN);
    return 0;
}

static int set_nonblocking(int fd)
{
    int fl = fcntl(fd, F_GETFL, 0);
    if (fl < 0)
        fl = 0;
    return fcntl(fd, F_SETFL, fl | O_NONBLOCK);
}

static void no_sigpipe(int fd)
{
#if defined(SO_NOSIGPIPE)
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof(one));
#else
    (void)fd;
#endif
}

static void fill_addr(struct sockaddr_in *sa, uint32_t ip, uint16_t port)
{
    memset(sa, 0, sizeof(*sa));
#if !defined(__linux__)
    sa->sin_len = sizeof(*sa);
#endif
    sa->sin_family = AF_INET;
    sa->sin_port = htons(port);
    sa->sin_addr.s_addr = htonl(ip);
}

int net_udp_bind(uint16_t port)
{
    struct sockaddr_in sa;
    int one = 1;
    int fd = socket(AF_INET, SOCK_DGRAM, 0);

    if (fd < 0) {
        set_err("socket UDP");
        return NET_ERR;
    }
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
#if defined(SO_REUSEPORT)
    setsockopt(fd, SOL_SOCKET, SO_REUSEPORT, &one, sizeof(one));
#endif
    fill_addr(&sa, INADDR_ANY, port);
    if (bind(fd, (struct sockaddr *)&sa, sizeof(sa)) != 0 || set_nonblocking(fd) != 0) {
        set_err("bind UDP");
        close(fd);
        return NET_ERR;
    }
    return fd;
}

long net_udp_recv(int fd, char *buf, size_t cap, uint32_t *src_ip, uint16_t *src_port)
{
    struct sockaddr_in sa;
    socklen_t sl = sizeof(sa);
    long n = (long)recvfrom(fd, buf, cap, 0, (struct sockaddr *)&sa, &sl);

    if (n < 0) {
        if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)
            return NET_AGAIN;
        set_err("recvfrom");
        return NET_ERR;
    }
    if (src_ip)
        *src_ip = ntohl(sa.sin_addr.s_addr);
    if (src_port)
        *src_port = ntohs(sa.sin_port);
    return n;
}

int net_tcp_connect(uint32_t ip, uint16_t port)
{
    struct sockaddr_in sa;
    int fd = socket(AF_INET, SOCK_STREAM, 0);

    if (fd < 0) {
        set_err("socket TCP");
        return NET_ERR;
    }
    no_sigpipe(fd);
    if (set_nonblocking(fd) != 0) {
        set_err("fcntl O_NONBLOCK");
        close(fd);
        return NET_ERR;
    }
    fill_addr(&sa, ip, port);
    if (connect(fd, (struct sockaddr *)&sa, sizeof(sa)) != 0 && errno != EINPROGRESS &&
        errno != EWOULDBLOCK && errno != EINTR && errno != EALREADY) {
        set_err("connect");
        close(fd);
        return NET_ERR;
    }
    return fd;
}

int net_wait(int fd, int for_write, int timeout_ms)
{
    struct pollfd p;
    int r;

    p.fd = fd;
    p.events = for_write ? POLLOUT : POLLIN;
    p.revents = 0;
    r = poll(&p, 1, timeout_ms);
    if (r < 0) {
        if (errno == EINTR)
            return 0;
        set_err("poll");
        return NET_ERR;
    }
    if (r == 0)
        return 0;
    /* POLLERR/POLLHUP também contam como "pronto": a próxima chamada mostra o erro. */
    return 1;
}

int net_tcp_connected(int fd)
{
    int err = 0;
    socklen_t len = sizeof(err);
    struct sockaddr_in peer;
    socklen_t pl = sizeof(peer);

    if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &len) != 0) {
        set_err("getsockopt SO_ERROR");
        return NET_ERR;
    }
    if (err != 0) {
        errno = err;
        set_err("connect");
        return NET_ERR;
    }
    /* Confirmação extra que não depende de errno: só está conectado se tem par. */
    if (getpeername(fd, (struct sockaddr *)&peer, &pl) != 0) {
        set_err("connect (sem par)");
        return NET_ERR;
    }
    return 0;
}

long net_send(int fd, const void *p, size_t n)
{
    long r = (long)send(fd, p, n, SEND_FLAGS);
    if (r < 0) {
        if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)
            return NET_AGAIN;
        set_err("send");
        return NET_ERR;
    }
    return r;
}

long net_recv(int fd, void *p, size_t n)
{
    long r = (long)recv(fd, p, n, 0);
    if (r < 0) {
        if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)
            return NET_AGAIN;
        set_err("recv");
        return NET_ERR;
    }
    return r;
}

void net_close(int fd)
{
    if (fd >= 0)
        close(fd);
}

int net_parse_ipv4(const char *s, uint32_t *ip)
{
    uint32_t v = 0;
    int parts = 0;

    while (parts < 4) {
        unsigned x = 0;
        int digits = 0;
        while (*s >= '0' && *s <= '9' && digits < 4) {
            x = x * 10 + (unsigned)(*s++ - '0');
            digits++;
        }
        if (digits == 0 || digits > 3 || x > 255)
            return 0;
        v = (v << 8) | x;
        parts++;
        if (parts < 4) {
            if (*s != '.')
                return 0;
            s++;
        }
    }
    if (*s != '\0')
        return 0;
    *ip = v;
    return 1;
}

void net_format_ipv4(uint32_t ip, char *out, size_t cap)
{
    snprintf(out, cap, "%u.%u.%u.%u", (ip >> 24) & 255u, (ip >> 16) & 255u, (ip >> 8) & 255u, ip & 255u);
}
