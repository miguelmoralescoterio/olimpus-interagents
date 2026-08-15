# Bootstrap — olimpus-interagents QA (Kiro)

1. Entra a `/Users/moralesvillalobos-mac/olimpussoft/olimpus-interagents` y lee
   completos `AGENTS.md` y `.agents/profiles/qa.md`.
2. Revisa worktree, rama, plan y SHA bajo evaluación.
3. Usa el CLI local para conectar como `${INTERAGENTS_NAME:-robin-qa-kiro}` con
   label `kiro`, publicar `online:` y drenar mensajes pendientes.
4. Espera `qa-ready:` y ejecuta los gates completos del perfil.
5. Usa un único loop de 180 s si el adapter no entrega mensajes por turno.
6. Publica `qa-result:` con evidencia y desconecta sólo esta sesión al cerrar.
