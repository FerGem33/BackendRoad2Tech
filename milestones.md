# StockCopilot — hitos de implementación concurrente

Este documento divide la arquitectura en entregables integrables. No se trata de asignar áreas aisladas: cada hito deja un contrato verificable que habilita al siguiente. Si el equipo es pequeño, una persona puede tomar varios hitos de la misma fase; si es grande, los hitos dentro de una misma fase se pueden trabajar en paralelo.

## Reglas de colaboración

1. Nadie cambia un contrato compartido sin actualizar este archivo, el esquema correspondiente y sus fixtures en el mismo pull request.
2. Los identificadores son strings estables: `store_id`, `product_id`, `campaign_id`, `draft_id`; fechas ISO `YYYY-MM-DD`; timestamps ISO-8601 UTC; dinero en MXN con dos decimales.
3. El LLM no calcula ni persiste decisiones económicas. Las herramientas y el motor determinista son la fuente de verdad.
4. Una promoción sólo puede pasar a `approved` mediante un endpoint explícito de usuario. El agente sólo crea `draft`.
5. Cada equipo trabaja en una rama con prefijo de hito: `m1-data/...`, `m2-api/...`, `m5-agent/...`. Evitar cambios no necesarios en archivos de otro hito.
6. Todo endpoint y evento debe tener un fixture de éxito, uno de error y validación de esquema antes de integrarse.

## Contratos compartidos — congelados en fase 0

Estos contratos permiten que frontend, agente y motor usen mocks mientras los servicios reales se terminan.

### Entidades mínimas

```text
StoreContext: store_id, as_of, inventory[], in_transit[], alerts[], constraints
Forecast: store_id, product_id, data_as_of, horizon[], p50, p80, p90, drivers[]
StrategyCandidate: strategy_type, store_id, products[], period, discount_pct, constraints, assumptions[]
StrategySimulation: candidate_id, baseline, treated, expected_units_increment,
                    expected_sales_increment, expected_margin_increment,
                    expected_waste_reduction, stockout_risk, confidence_interval,
                    violations[]
PromotionDraft: draft_id, status, strategy, simulation, created_at, requires_approval
Portfolio: store_id, period, strategies[], net_expected_margin_increment, conflicts[]
```

### API inicial

```text
GET  /stores/{store_id}/tendency?product_id=&history_days=&forecast_days=
POST /stores/{store_id}/strategies:generate
POST /stores/{store_id}/strategies:prompt
POST /drafts/{draft_id}/add-to-plan
POST /drafts/{draft_id}/approve
POST /drafts/{draft_id}/reject
GET  /stores/{store_id}/plan?from=&to=
```

Las respuestas deben incluir `request_id`, `data_as_of` y `data`. Los errores usan `code`, `message`, `request_id` y no exponen excepciones internas.

### Estados y eventos

```text
draft -> selected -> approved -> scheduled -> active -> measured -> expired
draft -> rejected
```

Eventos con versión: `stock.updated.v1`, `promotion.draft_created.v1`, `promotion.approved.v1`, `promotion.measured.v1`, `plan.regenerated.v1`. Los productores no suponen qué consumidor los recibirá.

---

## Fase 0 — Base común (secuencial, hito M0)

**Por qué es secuencial:** desbloquea mocks compatibles y evita que varios equipos inventen formatos distintos.

### M0. Contratos, entorno y datos demostrables

**Responsable sugerido:** líder técnico + una persona de datos.

**Entregables**

- Crear `contracts/` con esquemas JSON o OpenAPI de las entidades y endpoints anteriores.
- Crear `fixtures/` con una tienda, tres SKUs, forecast, estrategia viable, estrategia rechazada y borrador.
- Regenerar y validar el dataset con Docker; publicar el diccionario generado de `v2/README.md`.
- Definir una fecha de demo `data_as_of` dentro del histórico y un horizonte de 7/14 días.
- Configurar `.env.example` sin secretos y un comando de desarrollo documentado.

**Gate de integración**

```bash
docker compose build dataset
LOCAL_UID=$(id -u) LOCAL_GID=$(id -g) docker compose run --rm dataset
```

Los esquemas deben validar fixtures y los equipos M1–M3 deben poder desarrollar usando esos fixtures sin servicios AWS.

---

## Fase 1 — Fundaciones paralelas (M1, M2 y M3)

Los tres hitos dependen de M0, pero no entre sí. Usan contratos/fixtures congelados.

### M1. Datos Curated y capa analítica

**Responsable sugerido:** data engineer / ML engineer.

**Construir**

- Ingesta de los CSV a S3 Raw y transformación a S3 Curated particionada por fecha.
- Tablas: ventas, inventario diario, tickets, campañas, calendario/clima, catálogo y proveedores.
- Consultas Athena para tendencia, histórico promocional, afinidad y riesgo de caducidad.
- Pruebas de calidad: llaves foráneas, balance inventario–ventas, nulos, duplicados y separación temporal train/test.

**Contrato que entrega:** adaptadores de sólo lectura equivalentes a `get_historical_insights` y `get_current_store_context`, con fixtures de respuesta M0.

**No modificar:** contratos de API ni la lógica de negocio del motor.

### M2. API operativa, persistencia y aprobación

**Responsable sugerido:** backend engineer.

**Construir**

- API Gateway y handlers/Lambdas para los endpoints M0.
- DynamoDB para stock actual, alertas, borradores, planes, auditoría de aprobación y configuración por tienda.
- Máquina de estados de promociones con transiciones inválidas rechazadas.
- Idempotencia en `approve`, `reject` y eventos operativos; registrar `request_id`.

**Contrato que entrega:** API funcional contra repositorios in-memory o DynamoDB, que devuelva fixtures M0 antes de conectar ML.

**No modificar:** tablas Curated ni cálculos del modelo.

### M3. Dashboard y UX de decisión

**Responsable sugerido:** frontend engineer / diseñador.

**Construir**

- Selector de tienda y periodo; KPIs; serie histórica + P50/P80/P90; alertas y mapa/lista de demanda.
- Vista de hasta cinco estrategias: escenario base vs. tratado, impacto individual y total de portafolio.
- Flujo de prompt, tarjeta de borrador, agregar al plan, aprobar/rechazar y estados visibles.
- Cliente HTTP tipado generado desde OpenAPI o validado contra fixtures.

**Contrato que entrega:** interfaz totalmente navegable con datos fixture y sin cálculos financieros en frontend.

**No modificar:** formatos de respuesta ni archivos de infraestructura de otros equipos.

**Gate de fase 1:** una demo local puede mostrar datos, una estrategia viable y una rechazada usando exclusivamente contratos M0.

---

## Fase 2 — Inteligencia de decisión (M4, M5 y M6)

M4 y M5 pueden comenzar con fixtures tras M0; M6 requiere que M2 exponga persistencia de borradores. La integración final de los tres requiere M1.

### M4. Forecast y modelos analíticos

**Responsable sugerido:** ML engineer.

**Construir**

- Features `fecha × tienda × SKU`: lags, calendario, clima, zona, stock, promociones conocidas y lead time.
- Baseline temporal y CatBoost/LightGBM probabilístico P50/P80/P90; backtesting por ventanas temporales.
- Afinidad a partir de `ticket_lines`; separar reglas de negocio de afinidad observada.
- Uplift inicial con campañas treatment/control; reportar incertidumbre y no entrenar con variables posteriores al tratamiento.
- Endpoint/adaptador SageMaker que responde exactamente `Forecast`.

**Gate:** métricas de backtest registradas y una inferencia reproducible para el `data_as_of` de demo.

### M5. Motor determinista y optimizador

**Responsable sugerido:** backend/operations-research engineer.

**Construir**

- Generador de candidatas únicamente desde plantillas permitidas: afinidad, rescate y contextual.
- Simulación de inventario por día: baseline vs. tratado, descuento, costo, merma, stockout y canibalización.
- Validador de margen mínimo, inventario, fechas, vida útil, descuento máximo, presupuesto y conflictos.
- Optimizador de portafolio; calcula el impacto conjunto sin sumar tarjetas ciegamente.
- Tests deterministas por caso viable, margen negativo, quiebre, caducidad y conflicto entre estrategias.

**Contrato que entrega:** `simulate_strategy`, `optimize_portfolio` y respuestas `StrategySimulation`/`Portfolio` de M0.

**Regla:** el motor no llama al LLM.

### M6. Copilot Orchestrator — LangGraph + Bedrock

**Responsable sugerido:** AI/backend engineer.

**Construir**

- Grafo: `intent -> context -> forecast_insights -> candidates -> simulate -> validate -> optimize -> response -> approval_wait -> persist`.
- LangChain `ChatBedrockConverse` para interpretación y explicación; salida JSON validada contra esquema.
- Herramientas con permisos mínimos: tendencia, contexto, forecast, insights, restricciones, simulación, optimización y borrador.
- Respuestas para `get_product_tendency` y `get_product_sell_strategy` que muestran números provenientes de herramientas.
- Checkpoints por `conversation_id`/`draft_id`; reintentos idempotentes; protección contra prompt injection en parámetros de herramientas.

**Contrato que entrega:** el orquestador puede operar primero sobre mocks M0/M5 y después intercambiar adaptadores reales sin cambiar frontend.

**Regla:** no usar Bedrock Agents Classic. Para AWS, desplegar el contenedor LangGraph ARM64 en AgentCore Runtime con protocolo HTTP (`/ping`, `/invocations`), autenticación y rol IAM mínimo.

**Gate de fase 2:** desde una petición en lenguaje natural se crea un borrador trazable, con baseline/tratado, restricciones y `requires_approval: true`.

---

## Fase 3 — Integración operacional (M7 y M8)

Estos hitos requieren M1, M2, M4 y M5. M7 y M8 pueden desarrollarse en paralelo cuando los contratos ya respondan datos reales.

### M7. Automatización y ciclo de aprendizaje

**Responsable sugerido:** backend/cloud engineer.

**Construir**

- EventBridge para `stock.updated.v1`, generación diaria de planes, alertas y reentrenamiento semanal.
- Flujo de medición al terminar una promoción: ventas, margen, merma, quiebres, aceptación y grupo control.
- Persistencia Raw/Curated de resultados y actualización de datasets de evaluación.
- Reintentos, DLQ y observabilidad de eventos.

**Gate:** una aprobación genera evento, cambia estado de promoción y deja una medición consultable.

### M8. Integración de experiencia y seguridad

**Responsable sugerido:** frontend + backend + cloud.

**Construir**

- Conectar dashboard M3 a endpoints reales; sustituir fixtures gradualmente por feature flags.
- Autenticación/autorización por rol; el rol de encargado sólo aprueba para sus tiendas.
- Telemetría: `request_id`, latencia, errores de herramientas, calidad de forecast y resultado de estrategias.
- Validación de JSON del agente, rate limiting y sanitización de mensajes.

**Gate:** recorrido completo: consulta de tendencia → estrategia por prompt → simulación → borrador → aprobación → plan actualizado.

---

## Fase 4 — Cierre de hackathon (M9, secuencial)

### M9. Ensayo, evidencia y demo

**Responsable sugerido:** todo el equipo, con un dueño de integración.

1. Ejecutar la regeneración Docker y todas las validaciones de datos.
2. Ejecutar pruebas de contratos, motor y API; congelar versiones de modelos/dataset.
3. Preparar dos narrativas: evitar quiebre y rescatar merma/aumentar ticket.
4. Mostrar una estrategia rechazada y explicar la restricción: esto prueba control, no sólo generación.
5. Mostrar el impacto del portafolio, no sólo de una tarjeta.
6. Documentar límites: datos sintéticos, incertidumbre del uplift y aprobación humana obligatoria.

**Criterio de terminado:** la demo funciona con una sola configuración reproducible, cada cifra tiene una fuente trazable y ninguna promoción puede activarse sin aprobación.

## Matriz rápida de dependencias

```text
M0
├── M1 Datos Curated ──────────────┐
├── M2 API/Dynamo ───────┐         │
├── M3 Dashboard ────────┼─────┐   │
├── M4 Forecast ─────────┼─────┼───┤
├── M5 Motor/optimizador ─┼─────┼───┤
└── M6 LangGraph ────────┘     │   │
                              M7  M8
                               └──┬──┘
                                  M9
```

M3 puede enseñar la experiencia desde fase 1; M4/M5/M6 la vuelven inteligente; M7/M8 la conectan al ciclo operativo. El dueño de integración debe revisar los contratos al cierre de cada fase antes de abrir la siguiente.
