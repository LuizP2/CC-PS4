# PS4 Agent (CC-PS4)

[English](README.md) | [Português (BR)](README.pt-BR.md) | **Español**

Le da a Claude Code acceso al PS4 en tiempo de ejecución para que investigue bugs de ports por su cuenta, en bucle: leer memoria, poner breakpoints y watchpoints, recibir el evento cuando se disparan, leer registros, hilos, archivos y logs, y cruzarlo todo con el binario en Ghidra a través de ReVa. El plan está en [`ps4-claude-agent-plano.md`](ps4-claude-agent-plano.md) (en portugués).

El bucle del agente corre en el PC. En la consola quedan el payload ps4debug y, opcionalmente, la app **CCPS4 Agent Viewer**, que muestra lo que hace el agente y permite mandar mensajes desde el sofá.

```
Claude Code ── MCP stdio ──► ps4_mcp (PC) ──► ps4debug :744 / callback :755 (memoria, debug)
                                  ├────────► FTP de GoldHEN :2121 (archivos)
                                  ├────────► klog :3232 (logs)
                                  ├────────► bridge HTTP :8765 + UDP :8766 ◄── app CCPS4 Agent Viewer (PS4)
                                  └── dump_module → ELF → ReVa/Ghidra (pseudocódigo)
```

## Estado por fase

| Fase | Qué existe | Validado sin PS4 |
|---|---|---|
| 0. Primitivas | `scripts/fase0_smoke.py` (lista procesos, lee 64 bytes, prueba FTP/klog), `scripts/send_payload.py` | contra el ps4debug falso |
| 1. MCP de lectura | `ps4_status`, `list_processes`, `process_maps`, `read_memory`, `read_pointer_chain`, `read_string`, `resolve_address`, `disassemble` | pruebas de punta a punta vía MCP stdio |
| 2. Archivos y logs | `list_dir`, `read_file`, `download_file`, `tail_logs` (FTP + klog TCP/UDP/archivo) | FTP real (pyftpdlib), klog falso |
| 3. Debug y eventos | `attach`/`detach`, `set_breakpoint`/`clear_breakpoint`, `set_watchpoint`/`clear_watchpoint`, `wait_for_event`, `get_registers`, `list_threads`, `step`, `resume`/`pause` + snapshot (sección 4.4) + PKG de prueba `tests/crash-homebrew` | ps4debug falso fiel al código C + validación cruzada con la lib `ps4debug` de PyPI |
| 4. Integración con RE | `dump_module` (memoria descifrada → ELF con direcciones reales) + ReVa/Ghidra 12.1.3 instalados | ELF importado y decompilado en ReVa |
| 5. Prueba de valor | `mark_milestone`, `scripts/metrics.py`, `docs/relatorio-fase5.md`, herramientas del piloto KH3 (`search_memory`, `scan_value`, `rescan`, `read_text_region`, `search_files`, `compare_bytes`), `docs/kh3-formato-legendas.md` | herramientas probadas; la prueba de valor en sí necesita la consola |
| 6. Opcional | app `ps4-app/` (PKG), bridge (`post_status`, `get_messages`, los mensajes llegan en `wait_for_event`), `notify_console`, `build_homebrew`/`install_pkg` | build de los PKG, cliente de la app compilado y probado en el PC, instalación contra un RPI falso |

Queda fuera, como pide el propio plan: subagentes por área del port (solo cuando el flujo de un agente esté sólido) y overlay sobre el juego (no hay un método estable). Para avisar a quien está jugando, `notify_console` usa la notificación del sistema, que aparece encima del juego.

## Primer uso con la consola

1. **En el PS4:**
   - Con GoldHEN, activa el servidor FTP (puerto 2121) y el BinLoader (puerto 9090).
   - Envía el payload ps4debug **de la versión de tu firmware**: `uv run python scripts/send_payload.py ps4debug.bin --ip <IP del PS4>`.
   - Para logs, activa un servidor de klog en el puerto 3232 (o ajusta `PS4_LOG_SOURCE`).
2. **En el PC, una vez:**
   ```bash
   echo 'export PS4_IP=192.168.0.50' >> ~/.zshrc         # opcional: sin él, la IP se descubre en la red
   sudo sysctl -w net.ipv4.ip_unprivileged_port_start=755  # ps4debug se conecta de vuelta al puerto 755 del PC
   echo 'net.ipv4.ip_unprivileged_port_start=755' | sudo tee /etc/sysctl.d/99-ps4debug.conf
   ```
   Si hay firewall, permite TCP 755 desde el PS4 (y TCP 8765/UDP 8766 si vas a usar la app).
3. **Valida sin IA:** `uv run python scripts/fase0_smoke.py --ftp --logs`. Si termina con OK, la fase 0 está lista.
4. **En Claude Code:** reinicia la sesión en esta carpeta y aprueba el servidor `ps4` del `.mcp.json`. Pide "ejecuta ps4_diagnose" y después "¿qué juego está corriendo y qué módulos están cargados?", que es el criterio de la fase 1.

> ⚠️ Si un ATTACH falla por el puerto 755 o el firewall, ps4debug se queda atascado en "debugging". Es un bug del propio payload. Reinicia la consola y vuelve a cargar el payload. `ps4_diagnose` existe para evitarlo.

## Rendimiento y recomendaciones de uso

### Red: qué limita la lectura de memoria

Medido el 2026-10-29 con el PS4 y el PC en Wi-Fi de 2.4 GHz (PC con enlace de 300 Mbit/s y señal de −39 dBm):

| Prueba | Velocidad |
|---|---|
| ps4debug, 1 conexión, bloques de 1 MiB | 1.8–3.3 MiB/s |
| ps4debug, 8 conexiones en paralelo | 2.2 MiB/s |
| FTP de GoldHEN | 1.2–1.8 MiB/s |

ps4debug y el FTP son código distinto en la consola y chocan con el mismo techo. Más conexiones tampoco ayudan. El límite es el Wi-Fi del PS4, no el software. Paralelizar o hacer peticiones asíncronas solo reparte el mismo enlace. Recorrer los ~460 MiB del heap de un juego tarda unos 4 minutos así.

Para medir tu red: `uv run python scripts/bench_read.py`.

**Cable directo PC ↔ PS4, sin depender del router.** Es la mejor opción cuando no puedes llevar un cable hasta el router. El PC sigue en internet por Wi-Fi y comparte la conexión con el PS4 por el cable:

1. Conecta un cable de red normal entre el PS4 y el PC. No hace falta cable cruzado: los dos puertos lo detectan solos.
2. En el PC (NetworkManager), comparte la conexión por el puerto con cable. El PC pasa a ser `10.42.0.1` y le da IP al PS4 por DHCP:
   ```bash
   nmcli connection add type ethernet ifname enp3s0 con-name ps4-direto ipv4.method shared ipv6.method disabled
   nmcli connection up ps4-direto
   ```
   Cambia `enp3s0` por el nombre de tu puerto (`ip -br link`).
3. En el PS4: Ajustes → Red → Configurar conexión a Internet → **Usar un cable LAN** → Fácil. El PS4 apaga el Wi-Fi y sale a internet a través del PC.
4. Anota la IP que recibió el PS4 (`10.42.0.x`, aparece en Red → Ver estado de la conexión) y usa `export PS4_IP=10.42.0.x`. Define la IP explícitamente: el descubrimiento automático puede buscar en la red del Wi-Fi.
5. ps4debug se conecta de vuelta a `10.42.0.1:755`. Si el firewall está activo, permite el puerto 755 también en esa interfaz.

**Medido con cable directo (2026-10-29):** **10.1 MiB/s** con una conexión, 5× el Wi-Fi. El cable usado tenía un falso contacto y el enlace bajó a 100 Mbps (`Downshift occurred ... check cabling!` en `journalctl -k`), así que ese número ya está prácticamente en el techo del enlace (~11.5 MiB/s). Con un buen cable Cat5e/Cat6 el enlace queda en Gigabit y el límite pasa a ser el payload. La estimación, aún no medida, es de 10–30 MiB/s. El recorrido del heap baja de ~4 min a ~45 s a 100 Mbps, y debería quedar por debajo de eso en Gigabit.

> ⚠️ Durante un benchmark con 8 conexiones paralelas y 64 MiB por el cable, el PS4 tuvo un kernel panic (pantalla negra, hubo que reiniciar). Con 1 conexión funcionó sin problemas. La causa no está confirmada. Hasta entonces, usa 1 conexión (el valor por defecto de `bench_read.py`) y no paralelices las lecturas.

Si NetworkManager comparte la conexión y aun así el PS4 no obtiene IP, o si se detiene en el error **NW-31456-9** después de la pantalla del proxy, revisa el firewall:
- **ufw activo:** bloquea el DHCP y el DNS que vienen del cable. Permítelos con `sudo ufw allow in on enp3s0` y `sudo ufw route allow in on enp3s0 out on wlp4s0`.
- **Error al final de la prueba de internet:** la red local ya funciona. Sal de la pantalla, porque la configuración con cable queda guardada. Si el PS4 necesita internet y Docker está instalado, la política de `FORWARD` en `DROP` puede bloquear el NAT. Permítelo con `sudo iptables -I DOCKER-USER -i enp3s0 -o wlp4s0 -j ACCEPT` y la regla de vuelta (`-i wlp4s0 -o enp3s0 -m state --state RELATED,ESTABLISHED -j ACCEPT`).
- **Después de cambiar de red, ps4debug puede haberse caído:** vuelve a cargarlo desde GoldHEN → Payloads.

**Otras opciones:**
- **USB no sirve para esto.** Los puertos USB del PS4 solo funcionan como host: aceptan pendrive, mando y disco duro, pero no crean una red con el PC. Un pendrive solo ayuda a mover archivos grandes, no a leer memoria en vivo.
- **Wi-Fi de 5 GHz,** si tu modelo de PS4 lo soporta (el Slim y el Pro sí; el modelo original, CUH-10xx/11xx, solo tiene 2.4 GHz). Conecta el PS4 a la red de 5 GHz del router y mide. La mejora suele ser mucho menor que la del cable.
- **Acerca el PS4 al router** o quita obstáculos. En 2.4 GHz el PS4 es el lado débil del enlace.

### Leer menos memoria

En la práctica, esto ayuda tanto como la red:
- **Restringe la región.** En `search_memory`, usa `regions` (`writable`, `code` o parte del nombre). En recomps, busca solo en el heap del juego guest. En el recomp de Super Mario Wonder, por ejemplo, está en `0x5000000000` (~460 MiB) y no en los 3.5 GiB escribibles del proceso.
- **Refina en vez de volver a recorrer.** Para un número (monedas, vida), usa `scan_value` y después `rescan`. Los candidatos quedan en el servidor MCP y Claude recibe solo el conteo. Cambia el valor en el juego y llama a `rescan` con el valor nuevo (o `increased`/`decreased`/`changed`/`unchanged`): relee solo las direcciones candidatas, no la región entera. Con 20 candidatos o menos, la respuesta lista dirección y valor. En la prueba de Super Mario Wonder, esto pasó de 430 mil candidatos a 13.
- **Combina valores vecinos.** Buscar dos números juntos (por ejemplo, monedas `38` + monedas moradas `158`) encuentra la estructura en una sola pasada.
- **Haz el dump una vez y trabaja fuera de la consola.** `dump_module` genera el ELF una vez. Después, el análisis en ReVa/Ghidra ya no usa la red.

### Sesiones de debug

- **No dejes el juego adjuntado y detenido mucho tiempo.** En la prueba, Super Mario Wonder se quedó congelado varios minutos (bug del attach, ya corregido) y crasheó en el detach. Para inspeccionar, usa `pause` → lectura → `resume`, en ciclos cortos.
- **Después de reiniciar la consola, vuelve a cargar el payload.** Si está en `/data/payloads/` en el PS4, cárgalo desde GoldHEN → Payloads, sin necesitar el BinLoader.
- **FW 12.52 con ps4debug 1.3 (GoldHEN v1.1.19):** el ATTACH dejaba el proceso detenido y el módulo principal se llama `executable`. El cliente ya maneja los dos casos: `"eboot.bin+0x…"` sigue funcionando. Detalles del attach en el ítem 14 de [`docs/protocolo-ps4debug.md`](docs/protocolo-ps4debug.md) (en portugués).

## Configuración (`.mcp.json` → env)

| Variable | Por defecto | Uso |
|---|---|---|
| `PS4_IP` | `auto` | IP de la consola. Vacío o `auto`: se descubre al iniciar (DDP en el puerto 987; sin respuesta, recorre la /24 del PC buscando el puerto 744). Solo se aceptan IPs privadas, salvo `PS4_ALLOW_PUBLIC_IP=1` |
| `PS4DEBUG_PORT` / `PS4DEBUG_CALLBACK_PORT` | 744 / 755 | puertos de ps4debug (el 755 está fijo en el payload) |
| `PS4_FTP_PORT` | 2121 | FTP de GoldHEN |
| `PS4_LOG_SOURCE` | `tcp:<PS4_IP>:3232` | `tcp:IP:PUERTO`, `udp:BIND:PUERTO`, `file:/ruta` u `off` |
| `PS4_MAX_READ` / `PS4_INLINE_HEX_MAX` | 65536 / 1024 | límite de lectura y cuánto se muestra como hexdump inline (por encima: archivo + resumen) |
| `PS4_MAX_BREAKPOINTS` / `PS4_MAX_WAIT_S` | 10 / 300 | límites de la sección 8 |
| `PS4_ENABLE_WRITE` | 0 | habilita `write_memory`, `kill_process` y `kernel_read`, siempre con confirmación manual |
| `PS4_INGAME_SCAN` | 0 | ejecuta `scan_value` dentro del juego vía el RPC de ps4debug (primer recorrido mucho más rápido; por la red solo vuelve la lista de direcciones). Desactivado hasta validarlo en una consola. Nunca se usa con el proceso adjuntado |
| `PS4_ENABLE_DEPLOY` | 0 | habilita `build_homebrew` e `install_pkg` (instala vía Remote Package Installer, puerto 12800) |
| `PS4_BRIDGE` | 0 | activa el bridge de la app CCPS4 Agent Viewer |
| `PS4_WORKDIR` | carpeta del proyecto | donde quedan `dumps/`, `downloads/` y `logs/audit.jsonl` |

## Seguridad (sección 8)

- **Red:** solo red local. Las IPs públicas se rechazan y el bridge solo atiende a `PS4_IP`. No hace falta abrir ningún puerto en el router.
- **Escritura:** las herramientas de escritura, kernel y deploy ni siquiera se registran sin la variable correspondiente. Cuando están activas, piden confirmación manual en cada llamada mediante la *elicitation* de MCP: Claude Code te muestra la pregunta para que la respondas.
- **Límites:** tamaño máximo de lectura, número de breakpoints, timeouts, watchpoints alineados y rechazo de breakpoints fuera de código o duplicados.
- **Auditoría:** toda llamada va a `logs/audit.jsonl` (herramienta, argumentos, duración, resultado).
- **Salida del MCP:** si el servidor MCP muere, la conexión "dueña" del ATTACH se cierra. El payload entonces se desadjunta solo y restaura los bytes de los breakpoints.

## Probar sin el PS4

```bash
uv run pytest                                         # todo; guarda en un archivo si tu shell resume la salida
uv run python -m tests.fakes.run_fake --port 7440 --debug-port 7550 --scenario demo
```

El ps4debug falso se escribió a partir del código C del payload, con las mismas peculiaridades. Para hablar con él desde Claude Code, ejecuta el servidor con `PS4_IP=127.0.0.1 PS4DEBUG_PORT=7440 PS4DEBUG_CALLBACK_PORT=7550 PS4DEBUG_CALLBACK_BIND=127.0.0.1`.

## Estructura

```
ps4_mcp/            servidor MCP: server.py (herramientas), client.py/protocol.py (ps4debug), debug.py (sesión
                    y cola de eventos), snapshot.py, ftp.py, logs.py, sfo.py, text.py, elfdump.py, safety.py,
                    bridge.py (app del PS4), deploy.py (OpenOrbis + Remote Package Installer)
tests/              pruebas + fakes/ (ps4debug falso) + crash-homebrew/ (PKG de prueba de la fase 3)
ps4-app/            app CCPS4 Agent Viewer (fase 6)
scripts/            fase0_smoke.py, send_payload.py, bench_read.py, metrics.py, install_openorbis.sh
docs/               protocolo-ps4debug.md, relatorio-fase5.md, kh3-formato-legendas.md (en portugués)
```
