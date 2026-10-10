---
name: booking-assistance
description: Cómo ayudar a elegir evento y sesión (búsqueda, plazas y horarios locales).
---
# Ayuda para reservar

1. Usa las preferencias guardadas (ciudad, categoría) si el cliente no indica otras.
2. search_events para localizar opciones (máximo 5) y list_sessions para fechas y plazas.
3. Las fechas de las sesiones están en UTC: conviértelas a la hora local de la ciudad. La tabla de
   husos horarios está en el recurso `timezones.md` (load_skill con resource="timezones.md").
4. Muestra título, precio, fecha local y plazas libres. Si hay pocas plazas (<10), avísalo.
5. Todavía no se puede comprar desde el chat: indica que la compra se hace en la app de Headache.
