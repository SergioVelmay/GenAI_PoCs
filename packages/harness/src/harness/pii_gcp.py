"""Detector de PII con Sensitive Data Protection (GCP, antes Cloud DLP), fase 8.G2.

Usa el extra `gcp` de `genaipocs_core` (instalado por defecto en el workspace).

Aporta lo mismo que Presidio (nombres de persona además de los patrones con checksum) sin
instalar spaCy, a cambio de una llamada de red por texto: `inspect_content` con los infoTypes
de `DEFAULT_INFO_TYPES` (o una plantilla de inspección de `model_armor.tf`, 8.G3) procesado en
la región de `parent` (residencia de datos). Se traducen los infoTypes a los nombres de entidad
de Presidio, así el marcador que ve el modelo (`<ES_NIF>`, `<PERSON>`) no depende del detector.

- **Posiciones en *codepoints***, no en bytes: un `str` de Python se indexa por *codepoint*, y
  con `byte_range` cualquier tilde anterior desplazaría la redacción.
- **Sin idioma**: SDP no recibe idioma; `language` se ignora.
- **Sin texto de vuelta** (`include_quote=False`): solo las posiciones; el dato no viaja dos veces.
- Si el servicio falla, `PiiUnavailableError`: `pii_filter` cae al detector de patrones (el
  suelo determinista) y lo marca con `pii_fallback`, en vez de mandar el texto sin redactar o
  tumbar el turno.

Coste (21-11-2026): 3 $/GB inspeccionado, 1 GB gratis al mes y un mínimo de 1 KB por llamada
(un mensaje corto cuenta como 1 KB). La documentación desaconseja `PERSON_NAME` donde la
latencia importa: se mide en la nube.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol

from google.api_core.exceptions import GoogleAPIError
from google.cloud.dlp_v2 import (
    ContentItem,
    InfoType,
    InspectConfig,
    InspectContentRequest,
    InspectContentResponse,
    Likelihood,
)

from harness.pii import PiiSpan, PiiUnavailableError

# infoType de SDP → entidad de Presidio (las que no están aquí conservan su nombre).
ENTITIES = {
    "PERSON_NAME": "PERSON",
    "CREDIT_CARD_NUMBER": "CREDIT_CARD",
    "SPAIN_NIF_NUMBER": "ES_NIF",
    "SPAIN_DNI_NUMBER": "ES_NIF",
    "SPAIN_NIE_NUMBER": "ES_NIE",
}
# Lo mismo que `pii_presidio.DEFAULT_ENTITIES`: sin ubicaciones ni fechas (datos de negocio).
DEFAULT_INFO_TYPES = (
    "PERSON_NAME",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD_NUMBER",
    "IBAN_CODE",
    "SPAIN_NIF_NUMBER",
    "SPAIN_DNI_NUMBER",
    "SPAIN_NIE_NUMBER",
    "IP_ADDRESS",
)
SCORES = {
    Likelihood.VERY_LIKELY: 1.0,
    Likelihood.LIKELY: 0.8,
    Likelihood.POSSIBLE: 0.6,
    Likelihood.UNLIKELY: 0.4,
    Likelihood.VERY_UNLIKELY: 0.2,
}


type MinLikelihood = Literal["POSSIBLE", "LIKELY", "VERY_LIKELY"]


class DlpClient(Protocol):
    def inspect_content(
        self, *, request: InspectContentRequest, timeout: float
    ) -> InspectContentResponse: ...


class SensitiveDataProtectionPiiDetector:
    def __init__(
        self,
        client: DlpClient,
        *,
        parent: str,
        info_types: Sequence[str] = DEFAULT_INFO_TYPES,
        # Nombre del enum `Likelihood` (proto-plus lo acepta como texto).
        min_likelihood: MinLikelihood = "LIKELY",
        inspect_template: str = "",
        timeout_seconds: float = 5.0,
    ) -> None:
        self._client = client
        # `projects/<proyecto>/locations/<región>`: la región donde SDP procesa el texto.
        self._parent = parent
        self._template = inspect_template
        self._timeout = timeout_seconds
        if inspect_template:
            # La plantilla manda (infoTypes y verosimilitud); aquí solo que no devuelva el dato.
            self._config = InspectConfig(include_quote=False)
        else:
            self._config = InspectConfig(
                info_types=[InfoType(name=name) for name in info_types],
                min_likelihood=min_likelihood,
                include_quote=False,
            )

    def analyze(self, text: str, language: str) -> list[PiiSpan]:
        if not text.strip():
            return []
        request = InspectContentRequest(
            parent=self._parent,
            item=ContentItem(value=text),
            inspect_config=self._config,
            inspect_template_name=self._template,
        )
        try:
            response = self._client.inspect_content(request=request, timeout=self._timeout)
        except GoogleAPIError as exc:
            raise PiiUnavailableError(f"Sensitive Data Protection: {exc}") from exc
        result = response.result
        if result.findings_truncated:
            # Con hallazgos de más (límite del servicio), redactar solo algunos dejaría PII.
            raise PiiUnavailableError("Sensitive Data Protection truncó los hallazgos.")
        spans: list[PiiSpan] = []
        for finding in result.findings:
            span = finding.location.codepoint_range
            if not 0 <= span.start < span.end <= len(text):
                raise PiiUnavailableError(
                    "Sensitive Data Protection devolvió una posición inválida."
                )
            name = finding.info_type.name
            spans.append(
                PiiSpan(
                    ENTITIES.get(name, name),
                    span.start,
                    span.end,
                    SCORES.get(finding.likelihood, 0.5),
                )
            )
        return spans


def location_parent(project: str, location: str) -> str:
    return f"projects/{project}/locations/{location}"
