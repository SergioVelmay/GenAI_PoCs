"""Genera datos sintéticos del dominio Headache, deterministas por semilla.

Uso:
    uv run python scripts/generate_synthetic_data.py --scale small
    uv run python scripts/generate_synthetic_data.py --scale large   # millones de pedidos (Fase 1)
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Iterable
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, TextIO

from faker import Faker

from headache_core.domain import (
    Customer,
    DocumentType,
    Event,
    EventCategory,
    KnowledgeDocument,
    Money,
    Order,
    OrderStatus,
    Refund,
    RefundStatus,
    Session,
    SupportEmail,
    Venue,
)


@dataclass(frozen=True)
class Scale:
    venues_per_city: int
    events_per_venue: int
    sessions_per_event: int
    customers: int
    orders: int
    emails: int


SCALES = {
    "small": Scale(2, 3, 4, 500, 2_000, 120),
    "medium": Scale(5, 6, 10, 20_000, 100_000, 500),
    "large": Scale(10, 10, 30, 300_000, 3_000_000, 2_000),
}


@dataclass(frozen=True)
class City:
    name: str
    country: str
    currency: str
    language: str
    locale: str


CITIES = [
    City("Madrid", "ES", "EUR", "es", "es_ES"),
    City("Barcelona", "ES", "EUR", "es", "es_ES"),
    City("London", "GB", "GBP", "en", "en_GB"),
    City("Paris", "FR", "EUR", "en", "fr_FR"),
    City("Lisbon", "PT", "EUR", "en", "pt_PT"),
    City("Milan", "IT", "EUR", "en", "it_IT"),
    City("New York", "US", "USD", "en", "en_US"),
    City("Los Angeles", "US", "USD", "en", "en_US"),
    City("Chicago", "US", "USD", "en", "en_US"),
    City("Mexico City", "MX", "MXN", "es", "es_MX"),
]


@dataclass(frozen=True)
class CategoryRules:
    refundable: bool
    refund_window_hours: int
    price_range: tuple[int, int]
    label_es: str
    label_en: str
    titles: tuple[str, ...]


CATEGORY_RULES = {
    EventCategory.CANDLELIT: CategoryRules(
        True,
        48,
        (25, 60),
        "Conciertos a la luz de las velas",
        "Candlelit concerts",
        (
            "Candlelit Strings: 90s Pop Anthems",
            "Candlelit Strings: Vivaldi's Four Seasons",
            "Candlelit Strings: Epic Film Scores",
            "Candlelit Strings: Rock Legends vs Disco Classics",
            "Candlelit Strings: Minimalist Piano",
        ),
    ),
    EventCategory.EXHIBITION: CategoryRules(
        True,
        24,
        (15, 35),
        "Exposiciones inmersivas",
        "Immersive exhibitions",
        (
            "Van Gogh: Starry Rooms",
            "Monet: Gardens of Light",
            "Dinosaurs Alive",
            "Ocean Liners: An Immersive Voyage",
            "Klimt in Gold",
        ),
    ),
    EventCategory.THEATRE: CategoryRules(
        False,
        0,
        (20, 90),
        "Teatro y espectáculos",
        "Theatre and shows",
        ("Office Life: A Musical Comedy", "Murder Mystery Dinner Show", "Comedy Night Live"),
    ),
    EventCategory.FOOD_DRINK: CategoryRules(
        True,
        72,
        (35, 120),
        "Gastronomía y bebidas",
        "Food and drink",
        ("Wine Tasting Experience", "Gin Masterclass", "Cheese & Wine Pairing", "Sushi Workshop"),
    ),
    EventCategory.TOURS: CategoryRules(
        True,
        24,
        (15, 50),
        "Tours y visitas",
        "Tours and sightseeing",
        ("Ghost Walking Tour", "Street Art Tour", "Tapas Food Tour", "Hidden Gems Tour"),
    ),
    EventCategory.NIGHTLIFE: CategoryRules(
        False,
        0,
        (10, 40),
        "Ocio nocturno",
        "Nightlife",
        ("80s Rooftop Party", "Silent Disco", "Latin Night"),
    ),
    EventCategory.SPORTS: CategoryRules(
        False,
        0,
        (20, 80),
        "Deportes y actividades",
        "Sports and activities",
        ("Stadium Tour", "Indoor Climbing Session", "Padel Clinic"),
    ),
}

INTENTS = (
    "refund_request",
    "date_change",
    "event_info",
    "lost_tickets",
    "complaint",
    "partnership",
)

EMAIL_TEMPLATES: dict[str, dict[str, tuple[str, str]]] = {
    "es": {
        "refund_request": (
            "Solicitud de reembolso pedido {order_id}",
            "Hola, soy {name}. Compré {qty} entradas para '{title}' el {date} (pedido {order_id})"
            " y no podré asistir. ¿Podéis devolverme el dinero? Gracias.",
        ),
        "date_change": (
            "Cambio de fecha",
            "Buenas, ¿es posible cambiar mis entradas del pedido {order_id} para '{title}'"
            " a otra sesión? La del {date} me viene mal. Un saludo, {name}.",
        ),
        "event_info": (
            "Duda sobre {title}",
            "Hola, ¿a qué hora abren las puertas para '{title}' en {city}? ¿Es accesible"
            " para silla de ruedas? Gracias.",
        ),
        "lost_tickets": (
            "No encuentro mis entradas",
            "Hola, compré entradas (pedido {order_id}) para '{title}' pero no aparecen en la app."
            " El evento es el {date}. ¿Qué hago? {name}",
        ),
        "complaint": (
            "Queja sobre el evento",
            "Ayer estuve en '{title}' en {city} y el sonido fue muy malo y empezó con 40 minutos"
            " de retraso. Quiero poner una reclamación. Pedido {order_id}.",
        ),
        "partnership": (
            "Propuesta de colaboración",
            "Hola, somos {company}, promotores de eventos en {city}. Nos gustaría publicar"
            " nuestros espectáculos en Headache. ¿Con quién podemos hablar?",
        ),
    },
    "en": {
        "refund_request": (
            "Refund request for order {order_id}",
            "Hi, I'm {name}. I bought {qty} tickets for '{title}' on {date} (order {order_id})"
            " but I can't make it anymore. Could I get a refund? Thanks.",
        ),
        "date_change": (
            "Change of date",
            "Hello, could I move my tickets from order {order_id} for '{title}' to a different"
            " session? {date} doesn't work for me anymore. Best, {name}.",
        ),
        "event_info": (
            "Question about {title}",
            "Hi, what time do doors open for '{title}' in {city}? Is the venue wheelchair"
            " accessible? Thanks.",
        ),
        "lost_tickets": (
            "Can't find my tickets",
            "Hi, I bought tickets (order {order_id}) for '{title}' but they don't show up in"
            " the app. The event is on {date}. What should I do? {name}",
        ),
        "complaint": (
            "Complaint about the event",
            "I attended '{title}' in {city} yesterday. The sound was terrible and it started"
            " 40 minutes late. I want to file a complaint. Order {order_id}.",
        ),
        "partnership": (
            "Partnership proposal",
            "Hello, we are {company}, an event promoter based in {city}. We'd love to list"
            " our shows on Headache. Who should we talk to?",
        ),
    },
}


class HeadacheDataGenerator:
    def __init__(self, scale: Scale, seed: int, now: datetime) -> None:
        self.scale = scale
        self.now = now
        self.rng = random.Random(seed)
        Faker.seed(seed)
        self.fake = Faker(sorted({c.locale for c in CITIES}))
        self.venues: list[Venue] = []
        self.events: list[Event] = []
        self.sessions: list[Session] = []
        self.customers: list[Customer] = []
        self.door_minutes = {c.name: self.rng.choice((15, 30, 45)) for c in CITIES}

    def build_catalog(self) -> None:
        for city in CITIES:
            for _ in range(self.scale.venues_per_city):
                venue = Venue(
                    id=f"ven_{len(self.venues) + 1:05d}",
                    name=f"{self.fake[city.locale].last_name()} "
                    f"{self.rng.choice(('Hall', 'Palace', 'Studio', 'Gallery', 'Rooftop'))}",
                    city=city.name,
                    country=city.country,
                    address=self.fake[city.locale].street_address(),
                    capacity=self.rng.choice((80, 120, 200, 350, 600, 1200)),
                )
                self.venues.append(venue)
                for _ in range(self.scale.events_per_venue):
                    self._add_event(city, venue)

    def _add_event(self, city: City, venue: Venue) -> None:
        category = self.rng.choice(list(EventCategory))
        rules = CATEGORY_RULES[category]
        title = self.rng.choice(rules.titles)
        event = Event(
            id=f"evt_{len(self.events) + 1:06d}",
            title=title,
            category=category,
            city=city.name,
            venue_id=venue.id,
            description=f"{title} at {venue.name}, {city.name}. {rules.label_en}.",
            price=Money(Decimal(self.rng.randint(*rules.price_range)), city.currency),
            refundable=rules.refundable,
            refund_window_hours=rules.refund_window_hours,
        )
        self.events.append(event)
        for _ in range(self.scale.sessions_per_event):
            starts_at = (self.now + timedelta(days=self.rng.randint(-30, 120))).replace(
                hour=self.rng.choice((18, 19, 20, 21, 22)), minute=0, second=0, microsecond=0
            )
            self.sessions.append(
                Session(
                    id=f"ses_{len(self.sessions) + 1:07d}",
                    event_id=event.id,
                    starts_at=starts_at,
                    capacity=venue.capacity,
                    available=self.rng.randint(0, venue.capacity),
                )
            )

    def build_customers(self) -> None:
        for i in range(self.scale.customers):
            city = self.rng.choice(CITIES)
            faker = self.fake[city.locale]
            self.customers.append(
                Customer(
                    id=f"cus_{i + 1:07d}",
                    full_name=faker.name(),
                    email=f"user{i + 1}@example.com",
                    country=city.country,
                    language=city.language,
                )
            )

    def write_orders(self, orders_fh: TextIO, refunds_fh: TextIO) -> list[Order]:
        events = {e.id: e for e in self.events}
        sample_every = max(1, self.scale.orders // (self.scale.emails * 4))
        sample: list[Order] = []
        refunds = 0
        for i in range(self.scale.orders):
            session = self.rng.choice(self.sessions)
            event = events[session.event_id]
            quantity = self.rng.randint(1, 6)
            roll = self.rng.random()
            status = (
                OrderStatus.REFUNDED
                if roll < 0.05
                else OrderStatus.CANCELLED
                if roll < 0.07
                else OrderStatus.PAID
            )
            order = Order(
                id=f"ord_{i + 1:08d}",
                customer_id=self.rng.choice(self.customers).id,
                session_id=session.id,
                quantity=quantity,
                total=event.price * quantity,
                status=status,
                # Un pedido nunca se crea en el futuro respecto a la fecha de referencia.
                created_at=min(session.starts_at, self.now)
                - timedelta(hours=self.rng.randint(72, 60 * 24)),
            )
            _write_row(orders_fh, order)
            if status is OrderStatus.REFUNDED or (status is OrderStatus.PAID and roll > 0.98):
                refunds += 1
                _write_row(refunds_fh, self._refund_for(order, refunds))
            if i % sample_every == 0:
                sample.append(order)
        return sample

    def _refund_for(self, order: Order, number: int) -> Refund:
        approved = order.status is OrderStatus.REFUNDED
        return Refund(
            id=f"ref_{number:07d}",
            order_id=order.id,
            amount=order.total if approved else Money(Decimal("0"), order.total.currency),
            reason=self.rng.choice(("No puedo asistir", "Enfermedad", "Cambio de planes")),
            status=RefundStatus.APPROVED if approved else RefundStatus.REJECTED,
            requested_at=order.created_at + timedelta(hours=self.rng.randint(1, 48)),
        )

    def build_emails(self, orders: list[Order]) -> list[dict[str, Any]]:
        sessions = {s.id: s for s in self.sessions}
        events = {e.id: e for e in self.events}
        customers = {c.id: c for c in self.customers}
        rows: list[dict[str, Any]] = []
        for i in range(self.scale.emails):
            order = self.rng.choice(orders)
            customer = customers[order.customer_id]
            session = sessions[order.session_id]
            event = events[session.event_id]
            intent = self.rng.choice(INTENTS)
            language = customer.language if customer.language in EMAIL_TEMPLATES else "en"
            subject, body = EMAIL_TEMPLATES[language][intent]
            values = {
                "name": customer.full_name,
                "qty": order.quantity,
                "title": event.title,
                "date": f"{session.starts_at:%Y-%m-%d}",
                "order_id": order.id,
                "city": event.city,
                "company": self.fake["en_US"].company(),
            }
            email = SupportEmail(
                id=f"eml_{i + 1:06d}",
                customer_id=customer.id,
                language=language,
                subject=subject.format(**values),
                body=body.format(**values),
                received_at=self.now - timedelta(minutes=self.rng.randint(1, 60 * 24 * 7)),
                order_id=None if intent in ("event_info", "partnership") else order.id,
            )
            rows.append({**asdict(email), "expected_intent": intent})
        return rows

    def build_knowledge(self) -> list[KnowledgeDocument]:
        docs: list[KnowledgeDocument] = []
        for category, rules in CATEGORY_RULES.items():
            for language in ("es", "en"):
                docs.append(_policy_doc(category, rules, language))
        for city in CITIES:
            for language in ("es", "en"):
                docs.append(_city_faq_doc(city, self.door_minutes[city.name], language))
        for venue in self.venues:
            docs.append(_venue_doc(venue, self.rng.choice((True, False))))
        # Sin usar el RNG: añadir fichas no altera el resto de datos de la misma semilla.
        venues = {v.id: v for v in self.venues}
        sessions: dict[str, list[Session]] = {}
        for session in self.sessions:
            sessions.setdefault(session.event_id, []).append(session)
        for event in self.events:
            upcoming = sorted(s.starts_at for s in sessions[event.id] if s.starts_at > self.now)
            docs.append(_event_doc(event, venues[event.venue_id], upcoming[:3]))
        return docs


def _policy_doc(category: EventCategory, rules: CategoryRules, language: str) -> KnowledgeDocument:
    if language == "es":
        title = f"Política de reembolsos: {rules.label_es}"
        terms = (
            f"- Reembolsable: sí, hasta {rules.refund_window_hours} horas antes del inicio.\n"
            "- El importe se devuelve al método de pago original en 5-10 días hábiles.\n"
            "- Los gastos de gestión no son reembolsables."
            if rules.refundable
            else "- Estas entradas no admiten reembolso.\n"
            "- Se permite un cambio de fecha hasta 24 horas antes, sujeto a disponibilidad."
        )
        content = (
            f"# {title}\n\nAplicable a la categoría «{rules.label_es}» en todas las ciudades.\n\n"
            f"{terms}\n"
            "- Si el organizador cancela la sesión, se reembolsa el 100 % automáticamente."
        )
    else:
        title = f"Refund policy: {rules.label_en}"
        terms = (
            f"- Refundable: yes, up to {rules.refund_window_hours} hours before the start.\n"
            "- Money is returned to the original payment method within 5-10 business days.\n"
            "- Booking fees are non-refundable."
            if rules.refundable
            else "- These tickets are non-refundable.\n"
            "- One date change is allowed up to 24 hours before, subject to availability."
        )
        content = (
            f"# {title}\n\nApplies to the '{rules.label_en}' category in every city.\n\n"
            f"{terms}\n"
            "- If the organiser cancels the session, a full refund is issued automatically."
        )
    return KnowledgeDocument(
        id=f"policy-{category}-{language}",
        title=title,
        doc_type=DocumentType.POLICY,
        language=language,
        content=content,
        metadata={"category": category.value},
    )


def _city_faq_doc(city: City, door_minutes: int, language: str) -> KnowledgeDocument:
    if language == "es":
        title = f"Preguntas frecuentes: {city.name}"
        content = (
            f"# {title}\n\n"
            f"**¿Cuándo abren las puertas?** {door_minutes} minutos antes del inicio.\n\n"
            "**¿Dónde están mis entradas?** En la app de Headache, sección «Mis entradas».\n\n"
            "**¿Necesito imprimir la entrada?** No, basta con el código QR en el móvil.\n\n"
            "**¿Hay edad mínima?** Ocio nocturno: 18+. Resto: menores acompañados.\n\n"
            f"**Atención al cliente en {city.name}:** chat en la app, de 9:00 a 21:00 hora local."
        )
    else:
        title = f"FAQ: {city.name}"
        content = (
            f"# {title}\n\n"
            f"**When do doors open?** {door_minutes} minutes before the start.\n\n"
            "**Where are my tickets?** In the Headache app, under 'My tickets'.\n\n"
            "**Do I need to print my ticket?** No, the QR code on your phone is enough.\n\n"
            "**Is there a minimum age?** Nightlife: 18+. Others: minors must be accompanied.\n\n"
            f"**Customer support in {city.name}:** in-app chat, 9:00-21:00 local time."
        )
    return KnowledgeDocument(
        id=f"faq-{city.name.lower().replace(' ', '-')}-{language}",
        title=title,
        doc_type=DocumentType.FAQ,
        language=language,
        content=content,
        metadata={"city": city.name, "country": city.country},
    )


def _venue_doc(venue: Venue, step_free: bool) -> KnowledgeDocument:
    access = (
        "Step-free access and accessible toilets."
        if step_free
        else ("Access via stairs only; contact support for assistance.")
    )
    title = f"Venue guide: {venue.name} ({venue.city})"
    content = (
        f"# {title}\n\nAddress: {venue.address}, {venue.city}.\n\n"
        f"Capacity: {venue.capacity} people.\n\nAccessibility: {access}\n\n"
        "Cloakroom available. Food and drinks from outside are not allowed."
    )
    return KnowledgeDocument(
        id=f"venue-{venue.id}",
        title=title,
        doc_type=DocumentType.VENUE_GUIDE,
        language="en",
        content=content,
        metadata={"city": venue.city, "venue_id": venue.id},
    )


def _event_doc(event: Event, venue: Venue, upcoming: list[datetime]) -> KnowledgeDocument:
    rules = CATEGORY_RULES[event.category]
    refunds = (
        f"Refundable up to {event.refund_window_hours} hours before the start."
        if event.refundable
        else "Non-refundable; one date change allowed up to 24 hours before."
    )
    dates = ", ".join(f"{d:%Y-%m-%d %H:%M}" for d in upcoming) or "no upcoming sessions"
    title = f"Event: {event.title} ({event.city})"
    content = (
        f"# {title}\n\n{event.description}\n\n"
        f"Category: {rules.label_en}. Venue: {venue.name}, {venue.address}, {event.city}.\n\n"
        f"Price: {event.price.amount} {event.price.currency} per ticket.\n\n"
        f"Refunds: {refunds}\n\nUpcoming sessions: {dates}."
    )
    return KnowledgeDocument(
        id=f"event-{event.id}",
        title=title,
        doc_type=DocumentType.EVENT_SHEET,
        language="en",
        content=content,
        metadata={
            "city": event.city,
            "category": event.category.value,
            "venue_id": venue.id,
            "event_id": event.id,
        },
    )


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Tipo no serializable: {type(value).__name__}")


def _write_row(fh: TextIO, row: object) -> None:
    data = asdict(row) if is_dataclass(row) and not isinstance(row, type) else row
    fh.write(json.dumps(data, default=_json_default, ensure_ascii=False) + "\n")


def _write_jsonl(path: Path, rows: Iterable[object]) -> int:
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            _write_row(fh, row)
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", choices=SCALES, default="small")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data/synthetic"))
    parser.add_argument("--reference-date", default="2026-10-01", help="'Hoy' para los datos")
    args = parser.parse_args()

    now = datetime.fromisoformat(args.reference_date).replace(tzinfo=UTC)
    out: Path = args.out
    knowledge_dir = out / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    gen = HeadacheDataGenerator(SCALES[args.scale], seed=args.seed, now=now)
    gen.build_catalog()
    gen.build_customers()

    counts = {
        "venues": _write_jsonl(out / "venues.jsonl", gen.venues),
        "events": _write_jsonl(out / "events.jsonl", gen.events),
        "sessions": _write_jsonl(out / "sessions.jsonl", gen.sessions),
        "customers": _write_jsonl(out / "customers.jsonl", gen.customers),
    }
    with (
        (out / "orders.jsonl").open("w", encoding="utf-8") as orders_fh,
        (out / "refunds.jsonl").open("w", encoding="utf-8") as refunds_fh,
    ):
        sample = gen.write_orders(orders_fh, refunds_fh)
    counts["orders"] = gen.scale.orders
    counts["support_emails"] = _write_jsonl(out / "support_emails.jsonl", gen.build_emails(sample))

    documents = gen.build_knowledge()
    counts["knowledge_documents"] = _write_jsonl(knowledge_dir / "documents.jsonl", documents)
    for doc in documents:
        (knowledge_dir / f"{doc.id}.md").write_text(doc.content, encoding="utf-8")

    for name, count in counts.items():
        print(f"{name:>20}: {count:,}")
    print(f"Datos escritos en {out.resolve()}")


if __name__ == "__main__":
    main()
