# Async vs sync — esperimento

> Misura locale: query su PostgreSQL 16 in Docker (porta 5433), Windows 10, Python 3.12.
> `asyncio.gather` lancia le coroutine in parallelo; il tempo sequenziale e' la stima
> dell'equivalente "una alla volta".

## Comando eseguito

```
uv run python scripts/async_vs_sync.py
```

## Scenario A — micro-query (`SELECT 1`, 100 query)

| Modalità                          | Tempo osservato |
|-----------------------------------|-----------------|
| Sequenziale (`for` + await)       | 0.1840 s  |
| Parallelo (`asyncio.gather`)      | 2.7843 s  |
| Speedup                           | 0.1x |

Su query banali e in locale il guadagno è minimo (a volte negativo): il costo è dominato
dalla creazione contestuale di molte connessioni e dall'overhead di scheduling, non dall'attesa I/O.

## Scenario B — query con attesa reale (`SELECT pg_sleep(0.05)`, 50 query)

| Modalità                          | Tempo osservato |
|-----------------------------------|-----------------|
| Sequenziale (`for` + await)       | 2.8677 s  |
| Parallelo (`asyncio.gather`)      | 0.1101 s  |
| Speedup                           | 26.1x |

## Conclusione

Il parallelismo paga quando ogni chiamata **attende davvero** (I/O lento): con 50 query
da 50 ms lato server l'async le sovrappone e chiude in ~0.110s invece di
~2.868s (26x). Con micro-query locali il guadagno sparisce perchè
non c'è attesa da nascondere. È il modello giusto per LLM/API esterne (G4), dove ogni
chiamata impiega centinaia di ms e un server sincrono bloccherebbe un thread per chiamata.
