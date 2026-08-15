# `.agents/` — olimpus-interagents

Perfiles y bootstraps para desarrollar y validar el bus local que coordina
sesiones Claude, Codex, Kiro y otros clientes compatibles.

## Roles

| Rol | Perfil | Enfoque |
| --- | --- | --- |
| dev | `profiles/dev.md` | Protocolo, routing, persistencia, adapters y CLI |
| qa | `profiles/qa.md` | Compatibilidad, seguridad local, fallback y regresión |

## Arranque manual

Desde `/Users/moralesvillalobos-mac/olimpussoft/olimpus-interagents`, inicia el
cliente y pídele leer el bootstrap correspondiente bajo `.agents/bootstrap/`.
Los bootstraps usan el CLI del propio repositorio, no una copia cacheada.

Ejemplo para Codex dev:

```text
Lee y ejecuta el bootstrap completo: .agents/bootstrap/dev-codex.md
```

Toda comunicación entre agentes usa `interagents`. Las instrucciones de
ingeniería del repositorio viven en `AGENTS.md`; el flujo coordinado y la
selección de nombres están en el repo `manager`.

## Gates locales

```bash
poetry check
make test-fast
make test
```

Mantén sincronizados `skills/interagents/bin/` y la copia distribuible bajo
`plugins/interagents-codex/skills/interagents/bin/`. JSONL sigue siendo el
fallback de compatibilidad durante la migración a SQLite.
