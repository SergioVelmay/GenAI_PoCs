"""Contrato entre el workflow de revisión (9.G5) y el dominio, sin nube.

No hay emulador de Workflows: se comprueba que el YAML versionado habla el mismo idioma que
`ReviewHandler` (tipos, campos, estado pendiente, tema) y que la espera tiene SLA. Lo que solo se
ve ejecutándolo (formato real del evento de Eventarc, callback) va a pendientes-cloud.md.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from genaipocs_core.domain import CaseStatus
from orchestrator.async_flow import CASE_OUTCOMES, CASE_REVIEWS, REVIEW_DECISION, REVIEW_SLA_EXPIRED

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / "infra" / "workflows" / "case_review.yaml"
TERRAFORM = ROOT / "infra" / "terraform" / "gcp"


def _steps() -> dict[str, Any]:
    main = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["main"]
    assert main["params"] == ["event"]
    return {name: body for step in main["steps"] for name, body in step.items()}


def test_only_pending_cases_wait_for_a_person() -> None:
    steps = _steps()
    [condition] = steps["only_pending_cases"]["switch"]

    assert f'"{CaseStatus.PENDING_APPROVAL.value}"' in condition["condition"]
    assert "return" in condition
    assert list(steps)[:3] == ["init", "only_pending_cases", "create_callback"]


def test_the_wait_has_an_sla_and_its_timeout_is_not_a_failure() -> None:
    steps = _steps()
    wait = steps["wait_for_decision"]

    assert wait["try"]["call"] == "events.await_callback"
    assert wait["try"]["args"]["timeout"] == "${sla_seconds}"
    [switch] = wait["except"]["steps"][0]["sla_or_error"]["switch"]
    assert '"TimeoutError" in e.tags' in switch["condition"]
    assert switch["next"] == "sla_expired"
    # Cualquier otro error no se disfraza de SLA vencido: se relanza.
    assert wait["except"]["steps"][1]["unexpected"]["raise"] == "${e}"


def test_messages_speak_the_review_handler_language() -> None:
    steps = _steps()
    decision = steps["publish_decision"]["args"]["body"]["messages"][0]["data"]
    expired = steps["sla_expired"]["args"]["body"]["messages"][0]["data"]

    assert f'"kind": "{REVIEW_DECISION}"' in decision
    for field in ("case_id", "reviewer", "approve", "note"):
        assert f'"{field}":' in decision
    assert f'"kind": "{REVIEW_SLA_EXPIRED}"' in expired and '"case_id":' in expired
    for step in ("publish_decision", "sla_expired"):
        assert steps[step]["args"]["topic"] == "${reviews_topic}"


def test_topics_and_sla_come_from_terraform() -> None:
    env = set(re.findall(r'sys\.get_env\("([A-Z_]+)"\)', WORKFLOW.read_text(encoding="utf-8")))
    terraform = (TERRAFORM / "workflows.tf").read_text(encoding="utf-8")
    declared = set(re.findall(r"^\s+([A-Z_]+)\s+=", terraform, flags=re.MULTILINE))

    assert env - {"GOOGLE_CLOUD_PROJECT_ID"} == {
        "REVIEWS_TOPIC",
        "REQUESTS_TOPIC",
        "REVIEW_SLA_SECONDS",
    }
    assert env - {"GOOGLE_CLOUD_PROJECT_ID"} <= declared
    assert f'"{CASE_REVIEWS}"' in (TERRAFORM / "pubsub.tf").read_text(encoding="utf-8")
    assert f'google_pubsub_topic.cases["{CASE_OUTCOMES}"]' in terraform
