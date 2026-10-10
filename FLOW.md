# StockCopilot — flujo funcional

## Dos modos de uso

### 1. Plan comercial automático

La interfaz carga un plan por tienda y periodo (por defecto, próximos siete días). Muestra hasta cinco estrategias elegibles y priorizadas por impacto neto esperado.

No se deben forzar cinco promociones rentables: si sólo existen tres oportunidades que cumplen las reglas, se muestran tres y se explica por qué.

Cada tarjeta debe contener:

```text
tipo de estrategia
productos y tienda objetivo
periodo de vigencia
acción/precio o descuento propuesto
ventas incrementales esperadas
margen incremental esperado
merma evitada (si aplica)
riesgo de quiebre
intervalo de incertidumbre / confianza
explicación y restricciones consideradas
```

### 2. Estrategia bajo demanda con agente

El encargado puede pedir algo específico, por ejemplo:

> “Quiero aumentar las ventas de bebidas energéticas este sábado sin dar más de 10% de descuento.”

El agente transforma la petición en un objetivo y restricciones, consulta datos actuales/históricos y forecast, genera alternativas dentro de esos límites y las manda a simulación. La nueva estrategia se muestra como borrador y puede agregarse al plan.

## Flujo de datos operativos

```text
Venta / recepción / ajuste de inventario
  -> evento operativo
  -> actualización de stock actual
  -> persistencia en histórico
  -> reevaluación si el evento cambia una decisión relevante
```

Eventos que ameritan reevaluar recomendaciones:

- inventario bajo o recepción de mercancía;
- producto próximo a caducar;
- pedido retrasado o incompleto;
- venta atípicamente alta;
- cambio relevante en clima/evento;
- inicio/final de promoción;
- solicitud explícita del encargado.

No se reentrena el modelo después de cada venta. Las ventas actualizan el contexto actual; el reentrenamiento se ejecuta de forma periódica.

## Generación del plan automático

```text
1. Cargar contexto actual de la tienda.
2. Obtener forecast base sin estrategia para el horizonte.
3. Detectar oportunidades: exceso, merma, afinidad, evento, crecimiento/caída de demanda.
4. Crear candidatas de estrategia mediante plantillas válidas.
5. Simular el impacto incremental de cada candidata.
6. Rechazar candidatas no viables.
7. Optimizar el portafolio de estrategias compatibles.
8. Guardar y presentar las estrategias priorizadas.
```

### Plantillas iniciales

| Plantilla | Objetivo | Ejemplo |
| --- | --- | --- |
| Combo de afinidad | Aumentar artículos por ticket | Café + panqué; cerveza + botana |
| Combo de rescate | Evitar merma y recuperar margen | Producto popular + producto que vence pronto |
| Promoción contextual | Aumentar volumen según contexto | Bebida fría por ola de calor; botana previo a partido |

## Simulación de impacto

Para cada estrategia se compara el escenario base contra el escenario tratado:

```text
impacto incremental = resultado con estrategia - resultado sin estrategia
```

El cálculo debe estimar, como mínimo:

```text
unidades adicionales
ingresos incrementales
margen incremental
costo de descuento / subsidio
merma evitada
costo esperado de quiebre adicional
```

Una estrategia es elegible sólo si cumple las reglas de inventario y negocio:

- margen neto positivo o justificación explícita de objetivo;
- inventario disponible y en tránsito suficiente;
- no promover SKU con riesgo alto de quiebre;
- respetar vida útil, descuento máximo, presupuesto y fechas;
- respetar la capacidad de operación de la tienda;
- no ser incompatible con otra oferta activa.

## Optimización del portafolio

El total mostrado al usuario debe recalcularse cada vez que se selecciona, quita o agrega una estrategia. No se deben sumar impactos individuales sin validar conflictos.

Ejemplos de conflicto:

- dos estrategias usan el mismo SKU, tienda y horario;
- dos ofertas consumen más producto del disponible;
- un descuento canibaliza un combo más rentable;
- una oferta de rescate compite con una promoción contextual.

El optimizador maximiza el margen incremental esperado sujeto a las restricciones anteriores. Se recomienda una restricción de diversidad para evitar que el plan tenga cinco estrategias sobre el mismo producto o categoría.

## Copilot Orchestrator y rol del LLM

LangGraph controla el estado de la solicitud, aprobaciones, reintentos y continuación de borradores. LangChain conecta el grafo con Amazon Bedrock mediante `ChatBedrockConverse`. El LLM interpreta la intención y explica resultados, pero el forecast, los riesgos y todo impacto económico los calculan SageMaker y el motor determinista.

El Copilot tiene acceso únicamente mediante herramientas seguras:

```text
get_current_store_context(store_id)
get_demand_forecast(store_id, horizon)
get_historical_insights(store_id, filters)
get_business_constraints(store_id)
simulate_strategy(strategy)
optimize_portfolio(candidates)
create_promotion_draft(strategy)
```

El recorrido de una petición es:

```text
intención -> contexto -> forecast/insights -> candidatas
-> simulación -> validación -> optimización -> respuesta JSON
-> aprobación humana -> guardar borrador o promoción aprobada
```

El Copilot puede proponer una estrategia estructurada, pero no la declara viable por sí mismo. Siempre debe pasar por `simulate_strategy` y por el optimizador cuando afecta el plan actual.

Dos fachadas de producto evitan que el frontend conozca las herramientas internas:

- `get_product_tendency`: entrega serie histórica, forecast P50/P80/P90, tendencia y riesgos; Bedrock sólo genera el resumen.
- `get_product_sell_strategy`: compone contexto, forecast, historial, candidatas, simulación, validación y borrador; compara siempre escenario base contra escenario tratado.

## Aprobación y aprendizaje

```text
GENERADA -> SIMULADA -> EN BORRADOR -> AGREGADA AL PLAN
    -> APROBADA / RECHAZADA -> ACTIVA -> MEDIDA -> APRENDIZAJE
```

Tras una estrategia se almacenan resultados reales: ventas, margen, unidades de combo, merma, stockouts, aprobación/rechazo y contexto. Estos resultados alimentan la evaluación y los futuros ciclos de entrenamiento.

## Cadencias sugeridas

| Proceso | Cadencia |
| --- | --- |
| Actualización de stock | Por evento |
| Registro histórico | Por evento |
| Revisión de alertas | Por evento relevante o cada hora |
| Forecast operativo | Diario; horario en demo si aplica |
| Generar plan base | Diario o bajo demanda |
| Reentrenar modelos | Semanal |
| Evaluar promociones | Diario/semanal |

