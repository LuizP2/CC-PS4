# PS4 Agent (CC-PS4)

**English** | [Português (BR)](README.pt-BR.md) | [Español](README.es.md)

Gives Claude Code runtime access to a PS4 so it can investigate port bugs on its own, in a loop: read memory, set breakpoints and watchpoints, receive the event when they fire, read registers, threads, files and logs, and cross-check everything against the binary in Ghidra through ReVa. The plan is in [`ps4-claude-agent-plano.md`](ps4-claude-agent-plano.md) (Portuguese).

The agent loop runs on the PC. The console runs the ps4debug payload and, optionally, the **CCPS4 Agent Viewer** app, which shows what the agent is doing and lets you send messages from the couch.

```
Claude Code ── MCP stdio ──► ps4_mcp (PC) ──► ps4debug :744 / callback :755 (memory, debug)
                                  ├────────► GoldHEN FTP :2121 (files)
                                  ├────────► klog :3232 (logs)
                                  ├────────► HTTP bridge :8765 + UDP :8766 ◄── CCPS4 Agent Viewer app (PS4)
                                  └── dump_module → ELF → ReVa/Ghidra (pseudocode)
```

## Status by phase

| Phase | What exists | Validated without a PS4 |
|---|---|---|
| 0. Primitives | `scripts/fase0_smoke.py` (lists processes, reads 64 bytes, tests FTP/klog), `scripts/send_payload.py` | against the fake ps4debug |
| 1. Read-only MCP | `ps4_status`, `list_processes`, `process_maps`, `read_memory`, `read_pointer_chain`, `read_string`, `resolve_address`, `disassemble` | end-to-end tests over MCP stdio |
| 2. Files and logs | `list_dir`, `read_file`, `download_file`, `tail_logs` (FTP + klog TCP/UDP/file) | real FTP (pyftpdlib), fake klog |
| 3. Debug and events | `attach`/`detach`, `set_breakpoint`/`clear_breakpoint`, `set_watchpoint`/`clear_watchpoint`, `wait_for_event`, `get_registers`, `list_threads`, `step`, `resume`/`pause` + snapshot (section 4.4) + test PKG `tests/crash-homebrew` | fake ps4debug faithful to the C code + cross-check against the `ps4debug` lib from PyPI |
| 4. RE integration | `dump_module` (decrypted memory → ELF with real addresses) + ReVa/Ghidra 12.1.3 installed | ELF imported and decompiled in ReVa |
| 5. Value test | `mark_milestone`, `scripts/metrics.py`, `docs/relatorio-fase5.md`, KH3 pilot tools (`search_memory`, `read_text_region`, `search_files`, `compare_bytes`), `docs/kh3-formato-legendas.md` | tools tested; the value test itself needs the console |
| 6. Optional | `ps4-app/` app (PKG), bridge (`post_status`, `get_messages`, messages arrive in `wait_for_event`), `notify_console`, `build_homebrew`/`install_pkg` | PKG builds, app client compiled and tested on the PC, install against a fake RPI |

Left out, as the plan itself says: per-area subagents for the port (only after the single-agent flow is solid) and an in-game overlay (there is no stable method). To alert whoever is playing, `notify_console` uses the system notification, which shows on top of the game.

## First run with the console

1. **On the PS4:**
   - With GoldHEN, enable the FTP server (port 2121) and the BinLoader (port 9090).
   - Send the ps4debug payload **for your firmware version**: `uv run python scripts/send_payload.py ps4debug.bin --ip <PS4 IP>`.
   - For logs, enable a klog server on port 3232 (or set `PS4_LOG_SOURCE`).
2. **On the PC, once:**
   ```bash
   echo 'export PS4_IP=192.168.0.50' >> ~/.zshrc         # optional: without it the IP is discovered on the network
   sudo sysctl -w net.ipv4.ip_unprivileged_port_start=755  # ps4debug connects back to port 755 on the PC
   echo 'net.ipv4.ip_unprivileged_port_start=755' | sudo tee /etc/sysctl.d/99-ps4debug.conf
   ```
   If you have a firewall, allow TCP 755 from the PS4 (and TCP 8765/UDP 8766 if you use the app).
3. **Validate without AI:** `uv run python scripts/fase0_smoke.py --ftp --logs`. If it ends with OK, phase 0 is ready.
4. **In Claude Code:** restart the session in this folder and approve the `ps4` server from `.mcp.json`. Ask "run ps4_diagnose" and then "which game is running and which modules are loaded?", which is the phase 1 criterion.

> ⚠️ If an ATTACH fails because of port 755 or the firewall, ps4debug gets stuck in "debugging". This is a bug in the payload itself. Restart the console and reload the payload. `ps4_diagnose` exists to avoid this.

## Performance and usage tips

### Network: what limits memory reads

Measured on 2026-10-29 with the PS4 and the PC on 2.4 GHz Wi-Fi (PC with a 300 Mbit/s link and −39 dBm signal):

| Test | Speed |
|---|---|
| ps4debug, 1 connection, 1 MiB blocks | 1.8–3.3 MiB/s |
| ps4debug, 8 parallel connections | 2.2 MiB/s |
| GoldHEN FTP | 1.2–1.8 MiB/s |

ps4debug and FTP are different code on the console and hit the same ceiling. More connections don't help either. The limit is the PS4's Wi-Fi, not the software. Parallel or async requests just split the same link. Scanning the ~460 MiB heap of a game takes about 4 minutes this way.

To measure your network: `uv run python scripts/bench_read.py`.

**Direct cable PC ↔ PS4, neither depending on the router.** This is the best option when you can't run a cable to the router. The PC stays online over Wi-Fi and shares the connection with the PS4 over the cable:

1. Connect a regular network cable between the PS4 and the PC. No crossover cable needed: both ports auto-detect.
2. On the PC (NetworkManager), share the connection over the wired port. The PC becomes `10.42.0.1` and hands an IP to the PS4 via DHCP:
   ```bash
   nmcli connection add type ethernet ifname enp3s0 con-name ps4-direto ipv4.method shared ipv6.method disabled
   nmcli connection up ps4-direto
   ```
   Replace `enp3s0` with the name of your port (`ip -br link`).
3. On the PS4: Settings → Network → Set Up Internet Connection → **Use a LAN Cable** → Easy. The PS4 turns off Wi-Fi and goes online through the PC.
4. Note the IP the PS4 got (`10.42.0.x`, shown in Network → View Connection Status) and use `export PS4_IP=10.42.0.x`. Set the IP explicitly: auto-discovery may search the Wi-Fi network.
5. ps4debug connects back to `10.42.0.1:755`. If the firewall is on, allow port 755 on that interface too.

**Measured with direct cable (2026-10-29):** **10.1 MiB/s** with one connection, 5× Wi-Fi. The cable used had a bad contact and the link dropped to 100 Mbps (`Downshift occurred ... check cabling!` in `journalctl -k`), so this number is already close to the link ceiling (~11.5 MiB/s). With a good Cat5e/Cat6 cable the link runs at Gigabit and the limit becomes the payload. The estimate, not yet measured, is 10–30 MiB/s. The heap scan drops from ~4 min to ~45 s at 100 Mbps, and should be faster at Gigabit.

> ⚠️ During a benchmark with 8 parallel connections and 64 MiB over the cable, the PS4 had a kernel panic (black screen, needed a restart). With 1 connection it ran fine. The cause is not confirmed. Until then, use 1 connection (the `bench_read.py` default) and don't parallelize reads.

If NetworkManager shares the connection and the PS4 still doesn't get an IP, or if it stops at error **NW-31456-9** after the proxy screen, check the firewall:
- **ufw active:** it blocks DHCP and DNS coming from the cable. Allow them with `sudo ufw allow in on enp3s0` and `sudo ufw route allow in on enp3s0 out on wlp4s0`.
- **Error at the end of the internet test:** the local network already works. Leave the screen, since the cable setup is saved. If the PS4 needs internet and Docker is installed, the `FORWARD` policy set to `DROP` may block NAT. Allow it with `sudo iptables -I DOCKER-USER -i enp3s0 -o wlp4s0 -j ACCEPT` and the return rule (`-i wlp4s0 -o enp3s0 -m state --state RELATED,ESTABLISHED -j ACCEPT`).
- **After switching networks, ps4debug may have died:** reload it from GoldHEN → Payloads.

**Other options:**
- **USB doesn't work for this.** The PS4's USB ports are host-only: they accept flash drives, controllers and HDDs, but don't create a network with the PC. A flash drive only helps to move large files, not to read memory live.
- **5 GHz Wi-Fi,** if your PS4 model supports it (Slim and Pro do; the original model, CUH-10xx/11xx, only has 2.4 GHz). Connect the PS4 to the router's 5 GHz network and measure. The gain is usually much smaller than the cable's.
- **Move the PS4 closer to the router** or remove obstacles. On 2.4 GHz the PS4 is the weak side of the link.

### Read less memory

In practice this helps as much as the network:
- **Narrow the region.** In `search_memory`, use `regions` (`writable`, `code` or part of the name). In recomps, search only the guest game heap. In the Super Mario Wonder recomp, for example, it sits at `0x5000000000` (~460 MiB), not in the process's 3.5 GiB of writable memory.
- **Refine instead of rescanning.** In a value scan (for example, coins), the second pass rereads only the blocks that still have candidates. It went from 430 thousand candidates to 13 while reading much less memory.
- **Combine neighboring values.** Searching for two numbers together (for example, coins `38` + purple coins `158`) finds the structure in a single pass.
- **Dump once and work off the console.** `dump_module` generates the ELF once. After that, analysis in ReVa/Ghidra doesn't use the network anymore.

### Debug sessions

- **Don't leave the game attached and stopped for long.** In testing, Super Mario Wonder stayed frozen for minutes (attach bug, now fixed) and crashed on detach. To inspect, use `pause` → read → `resume`, in short cycles.
- **After restarting the console, reload the payload.** If it is in `/data/payloads/` on the PS4, load it from GoldHEN → Payloads, without needing the BinLoader.
- **FW 12.52 with ps4debug 1.3 (GoldHEN v1.1.19):** ATTACH left the process stopped and the main module is named `executable`. The client already handles both cases: `"eboot.bin+0x…"` still works. Attach details in item 14 of [`docs/protocolo-ps4debug.md`](docs/protocolo-ps4debug.md) (Portuguese).

## Configuration (`.mcp.json` → env)

| Variable | Default | Use |
|---|---|---|
| `PS4_IP` | `auto` | Console IP. Empty or `auto`: discovered at startup (DDP on port 987; with no reply, scans the PC's /24 for port 744). Only private IPs are accepted, unless `PS4_ALLOW_PUBLIC_IP=1` |
| `PS4DEBUG_PORT` / `PS4DEBUG_CALLBACK_PORT` | 744 / 755 | ps4debug ports (755 is hardcoded in the payload) |
| `PS4_FTP_PORT` | 2121 | GoldHEN FTP |
| `PS4_LOG_SOURCE` | `tcp:<PS4_IP>:3232` | `tcp:IP:PORT`, `udp:BIND:PORT`, `file:/path` or `off` |
| `PS4_MAX_READ` / `PS4_INLINE_HEX_MAX` | 65536 / 1024 | read limit and how much becomes an inline hexdump (above that: file + summary) |
| `PS4_MAX_BREAKPOINTS` / `PS4_MAX_WAIT_S` | 10 / 300 | section 8 limits |
| `PS4_ENABLE_WRITE` | 0 | enables `write_memory`, `kill_process` and `kernel_read`, always with manual confirmation |
| `PS4_ENABLE_DEPLOY` | 0 | enables `build_homebrew` and `install_pkg` (installs via Remote Package Installer, port 12800) |
| `PS4_BRIDGE` | 0 | turns on the CCPS4 Agent Viewer app bridge |
| `PS4_WORKDIR` | project folder | where `dumps/`, `downloads/` and `logs/audit.jsonl` live |

## Security (section 8)

- **Network:** local network only. Public IPs are refused and the bridge only serves `PS4_IP`. No port needs to be opened on the router.
- **Writes:** write, kernel and deploy tools aren't even registered without the matching variable. When enabled, they ask for manual confirmation on every call through MCP *elicitation*: Claude Code shows you the question to answer.
- **Limits:** max read size, number of breakpoints, timeouts, aligned watchpoints, and refusal of breakpoints outside code or duplicated.
- **Audit:** every call goes to `logs/audit.jsonl` (tool, arguments, duration, result).
- **MCP exit:** if the MCP server dies, the connection that "owns" the ATTACH closes. The payload then detaches on its own and restores the breakpoint bytes.

## Testing without a PS4

```bash
uv run pytest                                         # everything; write to a file if your shell summarizes output
uv run python -m tests.fakes.run_fake --port 7440 --debug-port 7550 --scenario demo
```

The fake ps4debug was written from the payload's C code, with the same quirks. To talk to it from Claude Code, run the server with `PS4_IP=127.0.0.1 PS4DEBUG_PORT=7440 PS4DEBUG_CALLBACK_PORT=7550 PS4DEBUG_CALLBACK_BIND=127.0.0.1`.

## Layout

```
ps4_mcp/            MCP server: server.py (tools), client.py/protocol.py (ps4debug), debug.py (session
                    and event queue), snapshot.py, ftp.py, logs.py, sfo.py, text.py, elfdump.py, safety.py,
                    bridge.py (PS4 app), deploy.py (OpenOrbis + Remote Package Installer)
tests/              tests + fakes/ (fake ps4debug) + crash-homebrew/ (phase 3 test PKG)
ps4-app/            CCPS4 Agent Viewer app (phase 6)
scripts/            fase0_smoke.py, send_payload.py, bench_read.py, metrics.py, install_openorbis.sh
docs/               protocolo-ps4debug.md, relatorio-fase5.md, kh3-formato-legendas.md (Portuguese)
```
