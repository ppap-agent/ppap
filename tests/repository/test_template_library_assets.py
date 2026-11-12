"""The template library manifest names exactly the checked-in originals, and the API image carries them."""

from pathlib import Path

from product import template_library as library

ROOT = Path(__file__).resolve().parents[2]


def test_manifest_covers_every_template_original_once():
    for customer in library.CUSTOMERS:
        folder = ROOT / "assets" / customer.id / "templates"
        on_disk = sorted(path.name for path in folder.iterdir() if path.is_file() and not path.name.startswith("."))
        assert sorted(template.source for template in customer.templates) == on_disk, customer.id
        # "已支持 N 类" counts document types; one template per type.
        assert len({template.document_type for template in customer.templates}) == len(customer.templates), customer.id
        assert len({template.filename for template in customer.templates}) == len(customer.templates), customer.id
        assert all(template.extension in library.CONTENT_TYPES for template in customer.templates), customer.id


def test_api_image_includes_only_the_template_directories_of_assets():
    dockerfile = (ROOT / "deploy/app/Dockerfile").read_text()
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert "assets" in ignore
    assert "ENV PPAP_TEMPLATE_ROOT=/app/assets" in dockerfile
    for customer in library.CUSTOMERS:
        assert f"!assets/{customer.id}/templates" in ignore
        assert f"COPY assets/{customer.id}/templates /app/assets/{customer.id}/templates" in dockerfile
    assert [line for line in ignore if line.startswith("!assets")] == [f"!assets/{customer.id}/templates" for customer in library.CUSTOMERS]
