# Bootstrap — olimpus-interagents QA (Claude)

1. Entra a `/Users/moralesvillalobos-mac/olimpussoft/olimpus-interagents` y lee
   `AGENTS.md` y `.agents/profiles/qa.md` completos.
2. Revisa worktree, rama, plan y SHA bajo evaluación.
3. Conecta con el CLI local como `${INTERAGENTS_NAME:-robin-qa-claude}`, label
   `claude`, publica `online:` y ejecuta `drain --limit 1000`.
4. Espera un `qa-ready:` concreto y ejecuta todos los gates del perfil, con
   énfasis en protocolo, seguridad, fallback JSONL y espejo del plugin.
5. Publica un `qa-result:` respaldado por comandos y resultados reales.
6. Desconecta únicamente esta sesión al cerrar.
