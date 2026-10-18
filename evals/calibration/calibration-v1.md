# Calibracion de jueces · conjunto v1

Etiqueta cada muestra en `labels-v1.csv` **sin mirar el `.jsonl`** (lleva el tipo):

- `faithful`: `si` si TODAS las afirmaciones de la respuesta estan respaldadas por los contextos, `no` si alguna no lo esta, `na` si la respuesta no afirma nada.
- `relevance`: 0 no responde, 1 tangencial, 2 responde con ruido, 3 responde directa y completamente. Mide si responde a lo preguntado, **con independencia de que sea verdad** (eso ya lo mide `faithful`).

## cal-01

**Pregunta:** ¿Hay edad mínima para el ocio nocturno en New York?

**Respuesta:** Depende de cada caso; consulta las condiciones en la web de Headache.

**Contextos:**

0. # Preguntas frecuentes: New York **¿Cuándo abren las puertas?** 15 minutos antes del inicio. **¿Dónde están mis entradas?** En la app de Headache, sección «Mis entradas». **¿Necesito imprimir la entrada?** No, basta con el código QR en el móvil. **¿Hay edad mínima?** Ocio nocturno: 18+. Resto: menores acompañados. **Atención al cliente en New York:** chat en la app, de 9:00 a 21:00 hora local.
1. # Preguntas frecuentes: Barcelona **¿Cuándo abren las puertas?** 15 minutos antes del inicio. **¿Dónde están mis entradas?** En la app de Headache, sección «Mis entradas». **¿Necesito imprimir la entrada?** No, basta con el código QR en el móvil. **¿Hay edad mínima?** Ocio nocturno: 18+. Resto: menores acompañados. **Atención al cliente en Barcelona:** chat en la app, de 9:00 a 21:00 hora local.
2. # Preguntas frecuentes: London **¿Cuándo abren las puertas?** 15 minutos antes del inicio. **¿Dónde están mis entradas?** En la app de Headache, sección «Mis entradas». **¿Necesito imprimir la entrada?** No, basta con el código QR en el móvil. **¿Hay edad mínima?** Ocio nocturno: 18+. Resto: menores acompañados. **Atención al cliente en London:** chat en la app, de 9:00 a 21:00 hora local.
3. # Preguntas frecuentes: Chicago **¿Cuándo abren las puertas?** 45 minutos antes del inicio. **¿Dónde están mis entradas?** En la app de Headache, sección «Mis entradas». **¿Necesito imprimir la entrada?** No, basta con el código QR en el móvil. **¿Hay edad mínima?** Ocio nocturno: 18+. Resto: menores acompañados. **Atención al cliente en Chicago:** chat en la app, de 9:00 a 21:00 hora local.
4. # Preguntas frecuentes: Lisbon **¿Cuándo abren las puertas?** 30 minutos antes del inicio. **¿Dónde están mis entradas?** En la app de Headache, sección «Mis entradas». **¿Necesito imprimir la entrada?** No, basta con el código QR en el móvil. **¿Hay edad mínima?** Ocio nocturno: 18+. Resto: menores acompañados. **Atención al cliente en Lisbon:** chat en la app, de 9:00 a 21:00 hora local.

## cal-02

**Pregunta:** What is the capacity of Baker Palace in London?

**Respuesta:** 80 people.

**Contextos:**

0. # Venue guide: Baker Palace (London) Address: Flat 7 Gemma Common, London. Capacity: 80 people. Accessibility: Step-free access and accessible toilets. Cloakroom available. Food and drinks from outside are not allowed.
1. # FAQ: London **When do doors open?** 15 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in London:** in-app chat, 9:00-21:00 local time.
2. # FAQ: Barcelona **When do doors open?** 15 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in Barcelona:** in-app chat, 9:00-21:00 local time.
3. # Event: Street Art Tour (London) Street Art Tour at Baker Palace, London. Tours and sightseeing. Category: Tours and sightseeing. Venue: Baker Palace, Flat 7 Gemma Common, London. Price: 32 GBP per ticket. Refunds: Refundable up to 24 hours before the start. Upcoming sessions: 2026-10-04 22:00, 2026-12-14 19:00, 2026-12-31 19:00.
4. # FAQ: Chicago **When do doors open?** 45 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in Chicago:** in-app chat, 9:00-21:00 local time.

## cal-03

**Pregunta:** ¿Qué pasa con mi entrada de «Tours y visitas» si el organizador cancela la sesión?

**Respuesta:** Se reembolsa el 100 % automáticamente. Además, el parking del recinto es gratuito para todos los asistentes.

**Contextos:**

0. # Política de reembolsos: Tours y visitas Aplicable a la categoría «Tours y visitas» en todas las ciudades. - Reembolsable: sí, hasta 24 horas antes del inicio. - El importe se devuelve al método de pago original en 5-10 días hábiles. - Los gastos de gestión no son reembolsables. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.
1. # Política de reembolsos: Gastronomía y bebidas Aplicable a la categoría «Gastronomía y bebidas» en todas las ciudades. - Reembolsable: sí, hasta 72 horas antes del inicio. - El importe se devuelve al método de pago original en 5-10 días hábiles. - Los gastos de gestión no son reembolsables. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.
2. # Política de reembolsos: Deportes y actividades Aplicable a la categoría «Deportes y actividades» en todas las ciudades. - Estas entradas no admiten reembolso. - Se permite un cambio de fecha hasta 24 horas antes, sujeto a disponibilidad. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.
3. # Política de reembolsos: Teatro y espectáculos Aplicable a la categoría «Teatro y espectáculos» en todas las ciudades. - Estas entradas no admiten reembolso. - Se permite un cambio de fecha hasta 24 horas antes, sujeto a disponibilidad. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.
4. # Política de reembolsos: Conciertos a la luz de las velas Aplicable a la categoría «Conciertos a la luz de las velas» en todas las ciudades. - Reembolsable: sí, hasta 48 horas antes del inicio. - El importe se devuelve al método de pago original en 5-10 días hábiles. - Los gastos de gestión no son reembolsables. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.

## cal-04

**Pregunta:** What is the capacity of Burton Studio in New York?

**Respuesta:** 350 people.

**Contextos:**

0. # Venue guide: Burton Studio (New York) Address: 166 Rice Plaza Apt. 184, New York. Capacity: 350 people. Accessibility: Step-free access and accessible toilets. Cloakroom available. Food and drinks from outside are not allowed.
1. # Event: Sushi Workshop (New York) Sushi Workshop at Burton Studio, New York. Food and drink. Category: Food and drink. Venue: Burton Studio, 166 Rice Plaza Apt. 184, New York. Price: 77 USD per ticket. Refunds: Refundable up to 72 hours before the start. Upcoming sessions: 2026-12-09 22:00, 2027-01-17 21:00, 2027-01-26 21:00.
2. # FAQ: New York **When do doors open?** 15 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in New York:** in-app chat, 9:00-21:00 local time.
3. # Event: Hidden Gems Tour (New York) Hidden Gems Tour at Burton Studio, New York. Tours and sightseeing. Category: Tours and sightseeing. Venue: Burton Studio, 166 Rice Plaza Apt. 184, New York. Price: 50 USD per ticket. Refunds: Refundable up to 24 hours before the start. Upcoming sessions: 2026-11-01 20:00, 2026-11-10 22:00, 2026-12-24 19:00.
4. # Event: Candlelit Strings: Epic Film Scores (New York) Candlelit Strings: Epic Film Scores at Burton Studio, New York. Candlelit concerts. Category: Candlelit concerts. Venue: Burton Studio, 166 Rice Plaza Apt. 184, New York. Price: 40 USD per ticket. Refunds: Refundable up to 48 hours before the start. Upcoming sessions: 2026-10-09 19:00, 2026-10-10 19:00, 2026-11-09 20:00.

## cal-05

**Pregunta:** ¿Puedo pedir el reembolso de una entrada de «Teatro y espectáculos»? ¿Hasta cuándo?

**Respuesta:** No: no admiten reembolso, pero se permite un cambio de fecha hasta 48 horas antes.

**Contextos:**

0. # Política de reembolsos: Teatro y espectáculos Aplicable a la categoría «Teatro y espectáculos» en todas las ciudades. - Estas entradas no admiten reembolso. - Se permite un cambio de fecha hasta 24 horas antes, sujeto a disponibilidad. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.
1. # Política de reembolsos: Gastronomía y bebidas Aplicable a la categoría «Gastronomía y bebidas» en todas las ciudades. - Reembolsable: sí, hasta 72 horas antes del inicio. - El importe se devuelve al método de pago original en 5-10 días hábiles. - Los gastos de gestión no son reembolsables. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.
2. # Política de reembolsos: Deportes y actividades Aplicable a la categoría «Deportes y actividades» en todas las ciudades. - Estas entradas no admiten reembolso. - Se permite un cambio de fecha hasta 24 horas antes, sujeto a disponibilidad. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.
3. # Política de reembolsos: Tours y visitas Aplicable a la categoría «Tours y visitas» en todas las ciudades. - Reembolsable: sí, hasta 24 horas antes del inicio. - El importe se devuelve al método de pago original en 5-10 días hábiles. - Los gastos de gestión no son reembolsables. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.
4. # Política de reembolsos: Ocio nocturno Aplicable a la categoría «Ocio nocturno» en todas las ciudades. - Estas entradas no admiten reembolso. - Se permite un cambio de fecha hasta 24 horas antes, sujeto a disponibilidad. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.

## cal-06

**Pregunta:** What are the customer support hours?

**Respuesta:** In-app chat, 9:00-21:00 local time. Parking at the venue is also free for every attendee.

**Contextos:**

0. # FAQ: Mexico City **When do doors open?** 15 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in Mexico City:** in-app chat, 9:00-21:00 local time.
1. # Venue guide: Menéndez Gallery (Mexico City) Address: Circuito Suriname 382 Interior 997, Mexico City. Capacity: 80 people. Accessibility: Access via stairs only; contact support for assistance. Cloakroom available. Food and drinks from outside are not allowed.
2. # Event: Sushi Workshop (Mexico City) Sushi Workshop at Menéndez Gallery, Mexico City. Food and drink. Category: Food and drink. Venue: Menéndez Gallery, Circuito Suriname 382 Interior 997, Mexico City. Price: 73 MXN per ticket. Refunds: Refundable up to 72 hours before the start. Upcoming sessions: 2026-10-25 20:00, 2027-01-24 22:00, 2027-01-29 21:00.
3. # Venue guide: Guerra Gallery (Mexico City) Address: Calzada Sur Calderón 656 Edif. 670 , Depto. 106, Mexico City. Capacity: 80 people. Accessibility: Step-free access and accessible toilets. Cloakroom available. Food and drinks from outside are not allowed.
4. # Event: Dinosaurs Alive (Mexico City) Dinosaurs Alive at Guerra Gallery, Mexico City. Immersive exhibitions. Category: Immersive exhibitions. Venue: Guerra Gallery, Calzada Sur Calderón 656 Edif. 670 , Depto. 106, Mexico City. Price: 34 MXN per ticket. Refunds: Refundable up to 24 hours before the start. Upcoming sessions: 2026-10-02 21:00, 2026-12-13 20:00, 2027-01-17 21:00.

## cal-07

**Pregunta:** How early do doors open in Chicago?

**Respuesta:** 45 minutes before the start. Parking at the venue is also free for every attendee.

**Contextos:**

0. # FAQ: Chicago **When do doors open?** 45 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in Chicago:** in-app chat, 9:00-21:00 local time.
1. # FAQ: Barcelona **When do doors open?** 15 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in Barcelona:** in-app chat, 9:00-21:00 local time.
2. # FAQ: Lisbon **When do doors open?** 30 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in Lisbon:** in-app chat, 9:00-21:00 local time.
3. # FAQ: London **When do doors open?** 15 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in London:** in-app chat, 9:00-21:00 local time.
4. # FAQ: Madrid **When do doors open?** 45 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in Madrid:** in-app chat, 9:00-21:00 local time.

## cal-08

**Pregunta:** ¿Puedo cancelar estas entradas y recuperar el dinero?

**Respuesta:** Depende de cada caso; consulta las condiciones en la web de Headache.

**Contextos:**

0. # Política de reembolsos: Ocio nocturno Aplicable a la categoría «Ocio nocturno» en todas las ciudades. - Estas entradas no admiten reembolso. - Se permite un cambio de fecha hasta 24 horas antes, sujeto a disponibilidad. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.

## cal-09

**Pregunta:** ¿Cuál es el aforo del recinto Mack Hall en New York?

**Respuesta:** 200 personas.

**Contextos:**

0. # Event: Latin Night (New York) Latin Night at Mack Hall, New York. Nightlife. Category: Nightlife. Venue: Mack Hall, 46270 Stanton Track Apt. 814, New York. Price: 35 USD per ticket. Refunds: Non-refundable; one date change allowed up to 24 hours before. Upcoming sessions: 2026-10-03 22:00, 2026-12-13 19:00, 2027-01-23 18:00.
1. # Venue guide: Mack Hall (New York) Address: 46270 Stanton Track Apt. 814, New York. Capacity: 200 people. Accessibility: Step-free access and accessible toilets. Cloakroom available. Food and drinks from outside are not allowed.
2. # Event: Silent Disco (New York) Silent Disco at Mack Hall, New York. Nightlife. Category: Nightlife. Venue: Mack Hall, 46270 Stanton Track Apt. 814, New York. Price: 14 USD per ticket. Refunds: Non-refundable; one date change allowed up to 24 hours before. Upcoming sessions: 2026-10-25 21:00, 2026-11-06 21:00, 2026-11-26 21:00.
3. # Preguntas frecuentes: New York **¿Cuándo abren las puertas?** 15 minutos antes del inicio. **¿Dónde están mis entradas?** En la app de Headache, sección «Mis entradas». **¿Necesito imprimir la entrada?** No, basta con el código QR en el móvil. **¿Hay edad mínima?** Ocio nocturno: 18+. Resto: menores acompañados. **Atención al cliente en New York:** chat en la app, de 9:00 a 21:00 hora local.
4. # Event: Murder Mystery Dinner Show (New York) Murder Mystery Dinner Show at Mack Hall, New York. Theatre and shows. Category: Theatre and shows. Venue: Mack Hall, 46270 Stanton Track Apt. 814, New York. Price: 73 USD per ticket. Refunds: Non-refundable; one date change allowed up to 24 hours before. Upcoming sessions: 2026-10-27 21:00, 2026-11-09 21:00, 2027-01-16 22:00.

## cal-10

**Pregunta:** Is there a minimum age for nightlife in Paris?

**Respuesta:** Yes, 18+. Parking at the venue is also free for every attendee.

**Contextos:**

0. # FAQ: Paris **When do doors open?** 45 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in Paris:** in-app chat, 9:00-21:00 local time.
1. # FAQ: Barcelona **When do doors open?** 15 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in Barcelona:** in-app chat, 9:00-21:00 local time.
2. # FAQ: Chicago **When do doors open?** 45 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in Chicago:** in-app chat, 9:00-21:00 local time.
3. # FAQ: Lisbon **When do doors open?** 30 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in Lisbon:** in-app chat, 9:00-21:00 local time.
4. # FAQ: London **When do doors open?** 15 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in London:** in-app chat, 9:00-21:00 local time.

## cal-11

**Pregunta:** If my 'Immersive exhibitions' ticket is refunded, how long until I get the money back?

**Respuesta:** 30 minutos antes del inicio.

**Contextos:**

0. # Refund policy: Immersive exhibitions Applies to the 'Immersive exhibitions' category in every city. - Refundable: yes, up to 24 hours before the start. - Money is returned to the original payment method within 5-10 business days. - Booking fees are non-refundable. - If the organiser cancels the session, a full refund is issued automatically.
1. # FAQ: Barcelona **When do doors open?** 15 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in Barcelona:** in-app chat, 9:00-21:00 local time.
2. # FAQ: Chicago **When do doors open?** 45 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in Chicago:** in-app chat, 9:00-21:00 local time.
3. # FAQ: Lisbon **When do doors open?** 30 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in Lisbon:** in-app chat, 9:00-21:00 local time.
4. # FAQ: London **When do doors open?** 15 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in London:** in-app chat, 9:00-21:00 local time.

## cal-12

**Pregunta:** ¿Qué pasa con mi entrada de «Gastronomía y bebidas» si el organizador cancela la sesión?

**Respuesta:** Se reembolsa el 50 % automáticamente.

**Contextos:**

0. # Política de reembolsos: Gastronomía y bebidas Aplicable a la categoría «Gastronomía y bebidas» en todas las ciudades. - Reembolsable: sí, hasta 72 horas antes del inicio. - El importe se devuelve al método de pago original en 5-10 días hábiles. - Los gastos de gestión no son reembolsables. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.
1. # Política de reembolsos: Tours y visitas Aplicable a la categoría «Tours y visitas» en todas las ciudades. - Reembolsable: sí, hasta 24 horas antes del inicio. - El importe se devuelve al método de pago original en 5-10 días hábiles. - Los gastos de gestión no son reembolsables. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.
2. # Política de reembolsos: Deportes y actividades Aplicable a la categoría «Deportes y actividades» en todas las ciudades. - Estas entradas no admiten reembolso. - Se permite un cambio de fecha hasta 24 horas antes, sujeto a disponibilidad. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.
3. # Política de reembolsos: Teatro y espectáculos Aplicable a la categoría «Teatro y espectáculos» en todas las ciudades. - Estas entradas no admiten reembolso. - Se permite un cambio de fecha hasta 24 horas antes, sujeto a disponibilidad. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.
4. # Política de reembolsos: Conciertos a la luz de las velas Aplicable a la categoría «Conciertos a la luz de las velas» en todas las ciudades. - Reembolsable: sí, hasta 48 horas antes del inicio. - El importe se devuelve al método de pago original en 5-10 días hábiles. - Los gastos de gestión no son reembolsables. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.

## cal-13

**Pregunta:** Does Skinner Gallery in Los Angeles have step-free access?

**Respuesta:** Yes: step-free access and accessible toilets.

**Contextos:**

0. # Venue guide: Skinner Gallery (Los Angeles) Address: 52880 Burns Creek, Los Angeles. Capacity: 200 people. Accessibility: Step-free access and accessible toilets. Cloakroom available. Food and drinks from outside are not allowed.
1. # Event: Stadium Tour (Los Angeles) Stadium Tour at Skinner Gallery, Los Angeles. Sports and activities. Category: Sports and activities. Venue: Skinner Gallery, 52880 Burns Creek, Los Angeles. Price: 50 USD per ticket. Refunds: Non-refundable; one date change allowed up to 24 hours before. Upcoming sessions: 2026-11-29 19:00.
2. # Venue guide: Hickman Palace (Los Angeles) Address: 03911 Cabrera Trace Apt. 278, Los Angeles. Capacity: 80 people. Accessibility: Step-free access and accessible toilets. Cloakroom available. Food and drinks from outside are not allowed.
3. # Venue guide: Leleu Gallery (Paris) Address: 4, avenue Lopes, Paris. Capacity: 600 people. Accessibility: Step-free access and accessible toilets. Cloakroom available. Food and drinks from outside are not allowed.
4. # Event: Ghost Walking Tour (Los Angeles) Ghost Walking Tour at Skinner Gallery, Los Angeles. Tours and sightseeing. Category: Tours and sightseeing. Venue: Skinner Gallery, 52880 Burns Creek, Los Angeles. Price: 25 USD per ticket. Refunds: Refundable up to 24 hours before the start. Upcoming sessions: 2026-10-21 18:00, 2026-11-19 18:00, 2026-12-06 21:00.

## cal-14

**Pregunta:** Can I get a refund for 'Tours and sightseeing' tickets, and until when?

**Respuesta:** Yes, up to 48 hours before the start.

**Contextos:**

0. # Refund policy: Tours and sightseeing Applies to the 'Tours and sightseeing' category in every city. - Refundable: yes, up to 24 hours before the start. - Money is returned to the original payment method within 5-10 business days. - Booking fees are non-refundable. - If the organiser cancels the session, a full refund is issued automatically.
1. # Refund policy: Sports and activities Applies to the 'Sports and activities' category in every city. - These tickets are non-refundable. - One date change is allowed up to 24 hours before, subject to availability. - If the organiser cancels the session, a full refund is issued automatically.
2. # Refund policy: Theatre and shows Applies to the 'Theatre and shows' category in every city. - These tickets are non-refundable. - One date change is allowed up to 24 hours before, subject to availability. - If the organiser cancels the session, a full refund is issued automatically.
3. # Event: Ghost Walking Tour (Paris) Ghost Walking Tour at Leleu Gallery, Paris. Tours and sightseeing. Category: Tours and sightseeing. Venue: Leleu Gallery, 4, avenue Lopes, Paris. Price: 20 EUR per ticket. Refunds: Refundable up to 24 hours before the start. Upcoming sessions: 2026-10-23 20:00, 2026-11-07 21:00, 2026-12-17 22:00.
4. # FAQ: Barcelona **When do doors open?** 15 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in Barcelona:** in-app chat, 9:00-21:00 local time.

## cal-15

**Pregunta:** What are the customer support hours?

**Respuesta:** In-app chat, 9:00-21:00 local time.

**Contextos:**

0. # FAQ: Lisbon **When do doors open?** 30 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in Lisbon:** in-app chat, 9:00-21:00 local time.
1. # Venue guide: Tavares Gallery (Lisbon) Address: Rua Isaac Miranda, 94, Lisbon. Capacity: 600 people. Accessibility: Access via stairs only; contact support for assistance. Cloakroom available. Food and drinks from outside are not allowed.
2. # Venue guide: Domingues Studio (Lisbon) Address: Rua Amorim, 56, Lisbon. Capacity: 200 people. Accessibility: Step-free access and accessible toilets. Cloakroom available. Food and drinks from outside are not allowed.
3. # Event: Wine Tasting Experience (Lisbon) Wine Tasting Experience at Tavares Gallery, Lisbon. Food and drink. Category: Food and drink. Venue: Tavares Gallery, Rua Isaac Miranda, 94, Lisbon. Price: 57 EUR per ticket. Refunds: Refundable up to 72 hours before the start. Upcoming sessions: 2026-10-27 19:00, 2026-11-08 19:00, 2026-11-25 21:00.
4. # Event: Candlelit Strings: Epic Film Scores (Lisbon) Candlelit Strings: Epic Film Scores at Tavares Gallery, Lisbon. Candlelit concerts. Category: Candlelit concerts. Venue: Tavares Gallery, Rua Isaac Miranda, 94, Lisbon. Price: 48 EUR per ticket. Refunds: Refundable up to 48 hours before the start. Upcoming sessions: 2026-10-31 19:00, 2026-11-03 18:00, 2027-01-22 21:00.

## cal-16

**Pregunta:** Does Leleu Gallery in Paris have step-free access?

**Respuesta:** 57 EUR per ticket.

**Contextos:**

0. # Venue guide: Leleu Gallery (Paris) Address: 4, avenue Lopes, Paris. Capacity: 600 people. Accessibility: Step-free access and accessible toilets. Cloakroom available. Food and drinks from outside are not allowed.
1. # Event: Latin Night (Paris) Latin Night at Leleu Gallery, Paris. Nightlife. Category: Nightlife. Venue: Leleu Gallery, 4, avenue Lopes, Paris. Price: 19 EUR per ticket. Refunds: Non-refundable; one date change allowed up to 24 hours before. Upcoming sessions: 2026-10-25 22:00, 2026-12-27 20:00.
2. # Event: Padel Clinic (Paris) Padel Clinic at Leleu Gallery, Paris. Sports and activities. Category: Sports and activities. Venue: Leleu Gallery, 4, avenue Lopes, Paris. Price: 30 EUR per ticket. Refunds: Non-refundable; one date change allowed up to 24 hours before. Upcoming sessions: 2026-10-18 18:00, 2026-10-31 21:00, 2027-01-24 19:00.
3. # Event: Ghost Walking Tour (Paris) Ghost Walking Tour at Leleu Gallery, Paris. Tours and sightseeing. Category: Tours and sightseeing. Venue: Leleu Gallery, 4, avenue Lopes, Paris. Price: 20 EUR per ticket. Refunds: Refundable up to 24 hours before the start. Upcoming sessions: 2026-10-23 20:00, 2026-11-07 21:00, 2026-12-17 22:00.
4. # Venue guide: Skinner Gallery (Los Angeles) Address: 52880 Burns Creek, Los Angeles. Capacity: 200 people. Accessibility: Step-free access and accessible toilets. Cloakroom available. Food and drinks from outside are not allowed.

## cal-17

**Pregunta:** Is there a minimum age for nightlife in London?

**Respuesta:** Yes, 18+.

**Contextos:**

0. # FAQ: London **When do doors open?** 15 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in London:** in-app chat, 9:00-21:00 local time.
1. # FAQ: Barcelona **When do doors open?** 15 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in Barcelona:** in-app chat, 9:00-21:00 local time.
2. # FAQ: Chicago **When do doors open?** 45 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in Chicago:** in-app chat, 9:00-21:00 local time.
3. # FAQ: Paris **When do doors open?** 45 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in Paris:** in-app chat, 9:00-21:00 local time.
4. # FAQ: Lisbon **When do doors open?** 30 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in Lisbon:** in-app chat, 9:00-21:00 local time.

## cal-18

**Pregunta:** Si me reembolsan una entrada de «Conciertos a la luz de las velas», ¿cuánto tarda en llegar el dinero?

**Respuesta:** Llega al método de pago original en 10-10 días hábiles.

**Contextos:**

0. # Política de reembolsos: Conciertos a la luz de las velas Aplicable a la categoría «Conciertos a la luz de las velas» en todas las ciudades. - Reembolsable: sí, hasta 48 horas antes del inicio. - El importe se devuelve al método de pago original en 5-10 días hábiles. - Los gastos de gestión no son reembolsables. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.
1. # Política de reembolsos: Exposiciones inmersivas Aplicable a la categoría «Exposiciones inmersivas» en todas las ciudades. - Reembolsable: sí, hasta 24 horas antes del inicio. - El importe se devuelve al método de pago original en 5-10 días hábiles. - Los gastos de gestión no son reembolsables. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.
2. # Política de reembolsos: Gastronomía y bebidas Aplicable a la categoría «Gastronomía y bebidas» en todas las ciudades. - Reembolsable: sí, hasta 72 horas antes del inicio. - El importe se devuelve al método de pago original en 5-10 días hábiles. - Los gastos de gestión no son reembolsables. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.
3. # Política de reembolsos: Tours y visitas Aplicable a la categoría «Tours y visitas» en todas las ciudades. - Reembolsable: sí, hasta 24 horas antes del inicio. - El importe se devuelve al método de pago original en 5-10 días hábiles. - Los gastos de gestión no son reembolsables. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.
4. # Política de reembolsos: Ocio nocturno Aplicable a la categoría «Ocio nocturno» en todas las ciudades. - Estas entradas no admiten reembolso. - Se permite un cambio de fecha hasta 24 horas antes, sujeto a disponibilidad. - Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente.

## cal-19

**Pregunta:** Does Rivas Palace in Barcelona have step-free access?

**Respuesta:** Yes: step-free access and accessible toilets.

**Contextos:**

0. # Venue guide: Rivas Palace (Barcelona) Address: Camino de Leopoldo Agudo 66, Barcelona. Capacity: 200 people. Accessibility: Step-free access and accessible toilets. Cloakroom available. Food and drinks from outside are not allowed.
1. # Event: Ghost Walking Tour (Barcelona) Ghost Walking Tour at Rivas Palace, Barcelona. Tours and sightseeing. Category: Tours and sightseeing. Venue: Rivas Palace, Camino de Leopoldo Agudo 66, Barcelona. Price: 22 EUR per ticket. Refunds: Refundable up to 24 hours before the start. Upcoming sessions: 2026-11-27 18:00, 2026-12-21 19:00, 2027-01-16 20:00.
2. # Venue guide: Lobo Palace (Barcelona) Address: Alameda Cristian Bru 61, Barcelona. Capacity: 600 people. Accessibility: Access via stairs only; contact support for assistance. Cloakroom available. Food and drinks from outside are not allowed.
3. # Venue guide: Boezio Palace (Milan) Address: Borgo Tarchetti, 37, Milan. Capacity: 200 people. Accessibility: Step-free access and accessible toilets. Cloakroom available. Food and drinks from outside are not allowed.
4. # Event: Monet: Gardens of Light (Barcelona) Monet: Gardens of Light at Rivas Palace, Barcelona. Immersive exhibitions. Category: Immersive exhibitions. Venue: Rivas Palace, Camino de Leopoldo Agudo 66, Barcelona. Price: 32 EUR per ticket. Refunds: Refundable up to 24 hours before the start. Upcoming sessions: 2026-10-06 22:00, 2026-12-12 20:00, 2027-01-16 20:00.

## cal-20

**Pregunta:** How much is a ticket for 'Sushi Workshop' at Burton Studio in New York?

**Respuesta:** 77 USD per ticket.

**Contextos:**

0. # Event: Sushi Workshop (New York) Sushi Workshop at Burton Studio, New York. Food and drink. Category: Food and drink. Venue: Burton Studio, 166 Rice Plaza Apt. 184, New York. Price: 77 USD per ticket. Refunds: Refundable up to 72 hours before the start. Upcoming sessions: 2026-12-09 22:00, 2027-01-17 21:00, 2027-01-26 21:00.
1. # Event: Hidden Gems Tour (New York) Hidden Gems Tour at Burton Studio, New York. Tours and sightseeing. Category: Tours and sightseeing. Venue: Burton Studio, 166 Rice Plaza Apt. 184, New York. Price: 50 USD per ticket. Refunds: Refundable up to 24 hours before the start. Upcoming sessions: 2026-11-01 20:00, 2026-11-10 22:00, 2026-12-24 19:00.
2. # Venue guide: Burton Studio (New York) Address: 166 Rice Plaza Apt. 184, New York. Capacity: 350 people. Accessibility: Step-free access and accessible toilets. Cloakroom available. Food and drinks from outside are not allowed.
3. # Event: Candlelit Strings: Epic Film Scores (New York) Candlelit Strings: Epic Film Scores at Burton Studio, New York. Candlelit concerts. Category: Candlelit concerts. Venue: Burton Studio, 166 Rice Plaza Apt. 184, New York. Price: 40 USD per ticket. Refunds: Refundable up to 48 hours before the start. Upcoming sessions: 2026-10-09 19:00, 2026-10-10 19:00, 2026-11-09 20:00.
4. # FAQ: New York **When do doors open?** 15 minutes before the start. **Where are my tickets?** In the Headache app, under 'My tickets'. **Do I need to print my ticket?** No, the QR code on your phone is enough. **Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied. **Customer support in New York:** in-app chat, 9:00-21:00 local time.
