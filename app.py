"""Triagem de tickets usando o modelo de decisão Clef da Cloudflare.

O Clef recebe o estado do ticket e perguntas tipadas, retornando probabilidades
por alternativa. A aplicação valida essas respostas e aplica a política de
roteamento sem permitir que o modelo execute efeitos externos.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from traffic import DEFAULT_DESTINATION, DEFAULT_ORIGIN, GoogleMapsError, get_traffic_route


DEFAULT_MODEL = "@cf/cloudflare/clef"
DEPARTMENTS = {"billing", "technical", "sales", "trust_safety"}
URGENCIES = ["baixa", "média", "alta", "crítica"]

CLEF_QUESTIONS = {
    "urgent": {
        "type": "noul",
        "instructions": "O ticket exige tratamento urgente agora?",
    },
    "department": {
        "type": "choice",
        "instructions": "Qual equipe deve tratar este ticket?",
        "criteria": {
            "billing": "Cobranças, faturas, pagamentos e reembolsos",
            "technical": "Erros, indisponibilidade, integrações e configuração",
            "sales": "Planos, contratação, upgrade e negociação comercial",
            "trust_safety": "Abuso, fraude, phishing, segurança e privacidade",
        },
    },
    "urgency": {
        "type": "choice",
        "instructions": "Qual é a urgência operacional deste ticket?",
        "criteria": {
            "baixa": "Pode aguardar sem impacto relevante",
            "média": "Precisa de resposta em horário comercial",
            "alta": "Há impacto importante ou prazo curto",
            "crítica": "Incidente grave, risco financeiro/legal ou indisponibilidade ampla",
        },
    },
    "needs_human": {
        "type": "noul",
        "instructions": "É necessária revisão ou atendimento humano?",
    },
    "is_spam": {
        "type": "noul",
        "instructions": "Há sinais claros de spam, golpe, phishing ou abuso?",
    },
    "severity": {
        "type": "score",
        "instructions": "Qual é a gravidade do impacto para o cliente?",
        "criteria": ["Sem impacto", "Baixo", "Relevante", "Grave"],
    },
}


class WorkersAIError(RuntimeError):
    pass


TRAFFIC_ACTIONS = {"monitorar", "alertar", "despachar", "redirecionar"}


def _traffic_questions(critical_points: list[dict]) -> dict:
    point_criteria = {
        point["id"]: (
            f"Trecho {point['id']}: severidade {point['severity']}, "
            f"velocidade {point['speed']}, coordenadas "
            f"{point['latitude']},{point['longitude']}"
        )
        for point in critical_points
    }
    point_criteria["none"] = "Nenhum ponto com trânsito lento ou congestionamento"
    return {
        "criticality": {
            "type": "score",
            "instructions": "Qual é a criticidade geral do trânsito nesta rota?",
            "criteria": ["normal", "moderada", "alta", "crítica"],
        },
        "most_critical_point": {
            "type": "choice",
            "instructions": "Qual ponto deve ser priorizado para intervenção?",
            "criteria": point_criteria,
        },
        "recommended_action": {
            "type": "choice",
            "instructions": "Qual ação operacional é mais adequada agora?",
            "criteria": {
                "monitorar": "Acompanhar sem intervenção imediata",
                "alertar": "Emitir alerta para motoristas e operação",
                "despachar": "Enviar equipe para verificar ou atuar no trecho",
                "redirecionar": "Recomendar rota alternativa para reduzir impacto",
            },
        },
        "prioritize_intervention": {
            "type": "noul",
            "instructions": "A situação requer intervenção operacional imediata?",
        },
    }


def normalize_traffic_clef_result(result: dict, point_ids: set[str]) -> dict:
    answers = result.get("answers") if isinstance(result, dict) else None
    if not isinstance(answers, dict):
        raise WorkersAIError("Cloudflare não devolveu as respostas de trânsito do Clef.")

    criticality = _answer(answers, "criticality")
    if criticality.get("type") != "score" or not isinstance(criticality.get("score"), (int, float)):
        raise WorkersAIError("Resposta Clef inválida para 'criticality'.")
    most_critical_point, point_confidence, point_probabilities = _choice_answer(
        answers, "most_critical_point", point_ids | {"none"}
    )
    recommended_action, action_confidence, action_probabilities = _choice_answer(
        answers, "recommended_action", TRAFFIC_ACTIONS
    )
    prioritize, prioritize_confidence = _noul_answer(answers, "prioritize_intervention")
    return {
        "criticality": float(criticality["score"]),
        "most_critical_point": most_critical_point,
        "recommended_action": recommended_action,
        "prioritize_intervention": prioritize,
        "confidence": min(point_confidence, action_confidence, prioritize_confidence),
        "probabilities": {
            "most_critical_point": point_probabilities,
            "recommended_action": action_probabilities,
        },
    }


def _clef_selector(model: str) -> str:
    if model == "@cf/cloudflare/clef-flash":
        return "clef-flash"
    if model == "@cf/cloudflare/clef":
        return "clef"
    raise WorkersAIError(
        "CLOUDFLARE_AI_MODEL precisa ser @cf/cloudflare/clef ou "
        "@cf/cloudflare/clef-flash."
    )


def _answer(answers: dict, key: str) -> dict:
    value = answers.get(key)
    if not isinstance(value, dict):
        raise WorkersAIError(f"Resposta Clef ausente ou inválida para {key!r}.")
    return value


def _noul_answer(answers: dict, key: str) -> tuple[bool, float]:
    value = _answer(answers, key)
    if value.get("type") != "noul" or not isinstance(value.get("noul"), (int, float)):
        raise WorkersAIError(f"Resposta Clef inválida para a pergunta {key!r}.")
    probability = float(value["noul"])
    if not 0 <= probability <= 1:
        raise WorkersAIError(f"Probabilidade Clef inválida para {key!r}.")
    return probability >= 0.5, max(probability, 1 - probability)


def _choice_answer(answers: dict, key: str, allowed: set[str]) -> tuple[str, float, dict]:
    value = _answer(answers, key)
    choice = value.get("choice")
    probabilities = value.get("probabilities")
    confidence = value.get("confidence")
    if (
        value.get("type") != "choice"
        or choice not in allowed
        or not isinstance(probabilities, dict)
        or not isinstance(confidence, (int, float))
    ):
        raise WorkersAIError(f"Resposta Clef inválida para a pergunta {key!r}.")
    confidence = float(confidence)
    if not 0 <= confidence <= 1:
        raise WorkersAIError(f"Confiança Clef inválida para {key!r}.")
    return choice, confidence, probabilities


def normalize_clef_result(result: dict) -> dict:
    """Converte a resposta tipada do Clef para o contrato da aplicação."""
    answers = result.get("answers") if isinstance(result, dict) else None
    if not isinstance(answers, dict):
        raise WorkersAIError("Cloudflare não devolveu as respostas do Clef.")

    department, department_confidence, department_probabilities = _choice_answer(
        answers, "department", DEPARTMENTS
    )
    urgency, urgency_confidence, urgency_probabilities = _choice_answer(
        answers, "urgency", set(URGENCIES)
    )
    urgent, urgent_confidence = _noul_answer(answers, "urgent")
    needs_human, human_confidence = _noul_answer(answers, "needs_human")
    is_spam, spam_confidence = _noul_answer(answers, "is_spam")

    severity = _answer(answers, "severity")
    if severity.get("type") != "score" or not isinstance(severity.get("score"), (int, float)):
        raise WorkersAIError("Resposta Clef inválida para a pergunta 'severity'.")

    confidence = min(
        department_confidence,
        urgency_confidence,
        urgent_confidence,
        human_confidence,
        spam_confidence,
    )
    reason = (
        f"Clef classificou como {department}, urgência {urgency}; "
        f"urgente={urgent}, revisão humana={needs_human}, spam={is_spam}."
    )
    return {
        "department": department,
        "urgency": urgency,
        "urgent": urgent,
        "needs_human": needs_human,
        "is_spam": is_spam,
        "confidence": confidence,
        "severity": float(severity["score"]),
        "reason": reason,
        "probabilities": {
            "department": department_probabilities,
            "urgency": urgency_probabilities,
        },
    }


def load_dotenv(path: str = ".env") -> None:
    """Carrega variáveis simples do arquivo .env sem dependências externas."""
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def classify_with_cloudflare(ticket: str, channel: str, customer_tier: str) -> dict:
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    api_token = os.getenv("CLOUDFLARE_API_TOKEN")
    if not account_id or not api_token:
        raise WorkersAIError(
            "Configure CLOUDFLARE_ACCOUNT_ID e CLOUDFLARE_API_TOKEN no .env. "
            "Crie um token Cloudflare com permissão Workers AI: Read."
        )

    model = os.getenv("CLOUDFLARE_AI_MODEL", DEFAULT_MODEL)
    selector = _clef_selector(model)
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
    body = {
        "model": selector,
        "state": {
            "channel": channel,
            "customer_tier": customer_tier,
            "ticket": ticket,
        },
        "questions": CLEF_QUESTIONS,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")[:1000]
        raise WorkersAIError(f"Workers AI respondeu HTTP {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise WorkersAIError(f"Falha de conexão com Cloudflare Workers AI: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise WorkersAIError("Cloudflare devolveu uma resposta que não é JSON válido.") from exc

    if payload.get("success") is False:
        raise WorkersAIError(f"Workers AI reportou erro: {payload.get('errors', payload)}")
    result = payload.get("result") or {}
    return validate_decision(normalize_clef_result(result))


def analyze_traffic_with_cloudflare(route: dict) -> dict:
    """Pede ao Clef para priorizar os pontos críticos retornados pelo Google."""
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    api_token = os.getenv("CLOUDFLARE_API_TOKEN")
    if not account_id or not api_token:
        raise WorkersAIError(
            "Configure CLOUDFLARE_ACCOUNT_ID e CLOUDFLARE_API_TOKEN no .env."
        )

    model = os.getenv("CLOUDFLARE_AI_MODEL", DEFAULT_MODEL)
    selector = _clef_selector(model)
    critical_points = route.get("critical_points", [])
    body = {
        "model": selector,
        "state": {
            "origin": route["origin"],
            "destination": route["destination"],
            "distance_meters": route["distance_meters"],
            "duration_seconds": route["duration_seconds"],
            "static_duration_seconds": route["static_duration_seconds"],
            "delay_seconds": route["delay_seconds"],
            "critical_points": critical_points,
            "traffic_intervals": route["traffic_intervals"],
        },
        "questions": _traffic_questions(critical_points),
    }
    request = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")[:1000]
        raise WorkersAIError(f"Workers AI respondeu HTTP {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise WorkersAIError(f"Falha de conexão com Cloudflare Workers AI: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise WorkersAIError("Cloudflare devolveu uma resposta que não é JSON válido.") from exc

    if payload.get("success") is False:
        raise WorkersAIError(f"Workers AI reportou erro: {payload.get('errors', payload)}")
    result = payload.get("result") or {}
    return normalize_traffic_clef_result(result, {point["id"] for point in critical_points})


def validate_decision(value: dict) -> dict:
    """Validação local; JSON mode ajuda no formato, mas a aplicação ainda valida."""
    if not isinstance(value, dict):
        raise WorkersAIError("A decisão não é um objeto JSON.")
    required = {"department", "urgency", "needs_human", "is_spam", "confidence", "reason"}
    missing = required - value.keys()
    if missing:
        raise WorkersAIError(f"Campos ausentes na decisão: {', '.join(sorted(missing))}")
    if value["department"] not in DEPARTMENTS:
        raise WorkersAIError(f"Departamento não permitido: {value['department']!r}")
    if value["urgency"] not in URGENCIES:
        raise WorkersAIError(f"Urgência não permitida: {value['urgency']!r}")
    for key in ("needs_human", "is_spam"):
        if not isinstance(value[key], bool):
            raise WorkersAIError(f"{key} precisa ser boolean.")
    confidence = value["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise WorkersAIError("confidence deve ser um número entre 0 e 1.")
    if not isinstance(value["reason"], str):
        raise WorkersAIError("reason precisa ser texto.")
    return {**value, "confidence": float(confidence)}


def apply_policy(decision: dict) -> dict:
    """Converte a classificação em ação operacional sem deixar o modelo executar efeitos."""
    urgency = decision["urgency"]
    department = decision["department"]
    confidence = decision["confidence"]

    if decision["is_spam"]:
        return {
            "action": "quarantine",
            "queue": "fila_confianca_seguranca",
            "sla": "24h",
            "human_review": True,
        }

    if confidence < 0.55:
        queue = "fila_revisao_manual"
        human_review = True
    else:
        queue = {
            "billing": "fila_financeiro",
            "technical": "fila_suporte_tecnico",
            "sales": "fila_vendas",
            "trust_safety": "fila_confianca_seguranca",
        }[department]
        human_review = False

    if urgency == "crítica":
        queue = f"prioritaria_{queue}"
    escalated = decision["needs_human"] or decision.get("urgent", False) or urgency == "crítica"
    action = "escalate" if escalated else ("review" if human_review else "route")
    return {
        "action": action,
        "queue": queue,
        "sla": {"baixa": "48h", "média": "8h", "alta": "2h", "crítica": "30min"}[urgency],
        "human_review": human_review or escalated,
    }


def analyze(ticket: str, channel: str, customer_tier: str) -> dict:
    decision = classify_with_cloudflare(ticket, channel, customer_tier)
    return {
        "ticket": {"message": ticket, "channel": channel, "customer_tier": customer_tier},
        "decision": decision,
        "policy": apply_policy(decision),
        "model": os.getenv("CLOUDFLARE_AI_MODEL", DEFAULT_MODEL),
        "confidence_note": "Confiança derivada das probabilidades retornadas pelo Clef.",
    }


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Triagem de tickets com Cloudflare Workers AI")
    parser.add_argument("--message", "--msg", dest="message", help="Texto do ticket")
    parser.add_argument("--channel", "--canal", default="email")
    parser.add_argument("--tier", default="standard", help="Tier do cliente: standard, enterprise etc.")
    parser.add_argument("--traffic", action="store_true", help="Analisa trânsito na rota Brasília-Valparaíso")
    parser.add_argument("--origin", default=DEFAULT_ORIGIN, help="Origem da rota")
    parser.add_argument("--destination", default=DEFAULT_DESTINATION, help="Destino da rota")
    parser.add_argument("--json", action="store_true", help="Imprime resposta JSON")
    args = parser.parse_args(argv)

    if args.traffic:
        try:
            route = get_traffic_route(args.origin, args.destination)
            clef = analyze_traffic_with_cloudflare(route)
            result = {"route": route, "clef": clef}
        except (GoogleMapsError, WorkersAIError) as exc:
            print(f"Erro: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"Rota: {route['origin']} -> {route['destination']}")
            print(f"Atraso estimado: {route['delay_seconds'] / 60:.1f} min")
            print(f"Pontos críticos: {len(route['critical_points'])}")
            for point in route["critical_points"]:
                print(
                    f"- {point['id']} | {point['speed']} | "
                    f"{point['latitude']:.6f},{point['longitude']:.6f} | {point['maps_url']}"
                )
            print(
                f"Clef: {clef['recommended_action']} em "
                f"{clef['most_critical_point']} (criticidade {clef['criticality']:.2f})"
            )
        return 0

    message = args.message or input("Mensagem do ticket: ").strip()
    if not message:
        parser.error("A mensagem do ticket não pode estar vazia.")
    try:
        result = analyze(message, args.channel, args.tier)
    except WorkersAIError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        decision = result["decision"]
        policy = result["policy"]
        print(f"Modelo Cloudflare: {result['model']}")
        print(f"Departamento: {decision['department']} | urgência: {decision['urgency']}")
        print(f"Confiança declarada pelo modelo: {decision['confidence']:.2f} (não calibrada)")
        print(f"Humano: {decision['needs_human']} | spam: {decision['is_spam']}")
        print(f"Ação: {policy['action']} | fila: {policy['queue']} | SLA: {policy['sla']}")
        print(f"Motivo: {decision['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
