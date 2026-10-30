/* Varredura de valor que roda DENTRO do processo do jogo, chamada pelo PROC_CALL do ps4debug.
 *
 * O blob é só o .text desta função: código independente de posição, sem libc, sem syscall, sem
 * dados globais. Por isso serve em qualquer firmware (é x86-64 puro, ABI System V) e um bug derruba
 * o jogo, não o console. Compile com scripts/build_helpers.sh; o MCP carrega scan_value.bin.
 *
 * scan_value(start, len, value, size, out, cap) -> total de achados
 *   procura `value` (os `size` bytes baixos, little-endian) em [start, start+len), só em endereços
 *   alinhados a `size` (1, 2, 4 ou 8). Grava até `cap` endereços em out[] e devolve o total, que
 *   pode passar de cap (aí a lista está cortada). size inválido devolve 0.
 */

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;

#define SCAN(T)                                                         \
    do {                                                                \
        const T *p = (const T *)start, *e = p + len / sizeof(T);        \
        const T v = (T)value;                                           \
        for (; p < e; p++)                                              \
            if (*p == v) {                                              \
                if (n < cap)                                            \
                    out[n] = (u64)p;                                    \
                n++;                                                    \
            }                                                           \
    } while (0)

u64 scan_value(const u8 *start, u64 len, u64 value, u64 size, u64 *out, u64 cap)
{
    u64 n = 0;
    if (size == 1)
        SCAN(u8);
    else if (size == 2)
        SCAN(u16);
    else if (size == 4)
        SCAN(u32);
    else if (size == 8)
        SCAN(u64);
    return n;
}
