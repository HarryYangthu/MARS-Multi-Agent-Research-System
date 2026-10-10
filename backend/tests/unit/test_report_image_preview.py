from pathlib import Path

import pytest
from PIL import Image

from app.bridge.report_service import report_image_path
from app.storage.run_store import RunStore


def test_image_preview_is_run_scoped_and_preserves_source(tmp_path: Path) -> None:
    run = RunStore(tmp_path / 'runs').create(task='image-preview', project='image-project')
    image = run.root / 'execution/曲线.png'
    Image.new('RGB', (16, 16), 'white').save(image)
    before = image.read_bytes()
    assert report_image_path(run, '../execution/%E6%9B%B2%E7%BA%BF.png') == image
    assert image.read_bytes() == before
    for ref in ('../../private.png', '/private.png', 'execution/../../private.png', 'input/source.png', 'execution/result.html'):
        with pytest.raises((ValueError, FileNotFoundError)):
            report_image_path(run, ref)
    outside = tmp_path / 'outside.png'
    Image.new('RGB', (16, 16), 'white').save(outside)
    (run.root / 'execution/link.png').symlink_to(outside)
    with pytest.raises(ValueError):
        report_image_path(run, 'execution/link.png')
