# Bootstrap — olimpus-interagents dev (Claude)

1. Entra a `/Users/moralesvillalobos-mac/olimpussoft/olimpus-interagents` y lee
   completos `AGENTS.md`, `.agents/profiles/dev.md` y las reglas globales allí
   referenciadas.
2. Revisa `git status --short`, rama, últimos cinco commits y planes activos.
3. Conecta al bus con el CLI del repo:

```bash
INTERAGENTS_CLI="$PWD/skills/interagents/bin/interagents.py"
python3 "$INTERAGENTS_CLI" install-deps
NAME="${INTERAGENTS_NAME:-luffy-claude}"
python3 "$INTERAGENTS_CLI" connect --name "$NAME" --label claude --daemon
python3 "$INTERAGENTS_CLI" broadcast \
  "online: $NAME | repo=olimpus-interagents | rol=dev | cliente=claude"
python3 "$INTERAGENTS_CLI" drain --limit 1000
```

4. Atiende las instrucciones del manager, valida Jira/rama/plan y preserva el
   worktree existente. Nunca push directo a `develop` o `main`.
5. Gates: `poetry check`, `make test-fast`, `make test` y espejo core/plugin.
6. Reporta checkpoint real y desconecta sólo esta sesión al cerrar.
