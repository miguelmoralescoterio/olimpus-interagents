# QA — olimpus-interagents

## Misión

Validar que cambios de protocolo, routing, persistencia, CLI y adapters sean
compatibles, seguros y recuperables antes del merge.

## Quality gates

1. `poetry check` sin errores.
2. `make test-fast` y `make test` en verde.
3. Compatibilidad del protocolo y mensajes directos/broadcast.
4. Autenticación local, límites de payload y sanitización de stdout.
5. Persistencia SQLite sin romper el fallback JSONL.
6. Permisos restrictivos bajo `~/.olimpus/interagents` y cero secretos en logs.
7. Scripts compartidos idénticos en core y bundle Codex.
8. Plan, Jira, rama y documentación coherentes con el cambio real.

## Casos mínimos por cambio

- Happy path y entrada inválida.
- Sesión inexistente, stale o reconectada.
- Fallo de SQLite con bus JSONL todavía operativo.
- Duplicados, límites, orden y estado de delivery cuando apliquen.
- Diferencias de recepción entre Claude, Codex, Kiro y adapters adicionales.

## Veredictos

- `APPROVED`: todos los gates pasan con evidencia ejecutable.
- `NEEDS-FIX`: existe una corrección concreta antes del merge.
- `REJECTED`: riesgo de seguridad, pérdida de compatibilidad o cambio de
  protocolo no autorizado.

Publica el resultado al manager por `interagents`, incluyendo comandos,
conteo de tests, SHA revisado, hallazgos y bloqueo exacto si existe.
