# CCPS4 Agent Viewer — app do PS4 que mostra o que o agente no PC está fazendo.
#
#   make            -> PKG do PS4 em build/IV0000-CCPS00001_00-CCPS4AGENTVIEWER.pkg
#                      (requer OO_PS4_TOOLCHAIN; rode scripts/install_openorbis.sh)
#   make host-test  -> testes no Linux (gcc + ASan/UBSan): unitários do núcleo e
#                      ponta a ponta contra test_host/bridge_stub.py; gera os PNG
#                      em build/host/screenshots/
#   make icon       -> regenera sce_sys/icon0.png
#   make clean      -> apaga build/

TITLE      := CCPS4 Agent Viewer
VERSION    := 1.00
TITLE_ID   := CCPS00001
CONTENT_ID := IV0000-CCPS00001_00-CCPS4AGENTVIEWER

BUILD      := build
PYTHON     := python3

CORE_SRCS  := $(wildcard src/core/*.c)
CORE_HDRS  := $(wildcard src/core/*.h) src/platform.h

# ------------------------------------------------------------------ PS4
TOOLCHAIN  := $(OO_PS4_TOOLCHAIN)
TOOLBIN    := $(TOOLCHAIN)/bin/linux
OPENSSL11_DIR ?= $(abspath $(TOOLCHAIN)/../openssl-1.1/lib)
PKGTOOL    := DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 \
              $(if $(wildcard $(OPENSSL11_DIR)/libssl.so.1.1),LD_LIBRARY_PATH=$(OPENSSL11_DIR)) \
              $(TOOLBIN)/PkgTool.Core
SAMPLE_DIR := $(TOOLCHAIN)/samples/hello_world

PS4_OBJDIR := $(BUILD)/ps4/obj
STAGE      := $(BUILD)/pkg
ELF        := $(BUILD)/ps4/viewer.elf
OELF       := $(BUILD)/ps4/viewer.oelf
EBOOT      := $(STAGE)/eboot.bin
PKG        := $(BUILD)/$(CONTENT_ID).pkg

PS4_SRCS   := $(CORE_SRCS) src/net_bsd.c src/platform_ps4.c
PS4_OBJS   := $(patsubst src/%.c,$(PS4_OBJDIR)/%.o,$(PS4_SRCS))

PS4_CC     := clang
PS4_LD     := ld.lld
PS4_CFLAGS := --target=x86_64-pc-freebsd12-elf -fPIC -funwind-tables -fno-omit-frame-pointer \
              -isysroot $(TOOLCHAIN) -isystem $(TOOLCHAIN)/include -Isrc \
              -std=gnu11 -O2 -g -Wall -Wextra -Wno-format-truncation -DCCPS4_PLATFORM_PS4=1
PS4_LDFLAGS := -m elf_x86_64 -pie --script $(TOOLCHAIN)/link.x --eh-frame-hdr -L$(TOOLCHAIN)/lib
PS4_LIBS   := -lc -lkernel -lSceVideoOut -lScePad -lSceUserService -lSceSysmodule \
              -lSceImeDialog -lSceCommonDialog

PKG_FILES  := eboot.bin sce_sys/param.sfo sce_sys/icon0.png sce_sys/about/right.sprx \
              sce_module/libc.prx sce_module/libSceFios2.prx

# ------------------------------------------------------------------ host
HOST_CC    := gcc
HOST_DIR   := $(BUILD)/host
HOST_CFLAGS := -std=gnu11 -O1 -g -Wall -Wextra -Werror -Wno-format-truncation \
               -fsanitize=address,undefined -fno-omit-frame-pointer -Isrc
HOST_SRCS  := $(CORE_SRCS) src/net_bsd.c src/platform_host.c

.PHONY: all pkg host-test icon clean ps4-toolchain-check
all: pkg
pkg: $(PKG)

ps4-toolchain-check:
	@if [ -z "$(TOOLCHAIN)" ] || [ ! -x "$(TOOLBIN)/create-fself" ]; then \
		echo "ERRO: defina OO_PS4_TOOLCHAIN (rode scripts/install_openorbis.sh na raiz do repo)"; exit 1; fi
export OO_PS4_TOOLCHAIN

$(PS4_OBJDIR)/%.o: src/%.c $(CORE_HDRS) | ps4-toolchain-check
	@mkdir -p $(dir $@)
	$(PS4_CC) $(PS4_CFLAGS) -c $< -o $@

$(ELF): $(PS4_OBJS)
	$(PS4_LD) $(PS4_OBJS) -o $@ $(PS4_LDFLAGS) $(PS4_LIBS) $(TOOLCHAIN)/lib/crt1.o

$(EBOOT): $(ELF)
	@mkdir -p $(STAGE)/sce_sys/about $(STAGE)/sce_module
	$(TOOLBIN)/create-fself -in=$(abspath $(ELF)) -out=$(abspath $(OELF)) \
		--eboot $(abspath $(EBOOT)) --paid 0x3800000000000011

$(STAGE)/sce_sys/param.sfo: Makefile | ps4-toolchain-check
	@mkdir -p $(dir $@)
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

sce_sys/icon0.png: tools/make_icon.py src/core/font8x8_basic.h
	$(PYTHON) -I tools/make_icon.py src/core/font8x8_basic.h $@

icon: sce_sys/icon0.png

$(STAGE)/sce_sys/icon0.png: sce_sys/icon0.png
	@mkdir -p $(dir $@)
	cp $< $@

$(STAGE)/sce_sys/about/right.sprx: | ps4-toolchain-check
	@mkdir -p $(dir $@)
	cp $(SAMPLE_DIR)/sce_sys/about/right.sprx $@

$(STAGE)/sce_module/%.prx: | ps4-toolchain-check
	@mkdir -p $(dir $@)
	cp $(SAMPLE_DIR)/sce_module/$*.prx $@

$(STAGE)/pkg.gp4: $(addprefix $(STAGE)/,$(PKG_FILES))
	cd $(STAGE) && $(TOOLBIN)/create-gp4 -out pkg.gp4 --content-id=$(CONTENT_ID) --files "$(PKG_FILES)"

$(PKG): $(STAGE)/pkg.gp4
	cd $(STAGE) && $(PKGTOOL) pkg_build pkg.gp4 $(abspath $(BUILD))
	@ls -la $@

# ------------------------------------------------------------------ host
$(HOST_DIR)/test_core: test_host/test_core.c $(HOST_SRCS) $(CORE_HDRS) src/platform_host.h
	@mkdir -p $(HOST_DIR)
	$(HOST_CC) $(HOST_CFLAGS) -o $@ test_host/test_core.c $(HOST_SRCS)

$(HOST_DIR)/e2e_main: test_host/e2e_main.c $(HOST_SRCS) $(CORE_HDRS) src/platform_host.h
	@mkdir -p $(HOST_DIR)
	$(HOST_CC) $(HOST_CFLAGS) -o $@ test_host/e2e_main.c $(HOST_SRCS)

host-test: $(HOST_DIR)/test_core $(HOST_DIR)/e2e_main
	$(HOST_DIR)/test_core
	PYTHON=$(PYTHON) bash test_host/run_host_test.sh

clean:
	rm -rf $(BUILD)
