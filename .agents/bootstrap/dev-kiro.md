# Bootstrap — olimpus-interagents dev (Kiro)

1. Entra a `/Users/moralesvillalobos-mac/olimpussoft/olimpus-interagents` y lee
   completos `AGENTS.md` y `.agents/profiles/dev.md`.
2. Revisa el worktree, rama, commits y planes activos sin modificar nada.
3. Usa `skills/interagents/bin/interagents.py` para instalar dependencias,
   conectar como `${INTERAGENTS_NAME:-luffy-kiro}` con label `kiro`, publicar
   `online:` para `repo=olimpus-interagents` y drenar el backlog.
4. Valida Jira/rama/plan antes de codear y preserva cambios ajenos.
5. Si Kiro no entrega mensajes en cada turno, ejecuta un único loop de 180 s.
6. Gates: `poetry check`, `make test-fast`, `make test` y espejo core/plugin.
7. Reporta checkpoint real y desconecta sólo esta sesión al cerrar.
