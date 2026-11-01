"""Check approval at the owning business boundary, after reading its inputs."""

from fastapi import Request
from product.mcp import ApprovedCall, Delegation, McpError


def approval_for(request: Request) -> ApprovedCall | None:
    delegation = request.scope.get("ppap.delegation")
    if not isinstance(delegation, Delegation):
        return None
    proof = request.scope.get("ppap.mcp_approval")
    if (
        not isinstance(proof, ApprovedCall)
        or proof.grant_id != delegation.id
        or proof.operation_id != request.scope.get("ppap.mcp_operation_id")
        or proof.method != request.method
        or proof.path != request.scope["path"]
    ):
        raise McpError("invalid_confirmation", 403)
    return proof


def require_approved_source(request: Request, path: str, value: object) -> None:
    proof = approval_for(request)
    if proof is not None:
        proof.require_source(path, value)
