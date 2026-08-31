from __future__ import annotations

import json
from typing import Any

import pytest

from corewarden.diagnostics import EvidenceRecorder, SecretRedactor
from corewarden.errors import RpcTransportError
from corewarden.models import Classification, Diagnosis, Evidence
from corewarden.rpc import CoreRpcNodeAdapter


class EvidenceNode:
    def get_blockchain_status(self) -> dict[str, Any]:
        return {
            "blocks": 50,
            "headers": 50,
            "warning": "contact operator observer",
            "authorization": "Basic dXNlcjpwYXNz",
        }

    def get_network_status(self) -> dict[str, Any]:
        return {"networkactive": True, "connections": 3, "username": "observer"}

    def get_peer_information(self) -> list[dict[str, Any]]:
        return [{"addr": "192.0.2.1:8338", "synced_blocks": 50}]

    def get_chain_tips(self) -> list[dict[str, Any]]:
        return [{"height": 50, "branchlen": 0, "status": "active"}]


def diagnosis() -> Diagnosis:
    return Diagnosis(
        classification=Classification.HEALTHY,
        confidence=0.9,
        summary="observer saw http://observer:very-secret@node as healthy",
        evidence=[
            Evidence(
                source="blockchain_status",
                observation="blocks and headers agree",
                significance="the known chain is synchronized",
            ),
            Evidence(
                source="peer_information",
                observation="three peers are connected",
                significance="peers corroborate connectivity",
            ),
        ],
        safety_boundary="No remediation was performed.",
    )


def test_redactor_handles_keys_auth_values_urls_and_known_secrets() -> None:
    redactor = SecretRedactor.from_values("observer", "very-secret")

    result = redactor.redact(
        {
            "username": "observer",
            "nested": {
                "password": "very-secret",
                "header": "Authorization: Bearer abc.def.ghi",
                "url": "http://observer:very-secret@127.0.0.1:8337",
                "note": "observer used very-secret",
            },
        }
    )

    serialized = json.dumps(result)
    assert "observer" not in serialized
    assert "very-secret" not in serialized
    assert "abc.def.ghi" not in serialized
    assert result["username"] == "[REDACTED]"


def test_recorder_captures_only_four_read_only_observations(tmp_path: Any) -> None:
    path = tmp_path / "evidence.json"
    redactor = SecretRedactor.from_values("observer", "very-secret")
    recorder = EvidenceRecorder(EvidenceNode(), path, redactor)

    recorder.get_blockchain_status()
    recorder.get_network_status()
    recorder.get_peer_information()
    recorder.get_chain_tips()
    recorder.write(diagnosis(), None)

    document = json.loads(path.read_text(encoding="utf-8"))
    serialized = path.read_text(encoding="utf-8")
    assert [item["source"] for item in document["observations"]] == [
        "blockchain_status",
        "network_status",
        "peer_information",
        "chain_tips",
    ]
    assert document["allowed_rpc_methods"] == [
        "getblockchaininfo",
        "getnetworkinfo",
        "getpeerinfo",
        "getchaintips",
    ]
    assert document["mode"] == "read_only_diagnostic"
    assert document["diagnosis"]["classification"] == "healthy"
    assert "observer" not in serialized
    assert "very-secret" not in serialized
    assert "dXNlcjpwYXNz" not in serialized
    assert not list(tmp_path.glob("*.tmp"))


def test_recorder_records_redacted_rpc_failure(tmp_path: Any) -> None:
    class FailingNode(EvidenceNode):
        def get_network_status(self) -> dict[str, Any]:
            raise RpcTransportError("observer password very-secret was rejected")

    path = tmp_path / "failed.json"
    recorder = EvidenceRecorder(
        FailingNode(), path, SecretRedactor.from_values("observer", "very-secret")
    )

    with pytest.raises(RpcTransportError):
        recorder.get_network_status()
    recorder.write(None, {"error": "RpcTransportError", "message": "very-secret"})

    serialized = path.read_text(encoding="utf-8")
    document = json.loads(serialized)
    assert document["observations"][0]["status"] == "error"
    assert "observer" not in serialized
    assert "very-secret" not in serialized


def test_structured_evidence_receives_only_sanitized_peer_metrics(tmp_path: Any) -> None:
    class PeerTransport:
        def call(self, method: str) -> Any:
            assert method == "getpeerinfo"
            return [
                {
                    "id": 42,
                    "addr": "203.0.113.40:8338",
                    "addrbind": "127.0.0.1:51000",
                    "subver": "/IdentifyingClient:2.0/",
                    "inbound": True,
                    "synced_headers": 50,
                    "synced_blocks": 50,
                    "pingtime": 0.03,
                    "servicesnames": ["NETWORK", "WITNESS"],
                    "lastrecv": 1700000000,
                }
            ]

    path = tmp_path / "sanitized-evidence.json"
    recorder = EvidenceRecorder(CoreRpcNodeAdapter(PeerTransport()), path, SecretRedactor())

    peers = recorder.get_peer_information()
    recorder.write(None, None)

    serialized = path.read_text(encoding="utf-8")
    stored_peer = json.loads(serialized)["observations"][0]["data"][0]
    assert peers == [stored_peer]
    assert stored_peer["synced_blocks"] == 50
    assert stored_peer["pingtime"] == 0.03
    assert stored_peer["servicesnames"] == ["NETWORK", "WITNESS"]
    assert "203.0.113.40" not in serialized
    assert "127.0.0.1:51000" not in serialized
    assert "IdentifyingClient" not in serialized
    assert '"id"' not in serialized
