#!/usr/bin/env bash
# Compila as rotinas de ps4_mcp/helpers/*.c em blobs .bin (só o .text, sem relocações).
# Basta o clang do sistema: o blob não usa nada do SDK do PS4. Rode depois de mudar um .c.
set -euo pipefail
cd "$(dirname "$0")/../ps4_mcp/helpers"
for src in *.c; do
    name="${src%.c}"
    clang -target x86_64-unknown-none-elf -O2 -ffreestanding -fno-builtin -fno-stack-protector \
        -fPIC -fno-jump-tables -fno-asynchronous-unwind-tables -fno-unwind-tables -c "$src" -o "/tmp/$name.o"
    if llvm-readelf -r "/tmp/$name.o" | grep -q "Relocation section"; then
        echo "$src gerou relocações: o blob não seria independente de posição" >&2
        exit 1
    fi
    llvm-objcopy -O binary --only-section=.text "/tmp/$name.o" "$name.bin"
    rm -f "/tmp/$name.o"
    echo "$name.bin: $(stat -c %s "$name.bin") bytes"
done
