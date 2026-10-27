# Soluparts: monitoreo público con navegador

Monitor gratuito de acceso y carga de productos con GitHub Actions y Playwright/Chromium. Publicación autorizada el 27/10/2026. Prueba real ejecutada el 27/10/2026 a las 16:29 de Chile: Cloudflare bloqueó la portada con HTTP 403 en ambos intentos. El programa y la persistencia funcionaron; se revisaron cero productos. El escaneo de productos todavía no está operativo. La programación de consultas y los correos permanecen pausados.

## Evidencia de la primera prueba

- Ejecución: https://github.com/matiasgutierrezquin-bit/soluparts-monitor/actions/runs/40569455943
- Registro: https://github.com/matiasgutierrezquin-bit/soluparts-monitor/blob/monitor-data/records/2026-10-27/40569455943-1.json
- Título devuelto: `Attention Required! | Cloudflare`.
- HTTP: `403`; resultado: `blocked`; `operational_observation=false`.
- Cloudflare Ray IDs: `a5141c84cc7be1ee-LAX` y `a5141d034dbbf7bb-LAX`.

Esto acredita un bloqueo al monitor, no una caída del sitio ni una falla atribuible a su proveedor. Antes de repetir la prueba, quien administra Cloudflare de Soluparts debe autorizar el acceso de este monitor a la portada, búsquedas, categorías y fichas. No se debe desactivar globalmente la seguridad del sitio. Una ejecución de GitHub marcada como exitosa indica que el programa terminó y guardó el resultado; no significa que el sitio haya aprobado la validación.

## Alcance

- Objetivo: un control cada 20 minutos, minutos 07, 27 y 47, todos los días. Corre en GitHub aunque el usuario cierre ChatGPT o apague su computador.
- Cada control visita portada, una búsqueda que rota entre 12 familias de repuestos, hasta cuatro categorías/páginas del catálogo y hasta 12 fichas de productos.
- El inventario descubierto se conserva entre controles. Se priorizan productos nunca revisados, se distribuye la muestra entre categorías y después se vuelve a los menos recientemente revisados. No hay una ficha fija obligatoria en todos los controles.
- Se comprueban HTTP, nombre, precio e imagen principal cargada. Se miden tiempos y se registran errores JavaScript, recursos fallidos y el primer intento cuando hay reintento.
- Un problema se vuelve a comprobar una vez tras 20 segundos. Un bloqueo confirmado detiene las demás rutas de ese control. No se utilizan proxies, cambios de huella ni evasión de protecciones.
- Se informa el número real de productos descubiertos, únicos intentados, únicos cargados correctamente, pendientes y categorías recorridas. No se declara una revisión de todo el catálogo cada 20 minutos.
- Presupuesto de siete minutos por control. Si no alcanza para la muestra prevista, se informa cobertura incompleta; los productos pendientes conservan su prioridad.

GitHub puede retrasar u omitir ejecuciones bajo carga. Los informes deben indicar las horas reales, los huecos y las limitaciones del muestreo. Un bloqueo de Cloudflare afecta al monitor y no demuestra por sí solo que Soluparts esté caída.

## Datos y costo

Usa runners estándar de Linux en un repositorio público. No requiere Firecrawl, API de IA ni servidor de pago. El workflow rechaza repositorios privados. No utiliza artifacts ni caches de Actions.

La rama `monitor-data` conserva:

- `history.ndjson`: índice por control con fecha real, resultado, ruta y SHA-256.
- `records/AAAA-MM-DD/ID.json`: mediciones de las páginas efectivamente visitadas y cobertura de ese control.
- `inventory.json`: inventario descubierto y fechas de revisión por producto/categoría.
- `state.json`: último control y última observación operativa completa.
- `evidence/`: como máximo una captura de incidente de hasta 64 KiB por control. Cada registro explica el recorte de diagnósticos. No se conserva HTML completo.

El código, las URL, los resultados y las capturas son públicos. No se incluyen contraseñas, cookies de sesión ni claves o destinatarios de Gmail. El correo se envía desde la tarea de Gmail autorizada por el usuario, fuera de GitHub.

## Activación y período

1. La publicación inicial en `main` ejecuta una prueba real. La programación empieza desactivada: `monitor_enabled=false` en `config.json`.
2. Leer las mediciones de `monitor-data`. La primera validación requiere portada, búsqueda y al menos tres productos distintos cargados con nombre, precio e imagen, sin bloqueos, errores del monitor ni resultados inconclusos.
3. Solo después se cambia `monitor_enabled=true` y se verifica la programación. Si el sitio bloquea el monitor, su administrador debe autorizarlo. No se simula una prueba exitosa.
4. Solo entonces se reactiva la tarea de informes diarios. Prueba: 27/10/2026 a las 22:00 de Chile. Informes oficiales diarios: 28/10 a 24/11/2026 a las 22:00. El último día también se envía el informe completo de cuatro semanas. No se retrofechan envíos.
5. Después del 24/11/2026 a las 22:00 de Chile el código deja de solicitar páginas. Los registros permanecen disponibles.

## Verificación del código

`python -m unittest discover -s tests -v` comprueba persistencia, requisitos de operación, conservación del primer intento, fin del período, rotación sin repeticiones mientras existan productos pendientes y reparto por categorías. Son pruebas con datos simulados; no sustituyen la primera ejecución real.

Referencias oficiales:

- https://docs.github.com/en/actions/concepts/billing-and-usage
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
- https://playwright.dev/python/docs/intro
