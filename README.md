# StockCopilot — contexto del proyecto

## Propósito

StockCopilot es un copiloto comercial y operativo para una cadena de tiendas de conveniencia. Convierte ventas, inventario, contexto externo y comportamiento histórico en decisiones que el encargado de tienda puede entender, simular y aprobar.

La propuesta no se limita a predecir demanda. Busca responder **qué acción concreta conviene tomar ahora** para proteger disponibilidad, elevar el ticket y maximizar el margen esperado.

> Nombre de trabajo: **StockCopilot**. La cadena y los datos de demostración son ficticios; no se debe usar la marca de una cadena real en el producto final sin autorización.

## Problema que resuelve

Una tienda de conveniencia enfrenta simultáneamente tres problemas:

1. **Quiebres de stock:** se pierden ventas cuando no hay producto disponible.
2. **Merma y sobreinventario:** productos de baja rotación o perecederos pueden caducar.
3. **Oportunidades comerciales desaprovechadas:** no se aprovechan combinaciones, eventos o contexto local para aumentar el ticket y margen.

## Propuesta de valor

Cada tienda inicia con un plan de hasta cinco estrategias comerciales simuladas y priorizadas. El encargado puede revisar su impacto económico, agregar o quitar estrategias, pedir alternativas específicas al agente y aprobar sólo las que desea ejecutar.

Las recomendaciones pueden incluir:

- reabasto priorizado por margen en riesgo;
- **combo de afinidad** para elevar artículos por ticket;
- **combo de rescate de merma**, que une un producto ancla de alta demanda con uno que corre riesgo de quedarse o caducar;
- promociones contextuales (por clima, evento, horario o temporada), sólo cuando son rentables y viables con el inventario disponible.

## Principios de diseño

- El modelo de ML y el optimizador producen números; el LLM los interpreta y explica.
- Ninguna oferta se recomienda si provoca un riesgo inaceptable de quiebre, incumple margen mínimo o excede inventario.
- El impacto se mide como **incremental**: resultado esperado con la estrategia menos el escenario base sin estrategia.
- El impacto mostrado arriba del plan es el del **portafolio completo**, no una suma ingenua de tarjetas incompatibles.
- El encargado mantiene la aprobación final; la IA crea borradores y recomendaciones, no activa promociones por sí sola.

## Indicadores principales

- ventas y margen incremental esperado;
- ticket promedio y artículos por ticket;
- unidades vendidas por cross-sell / combos;
- merma evitada y margen recuperado;
- ventas y margen en riesgo por quiebre;
- precisión del forecast;
- aceptación y resultado real de recomendaciones.

## Datos requeridos

La granularidad principal para el forecast es `fecha × tienda × SKU`:

```text
ventas, precio, costo, promoción, inventario inicial/final,
inventario en tránsito, stockout_hours, caducidad, proveedor,
clima, evento, calendario, localización y tipo de tienda
```

Para afinidad y cross-sell se requiere una tabla de tickets:

```text
ticket_id, store_id, timestamp, sku, quantity, unit_price,
discount, promotion_id
```

El repositorio contiene un dataset sintético del hackathon. Se puede ampliar o reemplazar por datos sintéticos propios, siempre manteniendo relaciones causales plausibles: demanda por clima/evento, impacto de promociones, caducidad, merma y restricciones de inventario.

## Enfoque de IA

1. **Forecast probabilístico:** CatBoost o LightGBM predice demanda por tienda-SKU y horizonte usando cuantiles P50/P80/P90.
2. **Afinidad de productos:** análisis de tickets para encontrar productos complementarios y estimar la oportunidad de cross-sell.
3. **Simulador de estrategias:** compara el escenario base contra cada promoción candidata para estimar uplift, margen, merma evitada y riesgo de quiebre.
4. **Optimizador de portafolio:** selecciona estrategias compatibles bajo restricciones de inventario, margen, tiempo y presupuesto.
5. **Agente LLM:** recibe objetivos en lenguaje natural, consulta herramientas controladas y devuelve explicaciones/UI estructurada; no inventa cálculos ni escribe cambios sin aprobación humana.

## Documentos relacionados

- [FLOW.md](./FLOW.md): recorrido funcional, generación de estrategias y retroalimentación.
- [ARCHITECTURE.md](./ARCHITECTURE.md): componentes AWS, responsabilidades e interfaces.
- [Diagrama de arquitectura AWS (SVG)](./docs/stockcopilot-aws-architecture.svg): versión vectorial editable con los servicios, flujos y límites de la solución.
- [Diagrama de arquitectura AWS (PNG)](./docs/stockcopilot-architecture.png): versión lista para visualizar o incluir en presentaciones.

