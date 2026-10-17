# pyme-backoffice-ar — Spanish-language SME back-office RL environment (HUD v6)

An agent works as the administrative clerk of **Distribuidora del Valle**, a wholesale distributor in
Patagonia, Argentina. It operates the company's management system only through MCP tools (no SQL, no
access to internal state), follows a written procedures manual in Spanish, and is graded on the final
state of the books.

**Why this environment**

- **Spanish, Latin American business workflows.** Invoice letters (Factura A/B by VAT status), credit
  notes, FIFO payment allocation, credit limits, stock corrections, price lists, sales commissions.
- **Not memorizable.** Each task builds a fresh company from a seed (26 customers, 34 products and
  about 70 days of consistent history: invoices, payments, stock movements), then injects the situation
  to solve. 8 families × 12 seeds = 96 tasks; more seeds are free.
- **Multi-step by construction.** Every task needs reading the manual, looking up customers, accounts,
  invoices or stock movements, and then acting, often with a written explanation.
- **Graders checked for reward hacking.** State-based graders compare the agent's final books with an
  oracle-computed expected state and penalize any change that wasn't asked for. 315 tests document it.

## Task families

| Family | What the agent must do | Main traps |
|---|---|---|
| `facturar_pendientes` | Invoice today's pending orders of one zone | right invoice letter, customer discount (not on "Ofertas"), skip customer with overdue debt, skip order without stock, ignore other zones, explain each skipped order |
| `imputar_transferencias` | Register bank transfers as payments and allocate them | identify customers by CUIT, FIFO allocation, leave surplus unallocated, do not register an unknown CUIT, ignore bank fees and outgoing payments |
| `doble_factura` | A customer reports being invoiced twice | find the duplicate, credit note on the *later* invoice with matching letter, also annul its stock movements |
| `stock_negativo` | A product shows negative stock | trace movements, annul the duplicated one, no manual adjustments |
| `lista_precios` | Apply a supplier price increase | round **up** to multiples of $10, skip frozen-price products, touch only the requested categories |
| `devolucion` | Register a customer return | credit note at the original invoice's net unit price, restock only if goods are in good condition, reference the credit note |
| `decision_credito` | Invoice an incoming order only if allowed | overdue debt vs credit limit; refusing needs the correct reason |
| `comisiones` | Compute commissions on collections in a period (read-only) | net-of-VAT base per allocation, date window on the payment date |

## Grading

- Each family returns weighted subscores (HUD `EvaluationResult` / `SubScore`), plus a
  `cambios_no_pedidos` penalty (up to −0.5) for collateral changes: extra documents, prices, payments,
  manual stock moves or annulments the oracle doesn't contain.
- Credit for "not doing" (respecting blocks, not registering the unknown transfer, leaving the
  original invoice alone) only counts when the agent did the actual work.
- Free-text answers are checked per sentence that mentions the order ID, and hedging (naming both
  possible reasons) earns no credit.

## Verifier test results

`tests/test_verificadores.py` (pure Python) + `tests/test_mcp.py` (HUD SDK, Linux): **315 passed**.

- Oracle solution: **1.0** on all 96 tasks.
- Doing nothing, or claiming "done" without doing it: **≤ 0.25**, and 0.0 on 7 of 8 families.
- 16 typical wrong strategies stay below their per-strategy thresholds, between 0.2 and 0.75. Examples:
  invoicing a blocked customer, no discount, wrong letter, LIFO allocation, credit note on the
  original invoice, manual stock adjustment, round-to-nearest, editing frozen prices, return at list
  price, restocking broken goods, always invoicing, commissions on gross.
- End to end: a scripted agent operating only through the MCP tools scores 1.0. Tool errors are
  returned as `ERROR: …` and don't end the session.

## Run

```bash
uv sync
uv run pytest -q tests                   # graders + MCP end-to-end
uv run hud task list --source tasks.py   # 96 tasks
uv run hud eval tasks.py claude          # needs HUD_API_KEY
```

The HUD SDK needs Linux or macOS (it imports `fcntl`). `Dockerfile.hud` packages it for `hud deploy`.
A `CancelledError` traceback printed when a rollout ends comes from the in-process MCP server
shutting down; it is harmless.

## Layout

```
pyme/sistema.py   management system (state, validated write operations, audit log)
pyme/datos.py     seeded company generator + procedures manual (Spanish)
pyme/tareas.py    scenarios, oracles and graders for the 8 families
env.py            HUD v6 Environment: MCP tools + task templates
tasks.py          96 tasks (8 families × seeds 1-12)
tests/            verifier tests and MCP end-to-end tests
```

---

# pyme-backoffice-ar — entorno de RL de back-office de pyme en español (HUD v6)

Un agente trabaja como administrativo de **Distribuidora del Valle**, una mayorista de la Patagonia
argentina. Opera el sistema de gestión solo a través de herramientas MCP (sin SQL ni acceso al estado
interno), sigue un manual de procedimientos en español y se lo corrige por cómo quedan los libros.

**Por qué este entorno**

- **Procesos de negocio latinoamericanos, en español.** Letra de factura (A/B según condición de IVA),
  notas de crédito, imputación FIFO de cobranzas, límites de crédito, correcciones de stock, lista de
  precios, comisiones de vendedores.
- **No se puede memorizar.** Cada tarea arma una empresa nueva desde una semilla: 26 clientes, 34
  productos y unos 70 días de historia coherente (facturas, cobranzas, movimientos de stock). Después
  le agrega la situación a resolver. Son 8 familias × 12 semillas = 96 tareas, y sumar semillas no
  cuesta nada.
- **Varios pasos por diseño.** Toda tarea pide leer el manual, buscar clientes, cuentas, facturas o
  movimientos, y recién después actuar, muchas veces con una explicación escrita.
- **Correctores probados contra trampas.** Comparan los libros finales del agente con el estado
  esperado que calcula un oráculo, y descuentan cualquier cambio que no se pidió. Hay 315 pruebas que
  lo documentan.

## Familias de tareas

| Familia | Qué tiene que hacer el agente | Trampas principales |
|---|---|---|
| `facturar_pendientes` | Facturar los pedidos pendientes de hoy de una zona | letra correcta, descuento del cliente (no en "Ofertas"), saltear al cliente con deuda vencida y el pedido sin stock, ignorar otras zonas, explicar cada pedido salteado |
| `imputar_transferencias` | Cargar las transferencias del banco como cobranzas e imputarlas | identificar al cliente por CUIT, imputación FIFO, dejar el sobrante a cuenta, no registrar un CUIT desconocido, ignorar comisiones bancarias y pagos salientes |
| `doble_factura` | Un cliente avisa que le facturaron dos veces | encontrar la duplicada, nota de crédito sobre la factura *posterior* con la misma letra, anular también sus movimientos de stock |
| `stock_negativo` | Un producto aparece con stock negativo | rastrear los movimientos, anular el duplicado, sin ajustes manuales |
| `lista_precios` | Aplicar un aumento del proveedor | redondear **hacia arriba** al múltiplo de $10, saltear los de precio congelado, tocar solo las categorías pedidas |
| `devolucion` | Registrar una devolución de un cliente | nota de crédito al precio neto unitario de la factura original, reponer stock solo si la mercadería está en buen estado, con referencia a la nota de crédito |
| `decision_credito` | Facturar un pedido solo si corresponde | deuda vencida contra límite de crédito; para rechazar hay que dar el motivo correcto |
| `comisiones` | Calcular comisiones sobre lo cobrado en un período (solo lectura) | base neta de IVA por imputación, período según la fecha de la cobranza |

## Corrección

- Cada familia devuelve puntajes parciales ponderados (`EvaluationResult` / `SubScore` de HUD), más un
  descuento `cambios_no_pedidos` (hasta −0,5) por cambios colaterales: comprobantes, precios,
  cobranzas, movimientos o anulaciones que el oráculo no tiene.
- El crédito por "no hacer" (respetar bloqueos, no registrar la transferencia desconocida, no tocar la
  factura original) solo cuenta si el agente hizo el trabajo de verdad.
- Las respuestas en texto se revisan oración por oración donde aparece el número de pedido. Si el
  agente nombra los dos motivos posibles para cubrirse, no suma.

## Resultados de las pruebas del corrector

`tests/test_verificadores.py` (Python puro) + `tests/test_mcp.py` (SDK de HUD, Linux): **315 pasan**.

- Solución del oráculo: **1,0** en las 96 tareas.
- No hacer nada, o decir "listo" sin hacerlo: **≤ 0,25**, y 0,0 en 7 de las 8 familias.
- 16 estrategias equivocadas típicas quedan por debajo del umbral fijado para cada una (entre 0,2 y
  0,75). Por ejemplo: facturar a un cliente bloqueado, sin descuento o con la letra equivocada,
  imputar LIFO, hacer la nota de crédito sobre la original, ajustar stock a mano, redondear al más
  cercano, tocar precios congelados, devolver a precio de lista, reponer mercadería rota, facturar
  siempre o calcular comisiones sobre el total con IVA.
- De punta a punta: un agente guionado que opera solo con las herramientas MCP saca 1,0. Los errores
  de las herramientas vuelven como `ERROR: …` y no cortan la sesión.

## Cómo correrlo

```bash
uv sync
uv run pytest -q tests                   # correctores + prueba MCP de punta a punta
uv run hud task list --source tasks.py   # 96 tareas
uv run hud eval tasks.py claude          # requiere HUD_API_KEY
```

El SDK de HUD necesita Linux o macOS, porque importa `fcntl`. `Dockerfile.hud` lo empaqueta para
`hud deploy`. Al terminar cada intento puede aparecer un `CancelledError`: viene del cierre del
servidor MCP interno y no afecta nada.

## Estructura

```
pyme/sistema.py   sistema de gestión (estado, escrituras validadas, registro de cambios)
pyme/datos.py     generador de la empresa por semilla + manual de procedimientos
pyme/tareas.py    escenarios, oráculos y correctores de las 8 familias
env.py            Environment de HUD v6: herramientas MCP + plantillas de tareas
tasks.py          96 tareas (8 familias × semillas 1-12)
tests/            pruebas de los correctores y prueba MCP de punta a punta
```
