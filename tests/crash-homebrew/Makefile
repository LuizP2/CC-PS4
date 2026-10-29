# CCPS4 Crash Test — homebrew com crash proposital (alvo da fase 3).
#
#   make        -> build/crashtest.elf, build/pkg/eboot.bin (fself),
#                  build/IV0000-CCPS00002_00-CCPS4CRASHTEST00.pkg,
#                  build/symbols.txt, build/offsets.txt, build/crash_here.disasm.txt
#   make clean  -> apaga build/
#
# Requer OO_PS4_TOOLCHAIN (rode scripts/install_openorbis.sh na raiz do repo),
# clang, ld.lld, llvm-nm, llvm-objdump e python3 no PATH.

TITLE      := CCPS4 Crash Test
VERSION    := 1.00
TITLE_ID   := CCPS00002
CONTENT_ID := IV0000-CCPS00002_00-CCPS4CRASHTEST00

TOOLCHAIN  := $(OO_PS4_TOOLCHAIN)
TOOLBIN    := $(TOOLCHAIN)/bin/linux
# PkgTool.Core é um app .NET Core 3.0: sem ICU ele aborta (daí o modo invariante)
# e o pkg_build precisa de OpenSSL 1.1. Em hosts só com OpenSSL 3, o
# scripts/install_openorbis.sh baixa libssl.so.1.1 para $(OO_PS4_TOOLCHAIN)/../openssl-1.1/lib;
# se essa pasta existir, ela entra no LD_LIBRARY_PATH só do PkgTool.Core.
OPENSSL11_DIR ?= $(abspath $(TOOLCHAIN)/../openssl-1.1/lib)
PKGTOOL    := DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 \
              $(if $(wildcard $(OPENSSL11_DIR)/libssl.so.1.1),LD_LIBRARY_PATH=$(OPENSSL11_DIR)) \
              $(TOOLBIN)/PkgTool.Core
# Módulos e right.sprx que todo PKG do OpenOrbis leva (vêm com os samples do toolchain).
SAMPLE_DIR := $(TOOLCHAIN)/samples/hello_world

BUILD   := build
OBJDIR  := $(BUILD)/obj
STAGE   := $(BUILD)/pkg
ELF     := $(BUILD)/crashtest.elf
OELF    := $(BUILD)/crashtest.oelf
EBOOT   := $(STAGE)/eboot.bin
PKG     := $(BUILD)/$(CONTENT_ID).pkg

CC      := clang
LD      := ld.lld
NM      := llvm-nm
OBJDUMP := llvm-objdump
PYTHON  := python3

# -O0 + -fno-omit-frame-pointer: crash_here fica com prólogo normal e o acesso
# a p->value não é otimizado; -g deixa DWARF no ELF para o Ghidra.
CFLAGS  := --target=x86_64-pc-freebsd12-elf -fPIC -funwind-tables -fno-omit-frame-pointer \
           -isysroot $(TOOLCHAIN) -isystem $(TOOLCHAIN)/include \
           -std=gnu11 -O0 -g -Wall -Wextra
LDFLAGS := -m elf_x86_64 -pie --script $(TOOLCHAIN)/link.x --eh-frame-hdr -L$(TOOLCHAIN)/lib
LIBS    := -lc -lkernel -lSceVideoOut

SRCS := src/main.c src/video.c
OBJS := $(patsubst src/%.c,$(OBJDIR)/%.o,$(SRCS))

PKG_FILES := eboot.bin sce_sys/param.sfo sce_sys/icon0.png sce_sys/about/right.sprx \
             sce_module/libc.prx sce_module/libSceFios2.prx

.PHONY: all clean
all: $(PKG) $(BUILD)/symbols.txt $(BUILD)/offsets.txt

ifeq ($(filter clean,$(MAKECMDGOALS)),)
ifeq ($(strip $(TOOLCHAIN)),)
$(error OO_PS4_TOOLCHAIN não definido. Rode scripts/install_openorbis.sh e exporte a variável)
endif
ifeq ($(wildcard $(TOOLBIN)/create-fself),)
$(error $(TOOLBIN)/create-fself não existe; OO_PS4_TOOLCHAIN aponta para o lugar certo?)
endif
endif
export OO_PS4_TOOLCHAIN

$(OBJDIR) $(STAGE)/sce_sys/about $(STAGE)/sce_module:
	mkdir -p $@

$(OBJDIR)/%.o: src/%.c src/video.h Makefile | $(OBJDIR)
	$(CC) $(CFLAGS) -c $< -o $@

$(ELF): $(OBJS)
	$(LD) $(OBJS) -o $@ $(LDFLAGS) $(LIBS) $(TOOLCHAIN)/lib/crt1.o

# ELF -> OELF -> eboot.bin (fake SELF), igual aos samples do OpenOrbis.
$(EBOOT): $(ELF) | $(STAGE)/sce_sys/about
	$(TOOLBIN)/create-fself -in=$(abspath $(ELF)) -out=$(abspath $(OELF)) \
		--eboot $(abspath $(EBOOT)) --paid 0x3800000000000011

$(STAGE)/sce_sys/param.sfo: Makefile | $(STAGE)/sce_sys/about
	rm -f $@
	$(PKGTOOL) sfo_new $@
	$(PKGTOOL) sfo_setentry $@ APP_TYPE --type Integer --maxsize 4 --value 1
	$(PKGTOOL) sfo_setentry $@ APP_VER --type Utf8 --maxsize 8 --value '$(VERSION)'
	$(PKGTOOL) sfo_setentry $@ ATTRIBUTE --type Integer --maxsize 4 --value 0
	$(PKGTOOL) sfo_setentry $@ CATEGORY --type Utf8 --maxsize 4 --value 'gd'
	$(PKGTOOL) sfo_setentry $@ CONTENT_ID --type Utf8 --maxsize 48 --value '$(CONTENT_ID)'
	$(PKGTOOL) sfo_setentry $@ DOWNLOAD_DATA_SIZE --type Integer --maxsize 4 --value 0
	$(PKGTOOL) sfo_setentry $@ SYSTEM_VER --type Integer --maxsize 4 --value 0
	$(PKGTOOL) sfo_setentry $@ TITLE --type Utf8 --maxsize 128 --value '$(TITLE)'
	$(PKGTOOL) sfo_setentry $@ TITLE_ID --type Utf8 --maxsize 12 --value '$(TITLE_ID)'
	$(PKGTOOL) sfo_setentry $@ VERSION --type Utf8 --maxsize 8 --value '$(VERSION)'

$(STAGE)/sce_sys/icon0.png: tools/make_icon.py | $(STAGE)/sce_sys/about
	$(PYTHON) -I tools/make_icon.py $@

$(STAGE)/sce_sys/about/right.sprx: | $(STAGE)/sce_sys/about
	cp $(SAMPLE_DIR)/sce_sys/about/right.sprx $@

$(STAGE)/sce_module/%.prx: | $(STAGE)/sce_module
	cp $(SAMPLE_DIR)/sce_module/$*.prx $@

$(STAGE)/pkg.gp4: $(addprefix $(STAGE)/,$(PKG_FILES))
	cd $(STAGE) && $(TOOLBIN)/create-gp4 -out pkg.gp4 --content-id=$(CONTENT_ID) --files "$(PKG_FILES)"

$(PKG): $(STAGE)/pkg.gp4
	cd $(STAGE) && $(PKGTOOL) pkg_build pkg.gp4 $(abspath $(BUILD))
	@ls -la $@

$(BUILD)/symbols.txt: $(ELF)
	$(NM) -n $< > $@

$(BUILD)/crash_here.disasm.txt: $(ELF)
	$(OBJDUMP) -d --no-show-raw-insn -M intel --disassemble-symbols=crash_here $< > $@

$(BUILD)/offsets.txt: $(ELF) $(BUILD)/crash_here.disasm.txt tools/gen_offsets.py
	$(PYTHON) -I tools/gen_offsets.py $(ELF) --disasm $(BUILD)/crash_here.disasm.txt > $@
	@cat $@

clean:
	rm -rf $(BUILD)
