# Bootstrap — olimpus-interagents QA (Codex)

Repo: `/Users/moralesvillalobos-mac/olimpussoft/olimpus-interagents`.

1. Lee completos `AGENTS.md` y `.agents/profiles/qa.md`.
2. Revisa `git status --short`, rama, plan activo y PR/commit que se evaluará.
3. Conecta usando el CLI local:

```bash
cd /Users/moralesvillalobos-mac/olimpussoft/olimpus-interagents
INTERAGENTS_CLI="$PWD/skills/interagents/bin/interagents.py"
python3 "$INTERAGENTS_CLI" install-deps
NAME="${INTERAGENTS_NAME:-robin-qa-codex}"
python3 "$INTERAGENTS_CLI" connect --name "$NAME" --label codex --daemon
python3 "$INTERAGENTS_CLI" broadcast \
  "online: $NAME | repo=olimpus-interagents | rol=qa | cliente=codex"
python3 "$INTERAGENTS_CLI" drain --limit 1000
```

4. Espera un `qa-ready:` concreto; no apruebes sin SHA y alcance verificables.
5. Ejecuta los gates del perfil, incluyendo fallback JSONL y espejo del plugin.
6. Sondea el bus cada 180 segundos con un único `loop` si no existe hook fiable.
7. Publica `qa-result: APPROVED|NEEDS-FIX|REJECTED` con evidencia y desconecta
   sólo esta sesión al cerrar.
