from __future__ import annotations

import argparse
import json
import os
import urllib.request

from justfastllm.config import load_settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="justfastllm")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("serve", help="run the gateway server")
    check = subcommands.add_parser("compliance-check", help="verify deployed technical compliance readiness")
    check.add_argument("--url", required=True, help="gateway base URL, for example https://gateway.example.com")
    check.add_argument("--master-key", default=os.getenv("JUSTFASTLLM_MASTER_KEY", ""), help="master key or JUSTFASTLLM_MASTER_KEY")
    check.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds")
    args = parser.parse_args(argv)

    if args.command == "compliance-check":
        result = compliance_check(args.url, master_key=args.master_key, timeout=args.timeout)
        print(json.dumps(result, indent=2, sort_keys=True))
        raise SystemExit(0 if result["ok"] else 1)

    serve()


def serve() -> None:
    settings = load_settings()
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - exercised only without dependency
        raise SystemExit("Install uvicorn or run: pip install -e .") from exc

    uvicorn.run(
        "justfastllm.app:app",
        host=os.getenv("JUSTFASTLLM_HOST", settings.host),
        port=int(os.getenv("JUSTFASTLLM_PORT", str(settings.port))),
        log_level=settings.log_level.lower(),
    )


def compliance_check(base_url: str, *, master_key: str, timeout: float = 10.0) -> dict[str, object]:
    base = base_url.rstrip("/")
    checks: dict[str, object] = {
        "health_ready": False,
        "report_ready": False,
        "integrity_ready": False,
        "automatic_legal_or_attested_claim_rejected": False,
    }
    details: dict[str, object] = {}
    errors: list[str] = []

    try:
        health = _get_json(f"{base}/health", timeout=timeout)
        details["health"] = health
        checks["health_ready"] = (
            health.get("status") == "ok"
            and isinstance(health.get("compliance"), dict)
            and bool(health["compliance"].get("ready"))
        )
    except Exception as exc:
        errors.append(f"health: {_safe_error(exc)}")
        health = {}

    if not master_key:
        errors.append("master key is required for compliance report and integrity endpoints")
    else:
        try:
            report = _get_json(f"{base}/v1/compliance/report", master_key=master_key, timeout=timeout)
            details["report"] = report
            assurance = report.get("assurance") if isinstance(report.get("assurance"), dict) else {}
            checks["report_ready"] = (
                report.get("status") == "technical_controls_ready"
                and bool(assurance.get("automated_deployment_gate_ready"))
                and bool(assurance.get("runtime_readiness_ready"))
            )
            checks["automatic_legal_or_attested_claim_rejected"] = (
                assurance.get("can_claim_automatic_legal_or_attested_compliance") is False
            )
        except Exception as exc:
            errors.append(f"report: {_safe_error(exc)}")

        try:
            integrity = _get_json(f"{base}/v1/compliance/integrity", master_key=master_key, timeout=timeout)
            details["integrity"] = integrity
            aggregate = integrity.get("aggregate_sha256")
            categories = integrity.get("categories")
            required_chain_categories = ("auth_events", "access_events", "audit_events")
            checks["integrity_ready"] = (
                integrity.get("object") == "evidence_integrity"
                and isinstance(aggregate, str)
                and len(aggregate) == 64
                and isinstance(categories, dict)
                and all(_hash_chain_valid(categories, name) for name in required_chain_categories)
            )
        except Exception as exc:
            errors.append(f"integrity: {_safe_error(exc)}")

    return {
        "ok": all(bool(value) for value in checks.values()),
        "checks": checks,
        "errors": errors,
        "details": details,
        "assurance_note": "This verifies deployed technical readiness evidence only; legal compliance and SOC 2 attestation remain operator, counsel, and auditor responsibilities.",
    }


def _get_json(url: str, *, master_key: str = "", timeout: float) -> dict[str, object]:
    headers = {"accept": "application/json"}
    if master_key:
        headers["authorization"] = f"Bearer {master_key}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("response is not a JSON object")
    return payload


def _safe_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    if "://" in message or "@" in message:
        return exc.__class__.__name__
    return message[:160]


def _hash_chain_valid(categories: object, name: str) -> bool:
    if not isinstance(categories, dict):
        return False
    category = categories.get(name)
    if not isinstance(category, dict):
        return False
    chain = category.get("hash_chain")
    return isinstance(chain, dict) and chain.get("valid") is True


if __name__ == "__main__":
    main()
