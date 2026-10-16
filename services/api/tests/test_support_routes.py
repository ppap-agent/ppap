from fastapi import FastAPI
from fastapi.testclient import TestClient
from ppap_core.settings import Settings


def support_client(settings, principal):
    from ppap_api.support_routes import register_support_routes

    app = FastAPI()
    register_support_routes(app, lambda _: principal, settings)
    return TestClient(app)


def test_support_requires_login_and_never_invents_missing_contact_details():
    settings = Settings()
    anonymous = support_client(settings, None)
    assert anonymous.get("/support").status_code == 401

    client = support_client(settings, object())
    response = client.get("/support")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == {"state": "unavailable", "config_version": None, "phone": None, "email": None, "service_hours": None, "qr_available": False}

    settings.support_email = " support@example.test "
    settings.support_service_hours = " 工作日 09:00–18:00 "
    configured = client.get("/support").json()
    assert configured["state"] == "configured"
    assert configured["email"] == "support@example.test"
    assert configured["phone"] is None
    assert configured["service_hours"] == "工作日 09:00–18:00"
    assert configured["qr_available"] is False
    assert configured["config_version"]
    assert client.get("/support").json() == configured


def test_support_qr_is_authenticated_and_bound_to_the_displayed_configuration(tmp_path):
    from PIL import Image

    path = tmp_path / "service.png"
    Image.new("RGB", (24, 24), "white").save(path)
    settings = Settings(support_qr_file=str(path))
    client = support_client(settings, object())
    info = client.get("/support").json()
    assert info["state"] == "configured"
    assert info["qr_available"] is True
    assert str(path) not in str(info)
    params = {"config_version": info["config_version"]}
    assert support_client(settings, None).get("/support/qr", params=params).status_code == 401
    image = client.get("/support/qr", params=params)
    assert image.status_code == 200
    assert image.content == path.read_bytes()
    assert image.headers["content-type"] == "image/png"
    assert image.headers["cache-control"] == "private, no-store"
    assert image.headers["x-content-type-options"] == "nosniff"

    Image.new("RGB", (24, 24), "black").save(path)
    assert client.get("/support/qr", params=params).status_code == 409
    current = client.get("/support").json()
    assert current["config_version"] != info["config_version"]
    assert client.get("/support/qr", params={"config_version": current["config_version"]}).content == path.read_bytes()
    settings.support_email = "support@example.test"
    assert client.get("/support/qr", params={"config_version": current["config_version"]}).status_code == 409


def test_bad_or_missing_support_image_does_not_hide_other_contact_channels(tmp_path):
    path = tmp_path / "service.png"
    settings = Settings(support_phone="+00 000 000", support_qr_file=str(path))
    client = support_client(settings, object())
    for body in (b"not an image", b'<svg xmlns="http://www.w3.org/2000/svg"/>', b"\x89PNG\r\n\x1a\ntruncated", b"x" * (2 * 1024 * 1024 + 1)):
        path.write_bytes(body)
        info = client.get("/support").json()
        assert info["state"] == "configured"
        assert info["phone"] == "+00 000 000"
        assert info["qr_available"] is False
        assert client.get("/support/qr", params={"config_version": info["config_version"]}).status_code == 404
    path.unlink()
    assert client.get("/support").json()["qr_available"] is False
    settings.support_phone = ""
    settings.support_service_hours = "09:00–18:00"
    assert client.get("/support").json()["state"] == "unavailable"
