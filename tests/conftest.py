import dataclasses
import io

import pytest
from PIL import Image

from headshot.settings import Settings
from headshot.store.sqlite import SQLiteStore


def make_photo(size=(900, 1200), fmt="JPEG", color=(120, 90, 70)) -> bytes:
    img = Image.new("RGB", size, color)
    # a little structure so hashes differ between colours/sizes
    for x in range(0, size[0], 50):
        for y in range(0, size[1], 50):
            img.putpixel((x, y), (255, 255, 255))
    out = io.BytesIO()
    img.save(out, format=fmt)
    return out.getvalue()


@pytest.fixture
def photo() -> bytes:
    return make_photo()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return dataclasses.replace(
        Settings(),
        data_dir=tmp_path / "data",
        mock_latency_ms=0,
        admin_token="test-admin-token",
        rate_limit_per_hour=1000,
        cors_origins=["http://localhost:5173"],
    )


@pytest.fixture
def store(settings) -> SQLiteStore:
    return SQLiteStore(settings.data_dir)
