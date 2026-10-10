# StockCopilot — arquitectura propuesta

## Vista general

```text
Fuentes operativas
  POS / tickets, stock, catálogo, proveedores, clima, eventos
                         |
                         v
        API Gateway (petición) + EventBridge (evento/agenda)
               |                    |
               v                    v
      DynamoDB (estado actual)   S3 Raw (histórico)
               |                    |
               |                    v
               |            ETL: Lambda / Glue
               |                    |
               |                    v
               |           S3 Curated / features ----> Athena
               |                    |
               |                    v
               +--------> SageMaker AI
                           forecast / afinidad / uplift
                                      |
                                      v
                    Motor de simulación y optimizador
                                      |
                 +--------------------+-------------------+
                 |                                        |
                 v                                        v
      Plan automático: hasta 5 estrategias       Estrategia por prompt
      por tienda y periodo                        solicitada al agente
                 |                                        |
                 +--------------------+-------------------+
                                      |
                                      v
          API de experiencia + Copilot Orchestrator
             LangChain / LangGraph + Amazon Bedrock
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

**No sustituye a Lambda:** EventBridge enruta o agenda; Lambda ejecuta código. Para el MVP se pueden invocar Lambdas directamente desde API Gateway y reducir eventos internos, pero se recomienda conservar EventBridge para el plan diario, el reentrenamiento semanal y procesos que no deben acoplarse a una solicitud de usuario.

### Amazon DynamoDB

Almacén operativo de baja latencia para el estado actual:

```text
stock por tienda-SKU
pedidos en tránsito
alertas activas
recomendaciones y estrategias en borrador
plan comercial vigente por tienda y periodo
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

### Planificador de estrategias

El planificador construye el **plan comercial automático** que aparece siempre en la interfaz. Se ejecuta diariamente, al cambiar una señal relevante o cuando el usuario pide regenerarlo.

1. Obtiene el contexto actual, forecast base e insights históricos.
2. Genera candidatas a partir de plantillas permitidas: combo de afinidad, combo de rescate y promoción contextual.
3. Envía cada candidata al motor de simulación.
4. Pasa las candidatas viables al optimizador de portafolio.
5. Guarda hasta cinco estrategias priorizadas para la tienda y el periodo.

El planificador no debe forzar cinco promociones: si existen menos oportunidades rentables y viables, devuelve sólo las que cumplan las reglas.

### Copilot Orchestrator — LangChain/LangGraph + Amazon Bedrock

LangGraph controla explícitamente el estado y las transiciones de la conversación; LangChain integra el modelo de Amazon Bedrock mediante `ChatBedrockConverse`. Bedrock interpreta intención, selecciona herramientas permitidas y redacta respuestas, pero no es la fuente de verdad de los cálculos.

Para un despliegue AWS nuevo, el grafo se empaqueta como contenedor ARM64 y se ejecuta en **Amazon Bedrock AgentCore Runtime**. El protocolo recomendado inicialmente es HTTP: `GET /ping` para salud y `POST /invocations` para solicitudes del dashboard. API Gateway conserva autenticación, límites y el contrato público. Para una demo local se puede ejecutar exactamente el mismo grafo detrás de una API Python; no se usa Bedrock Agents Classic, que está en modo de mantenimiento para clientes nuevos.

El grafo mínimo es:

```text
INTENCIÓN
  -> OBTENER_DATOS
  -> FORECAST_E_INSIGHTS
  -> GENERAR_CANDIDATAS
  -> SIMULAR
  -> VALIDAR
  -> OPTIMIZAR_PORTAFOLIO
  -> RESPONDER_JSON
  -> ESPERAR_APROBACIÓN
  -> GUARDAR_BORRADOR
```

Los nodos de datos, simulación y validación son funciones deterministas. Los checkpoints de LangGraph permiten retomar un borrador, aplicar reintentos seguros y separar la aprobación humana de la generación de la estrategia.

El frontend nunca debe ejecutar HTML/JavaScript arbitrario generado por el modelo. El contrato debe ser JSON validado contra un esquema de componentes conocido.

El agente tiene dos funciones complementarias:

- **curaduría del plan automático:** puede resumir y explicar las estrategias elegidas por el motor;
- **estrategia bajo demanda:** transforma una petición como “aumenta bebidas energéticas el sábado, con máximo 10% de descuento” en filtros y restricciones, genera candidatas y solicita su simulación.

Una estrategia creada por prompt inicia como borrador. Al agregarla al plan, el optimizador recalcula el impacto del portafolio completo para evitar doble conteo o canibalización.

El Runtime recibe una sola identidad IAM de mínimo privilegio y las herramientas validan todos sus parámetros. No se conceden accesos generales del LLM a S3, Athena o DynamoDB. Para producción, el Runtime usa VPC, autenticación de entrada, CloudTrail y logs de CloudWatch cifrados y con retención limitada.

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

Dos funciones de experiencia agrupan esas herramientas sin ampliar permisos:

```text
get_product_tendency(store_id, sku, history_days, forecast_days)
  -> histórico Athena/S3 Curated + forecast SageMaker
  -> tendencia, riesgos y series P50/P80/P90 calculados
  -> Bedrock sólo redacta `ai_summary`

get_product_sell_strategy(store_id, objective, constraints)
  -> contexto -> forecast -> insights -> candidatas permitidas
  -> simulador -> validador -> optimizador -> create_promotion_draft
```

La respuesta de estrategia incluye explícitamente series `baseline` y `treated`, unidades/ventas/margen incrementales, merma evitada, riesgo de quiebre, incertidumbre, supuestos, `draft_id` y `requires_approval: true`. Nunca se presenta el efecto como garantizado.

## Flujos de generación de estrategias

### Plan automático de la interfaz

```text
EventBridge Scheduler o acción “regenerar plan”
  -> Strategy Planner Lambda
  -> contexto actual (DynamoDB)
  -> forecast/impacto (SageMaker)
  -> histórico y afinidad (Athena/S3 Curated)
  -> simulador + optimizador (Decision Engine Lambda)
  -> guardar plan de hasta 5 estrategias en DynamoDB
  -> dashboard consulta el plan mediante API Gateway
```

### Estrategia solicitada por prompt

```text
Usuario escribe una intención en el dashboard
  -> API Gateway + Copilot Orchestrator (LangGraph)
  -> Bedrock convierte intención en objetivo/restricciones
  -> herramientas de contexto, forecast e histórico
  -> candidatas permitidas -> simulador -> validador -> optimizador
  -> tarjeta de estrategia y comparación contra el plan actual
  -> usuario agrega, descarta o solicita ajuste
  -> borrador/aprobación persistidos en DynamoDB
```

El total mostrado al usuario siempre es `impacto neto del portafolio seleccionado`; nunca se calcula como una suma ciega de impactos individuales.

## Red y subredes para Lambda

Una función Lambda **no es una subred**. Puede configurarse para usar subredes privadas de una VPC y un security group, normalmente en dos zonas de disponibilidad. Esto sólo es necesario si debe acceder a recursos privados, por ejemplo una base de datos privada o un endpoint interno.

Configuración de referencia si se decide usar VPC:

```text
VPC
  Private app subnet AZ-a  -> Lambdas de API, agente y motor de decisiones
  Private app subnet AZ-b  -> Lambdas de API, agente y motor de decisiones
  VPC endpoints            -> S3 y DynamoDB (y endpoints privados necesarios)
  NAT Gateway (opcional)   -> sólo si una Lambda necesita salir a Internet
```

API Gateway permanece fuera de las subredes y llama a Lambda. S3 y DynamoDB son servicios regionales; no se despliegan dentro de una subred. Para un hackathon, una VPC no es imprescindible y agrega configuración. Se recomienda incluirla sólo si se desea demostrar segmentación de red o conectar recursos privados; de otro modo, usar Lambda sin VPC reduce complejidad.

## Seguridad y control

- IAM de mínimo privilegio por servicio y función.
- Cifrado en tránsito y en reposo para datos y logs.
- Validar parámetros de todas las herramientas llamadas por el agente.
- El agente sólo puede crear borradores; activar promociones requiere autorización de un usuario.
- Registrar entradas, decisiones, simulaciones y aprobaciones para auditoría.
- Separar datos operativos actuales de datos analíticos históricos.
- Si Lambda se configura en una VPC, usar subredes privadas, security groups de mínimo privilegio y endpoints privados antes de abrir salida a Internet mediante NAT.

## Límites del MVP

- Forecast y simulación pueden correr sobre dataset sintético/expandido.
- Si no hay tickets reales, la afinidad se debe presentar como simulada o inferida con el dataset generado, no como comportamiento observado real.
- Si no hay experimentos/promociones históricas, el uplift inicial es una simulación basada en reglas causales del generador de datos. La versión productiva debe validarse con pruebas A/B o técnicas causales.
- La activación de promociones puede ser simulada; el flujo de aprobación y medición sigue siendo válido.

