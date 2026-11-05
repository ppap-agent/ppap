"""Self-service personal model settings. Never echo submitted secret inputs."""

from documents.model_access import ModelUnavailable
from fastapi import Request
from fastapi.responses import JSONResponse
from product.store.model_credentials import ModelCredentialError
from pydantic import SecretStr


def register_model_credential_routes(app, store, principal_of):
    @app.get("/me/model-config")
    def model_config(request: Request):
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        return JSONResponse(store.personal_model_config(principal), headers={"Cache-Control": "no-store"})

    @app.put("/me/model-config")
    async def put_model_config(request: Request):
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        # Parse manually: FastAPI/Pydantic validation errors can echo raw inputs.
        # Bound before JSON decoding and return only fixed, non-secret codes.
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 8192:
                return JSONResponse({"error": "invalid_model_credential"}, status_code=400)
        try:
            import json

            body = json.loads(raw)
            if not isinstance(body, dict) or set(body) != {"api_key"} or not isinstance(body["api_key"], str):
                raise ValueError()
        except (ValueError, UnicodeError):
            return JSONResponse({"error": "invalid_model_credential"}, status_code=400)
        try:
            result = store.put_personal_model_config(principal, SecretStr(body["api_key"]))
        except ModelUnavailable:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        except ModelCredentialError as exc:
            status = 503 if exc.code == "model_credential_encryption_unavailable" else 400
            return JSONResponse({"error": exc.code}, status_code=status)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.delete("/me/model-config")
    def delete_model_config(request: Request):
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        return JSONResponse(store.delete_personal_model_config(principal), headers={"Cache-Control": "no-store"})
