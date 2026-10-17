"""Real PNG files and ZIP bytes verify report portability without simulated tools."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit
import zipfile

from PIL import Image
import pytest

from app.reporting import generate_report_bundle
from app.reporting.bundle import _save_images
from app.reporting.data_pack import collect_report_data_pack, markdown_image_references
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunHandle, RunStore


def approve(run: RunHandle, body: str) -> Path:
    store = ArtifactStore(run)
    ref = store.write_metadata(metadata={'schema': 'report.v1', 'agent': 'writing', 'project': run.project,
        'deliverable_type': 'research_report', 'target_audience': 'file export verification', 'chain_refs': {}},
        body=body, expected_schema='report.v1')
    store.approve(ref)
    return run.root / 'writing/research_report.approved.md'


def png(path: Path, color: str) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (32, 24), color).save(path)
    return path.read_bytes()


def test_lab_manifest_exports_nested_images_without_markdown_embedding(tmp_path: Path) -> None:
    run = RunStore(tmp_path / 'runs').create(task='Manifest image archive, no scientific claims', project='generic')
    original = png(run.root / 'execution/visualizations/nested/diagram.png', 'blue')
    manifest = run.root / 'execution/visualizations/figures.json'
    manifest.write_text(json.dumps({'schema': 'research_figures.v1', 'figures': [
        {'path': 'visualizations/nested/diagram.png', 'sha256': 'sha256:' + hashlib.sha256(original).hexdigest()}]}))
    approved = approve(run, '# File export fixture\n\nA plain text citation, no image embedding.')
    before = approved.read_bytes()
    bundle = generate_report_bundle(run)
    metadata = bundle['metadata']
    assert len(metadata['images']) == 1
    assert metadata['images'][0]['source_path'] == 'execution/visualizations/nested/diagram.png'
    assert 'execution/visualizations/figures.json' in metadata['source_hashes']
    with zipfile.ZipFile(run.root / metadata['materials_archive']['path']) as archive:
        image_name = 'images/' + Path(metadata['images'][0]['path']).name
        assert archive.read(image_name) == original
    assert approved.read_bytes() == before
    # Changing the source must never silently refresh the recorded digest.
    png(run.root / 'execution/visualizations/nested/diagram.png', 'red')
    with pytest.raises(ValueError, match='哈希'):
        collect_report_data_pack(run)


def test_every_nested_body_plot_is_hash_bound_and_portable_in_genuine_zip(tmp_path: Path) -> None:
    run = RunStore(tmp_path / 'runs').create(task='PNG export fixture, no scientific claims', project='generic')
    paths = ['execution/local_commands/baseline/invocation/baseline/figs/loss_live.png',
             'execution/local_commands/baseline/invocation/comparison_spectrum_zoom.png',
             'execution/local_commands/baseline/invocation/comparison_channels.png']
    source_bytes = {name: png(run.root / name, color) for name, color in zip(paths, ['red', 'green', 'blue'], strict=True)}
    aggregate = 'execution/loss_curves_16.png'
    source_bytes[aggregate] = png(run.root / aggregate, 'white')
    body = '# Report material export fixture\n\nThese files verify document portability only.\n\n'
    body += '\n\n'.join(f'![Figure {index}](../{name} "Recorded image")' for index, name in enumerate(paths, 1))
    body += f'\n\n![Repeated](../{paths[0]})\n\n![External](https://example.invalid/published.png)\n'
    body += f'\n`![Inline example](../{paths[0]})`\n\n```md\n![Example](../execution/absent.png)\n```\n'
    approved = approve(run, body)
    approved_bytes = approved.read_bytes()
    bundle = generate_report_bundle(run, actor='file-verification')
    metadata = bundle['metadata']
    images = metadata['images']
    assert {item['source_path'] for item in images} == set(source_bytes)
    assert len(images) == 4
    data_pack = json.loads((run.root / metadata['data_pack']).read_text())
    for item in images:
        expected = source_bytes[item['source_path']]
        assert item['sha256'] == data_pack['source_hashes'][item['source_path']] == hashlib.sha256(expected).hexdigest()
        assert (run.root / item['path']).read_bytes() == expected
    archive_path = run.root / metadata['materials_archive']['path']
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(set(archive.namelist()))
        exported = archive.read('research_report.md').decode()
        assert f'`![Inline example](../{paths[0]})`' in exported
        assert '```md\n![Example](../execution/absent.png)\n```' in exported
        assert '![External](https://example.invalid/published.png)' in exported
        urls = [item['url'] for item in markdown_image_references(exported) if not urlsplit(item['url']).scheme]
        assert len(urls) == 8  # Four original image mentions plus the four saved-image entries.
        for url in urls:
            member = unquote(urlsplit(url).path)
            assert member.startswith('images/') and '..' not in Path(member).parts
            assert member in archive.namelist()
        for item in images:
            assert archive.read('images/' + Path(item['path']).name) == source_bytes[item['source_path']]
    assert approved.read_bytes() == approved_bytes
    assert all((run.root / name).read_bytes() == data for name, data in source_bytes.items())


def test_document_relative_unicode_and_reference_images_keep_working_package_links(tmp_path: Path) -> None:
    run = RunStore(tmp_path / 'runs').create(task='Markdown reference export fixture', project='generic')
    source = run.root / 'writing/figures/关键 图片.png'
    original = png(source, 'purple')
    body = '# Reference image fixture\n\n![Inline](<figures/关键 图片.png> "title")\n\n![Reference][figure]\n\n'
    body += '[figure]: <figures/关键 图片.png> "title"\n\n![Remote][remote]\n\n[remote]: https://example.invalid/image.png\n'
    approved = approve(run, body)
    approved_bytes = approved.read_bytes()
    bundle = generate_report_bundle(run)
    assert len(bundle['metadata']['images']) == 1
    with zipfile.ZipFile(run.root / bundle['metadata']['materials_archive']['path']) as archive:
        exported = archive.read('research_report.md').decode()
        assert '[remote]: https://example.invalid/image.png' in exported
        local_urls = [item['url'] for item in markdown_image_references(exported) if not urlsplit(item['url']).scheme]
        assert len(local_urls) == 3
        for url in local_urls:
            assert archive.read(unquote(urlsplit(url).path)) == original
        assert '%20' in exported and '[figure]: images/' in exported
    assert approved.read_bytes() == approved_bytes


@pytest.mark.parametrize('reference', ['../../outside.png', '%2e%2e/%2e%2e/outside.png', '/tmp/outside.png', '../input/private.png'])
def test_unsafe_local_image_paths_cannot_enter_archive(tmp_path: Path, reference: str) -> None:
    run = RunStore(tmp_path / 'runs').create(task='Unsafe reference rejection fixture', project='generic')
    approve(run, '# Boundary fixture\n\n![Rejected](' + reference + ')\n')
    with pytest.raises(ValueError):
        generate_report_bundle(run)
    assert not list(run.root.rglob('report_materials.zip'))


def test_symlink_and_changed_source_images_cannot_be_archived(tmp_path: Path) -> None:
    run = RunStore(tmp_path / 'runs').create(task='Image integrity fixture', project='generic')
    path = run.root / 'execution/nested/real.png'
    png(path, 'red')
    approved = approve(run, '# Image integrity fixture\n\n![Figure](../execution/nested/real.png)\n')
    pack = collect_report_data_pack(run)
    png(path, 'blue')
    with pytest.raises(ValueError, match='修改'):
        _save_images(run, run.root / 'writing/export-check', pack)
    external = tmp_path / 'external.png'
    png(external, 'white')
    path.unlink()
    path.symlink_to(external)
    original = approved.read_bytes()
    with pytest.raises(ValueError, match='symbolic'):
        generate_report_bundle(run)
    assert approved.read_bytes() == original
    assert not list(run.root.rglob('report_materials.zip'))
