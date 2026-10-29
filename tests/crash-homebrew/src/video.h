/* Vídeo mínimo do CCPS4 Crash Test: só uma barra de progresso, sem fonte.
 * Serve para a pessoa no sofá ver que o app está vivo e quando o crash vem. */
#ifndef CCPS4_CRASHTEST_VIDEO_H
#define CCPS4_CRASHTEST_VIDEO_H

/* Abre o VideoOut e aloca 2 framebuffers 1920x1080. Retorna 0 se deu certo. */
int video_init(void);

/* Desenha a barra com `tick` de `total` segmentos preenchidos e faz o flip.
 * tick < 0 desenha a tela vermelha de "crash agora". */
void video_draw_progress(int tick, int total);

#endif
