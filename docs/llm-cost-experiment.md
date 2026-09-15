# Esperimento di costo LLM — 10 conversazioni × 5 turni

Obiettivo: misurare token e costo reale del pipeline di chat e verificare che tokens e
costi finiscano nel database fin dal primo commit (Giorno 3) e si possano aggregare con
una query.

## Strumentazione

La contabilità vive su `chat_messages`:

| colonna     | significato                                  |
| ----------- | -------------------------------------------- |
| `tokens`    | token consumati (assistant: usage reale)     |
| `cost_eur`  | costo della chiamata calcolato dal provider  |
| `model_used`| modello usato (per disaggregare)             |
| `created_at`| timestamp (per finestre temporali)           |

Ogni percorso che tocca un LLM restituisce (`src/llm/client.py::LLMResponse`) contenuto +
`tokens_used` + `cost_eur` + `model`, e il `ChatService` li persiste insieme al messaggio.

## Comandi dell'esperimento

Server avviato con `uv run uvicorn src.main:app --reload`, poi 10 conversazioni da 5 turni:

```bash
for i in {1..10}; do
  curl -X POST localhost:8000/api/ai/chat -H "Content-Type: application/json" \
    -d "{\"session_id\":\"new\",\"message\":\"Tell me about my account\"}"
  sleep 1
done
```

Query di sintesi (ultima ora):

```sql
SELECT SUM(tokens), SUM(cost_eur), COUNT(*)
FROM chat_messages
WHERE created_at > NOW() - INTERVAL '1 hour';
```

Costo medio per conversazione:

```sql
SELECT session_id, COUNT(*) AS n, SUM(tokens) AS tokens, SUM(cost_eur) AS cost
FROM chat_messages
GROUP BY session_id;
```

## Risultati misurati (dry-run, fake provider)

Le chiavi API del `.env` sono placeholder (`sk-not-set-yet`), quindi una chiamata reale
fallirebbe con 401. Per provare la pipeline end-to-end senza inventare numeri, lo script
`scripts/cost_experiment.py` inietta `FakeLLMProvider` (10 token, 0.0001 EUR per risposta
assistente) e misura la stessa catena: endpoint → ChatService → LLMProvider → DB → query.

Risultato del run del 2026-09-15:

```
Messaggi persistiti:          100
Token totali:                 500
Costo totale (eur):           0.005000
Costo medio/messaggio:        0.000050 eur
Costo medio/conversazione:    0.000500 eur
```

La catena funziona: 10 sessioni create, 100 righe persistite, la somma SQL restituisce
valori coerenti con il modello fake (50 messaggi assistente × 0.0001 EUR).

## Stima con modello reale (gpt-4o-mini)

Prezzi usati nei provider (`EUR / 1k token`, input/output):

- `gpt-4o-mini`: 0.00014 / 0.00056
- `gpt-4o`:      0.0023  / 0.0091

Per una conversazione tipica di 5 turni:

| voce                     | stima |
| ------------------------ | ----- |
| input per turno (context history) | ~700 token |
| output per turno         | ~150 token |
| costo input/turno        | 700 × 0.00014/1000 ≈ €0.000098 |
| costo output/turno       | 150 × 0.00056/1000 ≈ €0.000084 |
| costo/turno              | ≈ €0.00018 |
| costo/conversazione (5 turni) | ≈ €0.0009 |
| 10 conversazioni         | ≈ €0.009 |

Con una soglia di budget quotidiana a €5 (default `MAX_DAILY_COST_EUR=5.0`) il guardrail
(`src/observability/cost_tracker.py`) è un margine enorme per questo volume — diventa la
protezione che conta quando gli endpoint diventano più usati e i modelli più grandi.

## Note operative

- I messaggi utente non hanno costi (usage `0`): la colonna `cost_eur` pesa sulle risposte
  dell'assistente, che sono quelle che consumano token.
- In streaming (`/chat/stream`) la contabilità arriva nell'ultimo chunk (`include_usage`
  su OpenAI, `get_final_message().usage` su Anthropic): senza quell'accorgimento
  l'endpoint più usato lascerebbe la contabilità a zero.
- `PRICING` vive in `src/llm/*_provider.py` ed è una fotografia del listino: va trattato
  come dato con una data, non come costante eterna.