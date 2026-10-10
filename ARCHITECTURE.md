# StockCopilot — arquitectura propuesta

## Vista general

```text
Fuentes operativas
  POS / tickets, stock, catálogo, proveedores, clima, eventos
                         |
                         v
             API Gateway + EventBridge
               |                    |
               v                    v
      DynamoDB (estado actual)   S3 Raw (histórico)
               |                    |
               |                    v
               |            ETL: Lambda / Glue
               |                    |
               |                    v
               |           S3 Curated / features
               |                    |
               |                    v
               +--------> SageMaker AI
                              | forecast / afinidad / impacto
                              v
                    Motor de decisiones y optimizador
                              |
                              v
                    API de experiencia + Bedrock Agent
                              |
                              v
                  Dashboard / encargado de tienda
                              |
                              +--> aprobación, ejecución y feedback
                                      -> EventBridge / DynamoDB / S3 Raw
```

## Componentes y responsabilidades

### Fuentes operativas

| Fuente | Datos principales |
| --- | --- |
| POS y tickets | Venta, SKU, cantidad, precio, descuento, ticket y promoción aplicada |
| Inventario | Stock actual, recibido, en tránsito, merma, ajustes y caducidad |
| Catálogo y costos | Producto, categoría, costo, precio, tamaño de caja y vida útil |
| Proveedores | Lead time, entregas y cumplimiento |
| Contexto externo | Clima, calendario, temporadas y eventos locales |

### Amazon API Gateway

Expone endpoints síncronos para frontend y sistemas operativos. Casos: consultar el plan, consultar forecast, solicitar estrategia personalizada, agregar al plan, aprobar/rechazar un borrador.

Puede representar dos roles lógicos en el diagrama, pero puede ser una sola API con rutas separadas:

- **ingesta operacional**: entrada validada de ventas, recepciones y ajustes;
- **experiencia**: endpoints consumidos por dashboard y agente.

### Amazon EventBridge

Bus asíncrono de eventos. Desacopla una venta/recepción de los procesos posteriores: actualizar stock, persistir histórico, comprobar alertas y reevaluar recomendaciones. También agenda procesos diarios y semanales.

### Amazon DynamoDB

Almacén operativo de baja latencia para el estado actual:

```text
stock por tienda-SKU
pedidos en tránsito
alertas activas
recomendaciones y estrategias en borrador
estado de aprobación/activación
configuración y restricciones por tienda
```

No es el repositorio analítico de largo plazo; las consultas históricas complejas salen de S3 Curated/Athena.

### Amazon S3 Raw

Data lake histórico, inmutable y particionado por fecha/origen. Conserva eventos originales y permite reproducir transformaciones o reconstruir datasets.

### ETL: AWS Lambda / AWS Glue

El ETL valida, normaliza, une y enriquece fuentes. Produce la tabla `fecha × tienda × SKU` y la tabla de tickets. Para el hackathon, Lambda + Python/Pandas es suficiente; AWS Glue es la alternativa escalable.

### Amazon S3 Curated y Athena

S3 Curated almacena tablas limpias y variables derivadas listas para ML y análisis. Athena permite consultas SQL históricas: promociones más rentables, merma por categoría, afinidad de productos o comportamiento por tienda.

### Amazon SageMaker AI

Responsable de entrenar, evaluar y servir artefactos ML:

- forecast probabilístico de demanda con CatBoost/LightGBM (P50/P80/P90);
- afinidad de productos a partir de tickets;
- modelo de impacto/uplift de promociones cuando el dataset lo permita;
- backtesting temporal y comparación contra el modelo vigente.

La inferencia puede ser batch diaria para el forecast. El motor toma esa predicción y la combina con stock actual para generar recomendaciones dinámicas sin reentrenar ante cada venta.

### Motor de decisiones y optimizador

Servicio determinista, por ejemplo Lambda/Python, que recibe forecast, estado actual, restricciones y candidatas. Debe:

1. Proyectar inventario por horizonte.
2. Calcular riesgo de quiebre y merma.
3. Simular el impacto incremental de cada estrategia.
4. Eliminar estrategias no viables.
5. Seleccionar el portafolio que maximiza impacto neto sin doble conteo.

El motor es la fuente de verdad para valores económicos. El LLM no realiza estos cálculos.

### Amazon Bedrock Agent / GenUI

Interfaz conversacional sobre herramientas controladas. El agente interpreta la intención del encargado, solicita datos por funciones y devuelve una respuesta estructurada para renderizar componentes permitidos: tarjetas de estrategia, gráfico de forecast, calendario de reabasto o resumen del plan.

El frontend nunca debe ejecutar HTML/JavaScript arbitrario generado por el modelo. El contrato debe ser JSON validado contra un esquema de componentes conocido.

### Dashboard y aprobación humana

Muestra KPIs, estrategias, simulaciones y alertas. El encargado puede activar o retirar una estrategia del plan, pero una promoción requiere aprobación explícita antes de ejecutarse. El resultado vuelve como evento al ciclo de aprendizaje.

## Herramientas del agente

El agente debe obtener información por APIs o funciones con mínimo privilegio, no mediante acceso directo y generalizado a DynamoDB/S3:

```text
get_current_store_context(store_id)
get_demand_forecast(store_id, horizon)
get_historical_insights(store_id, filters)
get_business_constraints(store_id)
simulate_strategy(strategy)
optimize_portfolio(candidates)
create_promotion_draft(strategy)
```

## Seguridad y control

- IAM de mínimo privilegio por servicio y función.
- Cifrado en tránsito y en reposo para datos y logs.
- Validar parámetros de todas las herramientas llamadas por el agente.
- El agente sólo puede crear borradores; activar promociones requiere autorización de un usuario.
- Registrar entradas, decisiones, simulaciones y aprobaciones para auditoría.
- Separar datos operativos actuales de datos analíticos históricos.

## Límites del MVP

- Forecast y simulación pueden correr sobre dataset sintético/expandido.
- Si no hay tickets reales, la afinidad se debe presentar como simulada o inferida con el dataset generado, no como comportamiento observado real.
- Si no hay experimentos/promociones históricas, el uplift inicial es una simulación basada en reglas causales del generador de datos. La versión productiva debe validarse con pruebas A/B o técnicas causales.
- La activación de promociones puede ser simulada; el flujo de aprobación y medición sigue siendo válido.

