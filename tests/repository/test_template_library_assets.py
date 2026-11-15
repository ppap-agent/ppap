"""The template library manifest names exactly the checked-in originals."""

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
