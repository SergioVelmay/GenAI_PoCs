"""Adaptador de OpenTelemetry para el puerto de telemetría (fase 11).

El proceso exporta por OTLP/HTTP a **un** collector y no sabe nada de los backends: el collector
reparte a Jaeger, Prometheus y Langfuse en local, y a App Insights (Azure Monitor) o X-Ray /
CloudWatch (ADOT) en la nube. Cambiar de backend es cambiar la configuración del collector, no
el código.

El proveedor se registra también como global de OTel para que las instrumentaciones de terceros
(FastAPI, httpx, MAF, Strands) cuelguen sus spans de la misma traza.
"""

from __future__ import annotations

import traceback
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any, Literal

from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Status, StatusCode

from headache_core.application.telemetry import (
    Redactor,
    configure_logging,
    get_telemetry,
    redact_text,
    set_telemetry,
)
from headache_core.config import Settings, TelemetrySettings
from headache_core.ports.telemetry import (
    Attributes,
    AttributeValue,
    SpanKind,
    SpanPort,
    TelemetryPort,
    TraceCarrier,
    TraceIds,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

_KINDS = {
    SpanKind.INTERNAL: trace.SpanKind.INTERNAL,
    SpanKind.SERVER: trace.SpanKind.SERVER,
    SpanKind.CLIENT: trace.SpanKind.CLIENT,
    SpanKind.PRODUCER: trace.SpanKind.PRODUCER,
    SpanKind.CONSUMER: trace.SpanKind.CONSUMER,
}
INSTRUMENTATION_SCOPE = "headache"
PROBES = "/health,/ready"
# Un span por cada `receive`/`send` del ASGI: en un stream SSE serían cientos por petición.
QUIET_SPANS: list[Literal["receive", "send"]] = ["receive", "send"]


# `Any`: el `AttributeValue` de OTel es una variable `Union`, no un alias que mypy acepte.
def _value(value: AttributeValue) -> Any:
    if isinstance(value, str | bool | int | float):
        return value
    return list(value)


def _attributes(attributes: Attributes | None) -> dict[str, Any]:
    return {key: _value(value) for key, value in (attributes or {}).items()}


def _links(carriers: Sequence[TraceCarrier]) -> list[trace.Link]:
    links = []
    for carrier in carriers:
        context = trace.get_current_span(propagate.extract(dict(carrier))).get_span_context()
        if context.is_valid:
            links.append(trace.Link(context))
    return links


class OTelSpan:
    def __init__(self, span: trace.Span, redact: Redactor) -> None:
        self._span = span
        self._redact = redact

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        self._span.set_attribute(key, _value(value))

    def set_attributes(self, attributes: Attributes) -> None:
        self._span.set_attributes(_attributes(attributes))

    def update_name(self, name: str) -> None:
        self._span.update_name(name)

    def add_event(self, name: str, attributes: Attributes | None = None) -> None:
        self._span.add_event(name, _attributes(attributes))

    def record_error(self, error: BaseException) -> None:
        # Como `record_exception`, pero con el mensaje y la pila redactados: un error de
        # validación puede llevar el email o el teléfono que venía en la petición.
        error_type = type(error).__name__
        stack = "".join(traceback.format_exception(error))
        self._span.add_event(
            "exception",
            {
                "exception.type": error_type,
                "exception.message": self._redact(str(error)),
                "exception.stacktrace": self._redact(stack),
            },
        )
        self._span.set_status(Status(StatusCode.ERROR, error_type))

    def mark_error(self, error_type: str) -> None:
        self._span.set_attribute("error.type", error_type)
        self._span.set_status(Status(StatusCode.ERROR, error_type))

    def end(self) -> None:
        self._span.end()


class OTelTelemetry:
    def __init__(
        self,
        tracer: trace.Tracer,
        *,
        redact: Redactor = redact_text,
        capture_content: bool = False,
    ) -> None:
        self._tracer = tracer
        self._redact = redact
        self._capture_content = capture_content

    def span(
        self,
        name: str,
        *,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Attributes | None = None,
        parent: TraceCarrier | None = None,
        links: Sequence[TraceCarrier] = (),
    ) -> AbstractContextManager[SpanPort]:
        return self._current_span(name, kind, attributes, parent, links)

    @contextmanager
    def _current_span(
        self,
        name: str,
        kind: SpanKind,
        attributes: Attributes | None,
        parent: TraceCarrier | None,
        links: Sequence[TraceCarrier],
    ) -> Iterator[SpanPort]:
        # Un carrier vacío o inválido (mensaje de antes de la fase 11) no rompe nada: el span
        # empieza una traza nueva.
        context = propagate.extract(dict(parent)) if parent else None
        with self._tracer.start_as_current_span(
            name,
            context=context,
            kind=_KINDS[kind],
            attributes=_attributes(attributes),
            links=_links(links),
            record_exception=False,
            set_status_on_exception=False,
        ) as raw:
            span = OTelSpan(raw, self._redact)
            try:
                yield span
            except BaseException as exc:
                span.record_error(exc)
                raise

    def start_span(
        self,
        name: str,
        *,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Attributes | None = None,
    ) -> SpanPort:
        raw = self._tracer.start_span(name, kind=_KINDS[kind], attributes=_attributes(attributes))
        return OTelSpan(raw, self._redact)

    def current_ids(self) -> TraceIds | None:
        context = trace.get_current_span().get_span_context()
        if not context.is_valid:
            return None
        return TraceIds(
            trace_id=trace.format_trace_id(context.trace_id),
            span_id=trace.format_span_id(context.span_id),
        )

    def inject(self) -> dict[str, str]:
        carrier: dict[str, str] = {}
        propagate.inject(carrier)
        return carrier

    def current_span(self) -> SpanPort:
        return OTelSpan(trace.get_current_span(), self._redact)

    def content(self, text: str) -> str | None:
        return self._redact(text) if self._capture_content else None


def _service_version() -> str:
    try:
        return version("headache-core")
    except PackageNotFoundError:  # pragma: no cover - siempre instalado en el monorepo
        return "0.0.0"


def build_tracer_provider(
    settings: TelemetrySettings,
    *,
    service_name: str,
    environment: str,
    exporters: Sequence[SpanExporter] | None = None,
) -> TracerProvider:
    """Proveedor con el recurso del servicio, muestreo en la cabeza y exportación por lotes.

    `exporters` sustituye a los de la configuración (tests con `InMemorySpanExporter`).
    """
    resource = Resource.create(
        {
            "service.name": settings.service_name or service_name,
            "service.namespace": "headache",
            "service.version": _service_version(),
            "deployment.environment.name": environment,
        }
    )
    provider = TracerProvider(
        resource=resource, sampler=ParentBased(TraceIdRatioBased(settings.sample_ratio))
    )
    if exporters is None:
        exporters = _configured_exporters(settings)
    for exporter in exporters:
        provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


def _configured_exporters(settings: TelemetrySettings) -> list[SpanExporter]:
    if settings.exporter == "console":
        return [ConsoleSpanExporter()]
    if settings.exporter == "otlp":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        return [OTLPSpanExporter(endpoint=f"{settings.otlp_endpoint.rstrip('/')}/v1/traces")]
    return []


def configure_telemetry(
    settings: Settings,
    *,
    service_name: str,
    redact: Redactor = redact_text,
) -> TelemetryPort:
    """Arranque de un servicio: fija la telemetría global (de OTel y la nuestra) y la devuelve.

    Con `TELEMETRY__EXPORTER=none` (por defecto) no se toca nada: se queda la que hubiera
    (`NullTelemetry`, o la de un test que graba spans en memoria).
    """
    if settings.telemetry.exporter == "none":
        return get_telemetry()
    # OTel solo admite fijar el proveedor global una vez por proceso: si ya hay uno (dos apps
    # en el mismo proceso), se reutiliza.
    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        provider = current
    else:
        provider = build_tracer_provider(
            settings.telemetry, service_name=service_name, environment=settings.environment
        )
        trace.set_tracer_provider(provider)
    telemetry = OTelTelemetry(
        provider.get_tracer(INSTRUMENTATION_SCOPE),
        redact=redact,
        capture_content=settings.telemetry.capture_content,
    )
    set_telemetry(telemetry)
    return telemetry


def setup_observability(
    settings: Settings,
    *,
    service_name: str,
    redact: Redactor = redact_text,
) -> TelemetryPort:
    """Todo lo de un proceso: telemetría, logs JSON (si se piden) y propagación por httpx.

    Con la configuración por defecto (`none` y logs de texto) no toca nada: los tests y los
    scripts siguen como estaban.
    """
    if settings.telemetry.log_format == "json":
        configure_logging(
            service=settings.telemetry.service_name or service_name,
            log_format="json",
            level=settings.telemetry.log_level,
            redact=redact,
        )
    telemetry = configure_telemetry(settings, service_name=service_name, redact=redact)
    if settings.telemetry.exporter != "none":
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        # Cada petición saliente (MCP, A2A, ticketing) lleva `traceparent` y tiene su span.
        instrumentor = HTTPXClientInstrumentor()
        if not instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.instrument()
    return telemetry


def instrument_fastapi(app: FastAPI, settings: Settings) -> None:
    """Span SERVER por petición (con la ruta, no la URL) y lectura del `traceparent` entrante.

    Sin sondas: `/health` y `/ready` se llaman cada pocos segundos y solo meterían ruido.
    """
    if settings.telemetry.exporter == "none":
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls=PROBES, exclude_spans=QUIET_SPANS)


@dataclass(frozen=True, slots=True)
class RecordedTelemetry:
    telemetry: OTelTelemetry
    exporter: InMemorySpanExporter
    provider: TracerProvider

    def spans(self) -> dict[str, ReadableSpan]:
        """Spans terminados por nombre (si se repite un nombre, el último)."""
        return {span.name: span for span in self.exporter.get_finished_spans()}

    def tree(self, *, attributes: Sequence[str] = ()) -> str:
        """Los spans como árbol de texto (padre → hijos por orden de inicio), una traza tras
        otra: lo que pintaría Jaeger, para tests y para el doc."""
        spans = sorted(self.exporter.get_finished_spans(), key=lambda s: s.start_time or 0)
        ids = {s.context.span_id for s in spans}
        children: dict[int | None, list[ReadableSpan]] = {}
        for span in spans:
            parent = span.parent.span_id if span.parent else None
            children.setdefault(parent if parent in ids else None, []).append(span)
        lines: list[str] = []

        def walk(span: ReadableSpan, depth: int) -> None:
            kind = span.kind.name.lower()
            values = span.attributes or {}
            extra = [key + "=" + str(values[key]) for key in attributes if key in values]
            error = " ERROR" if span.status.status_code is StatusCode.ERROR else ""
            trace_id = trace.format_trace_id(span.context.trace_id)[:8]
            line = f"{'  ' * depth}{span.name} [{kind}] trace={trace_id}{error}"
            lines.append(" ".join([line, *extra]))
            for child in children.get(span.context.span_id, []):
                walk(child, depth + 1)

        for root in children.get(None, []):
            walk(root, 0)
        return "\n".join(lines)


@contextmanager
def recording_telemetry(*, capture_content: bool = False) -> Iterator[RecordedTelemetry]:
    """Telemetría global que guarda los spans en memoria (tests y demos sin collector)."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry = OTelTelemetry(provider.get_tracer("test"), capture_content=capture_content)
    previous = get_telemetry()
    set_telemetry(telemetry)
    try:
        yield RecordedTelemetry(telemetry, exporter, provider)
    finally:
        set_telemetry(previous)
        provider.shutdown()


def shutdown_telemetry() -> None:
    """Vacía los lotes pendientes al parar el proceso (si no, se pierden los últimos spans)."""
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.shutdown()
