"""Real temporary observation files and event decoding; no substituted execution."""
import json
from pathlib import Path

import pytest

from PIL import Image
from tensorboard.backend.event_processing.event_accumulator import EventAccumulator

from app.execution.tensorboard_artifacts import sync_run_visualizations


def test_saved_observations_figures_and_provenance_are_idempotent(tmp_path: Path) -> None:
    run = tmp_path/'run'
    attempt = run/'execution/local_commands/experiment/invocation'
    model = attempt/'training/baseline'
    model.mkdir(parents=True)
    (attempt/'job.json').write_text(json.dumps({'run_id': run.name, 'experiment_id': 'experiment'}))
    (model/'steps.jsonl').write_text(json.dumps({'optimizer_step': 1, 'training_loss': .25, 'lr': .001})+'\n')
    (model/'config.json').write_text('{"model": "baseline"}')
    (model/'history.json').write_text(json.dumps([{'optimizer_steps': 0, 'validation_RES_db': 10., 'epoch': 0},
                                               {'optimizer_steps': 1, 'validation_RES_db': 8., 'epoch': 1}]))
    (model/'summary.json').write_text(json.dumps({'real_parameters': 12, 'selected_optimizer_steps': 1,
        'test': {'RES_db': 7., 'worst_channel_db': 8., 'channel_mean_db': 6., 'per_channel_db': [6., 8.]}}))
    figure = model/'figs/coeffs/lut.png'
    figure.parent.mkdir(parents=True)
    Image.new('RGB', (12, 10), 'blue').save(figure)
    original = (model/'steps.jsonl').read_bytes()
    report = sync_run_visualizations(run)
    assert report['images'] == 1 and not report['warnings']
    assert report['models'][0]['validation_points'] == 2
    assert report['models'][0]['is_baseline'] is True
    assert report['models'][0]['scalar_run'].endswith('/training/baseline')
    assert report['models'][0]['image_tags'] == ['figures/training/baseline/figs/coeffs/lut.png']
    report2 = sync_run_visualizations(run)
    assert report2 == report
    root = run/'execution/tensorboard_artifacts'
    event_dir = next(root.glob('*/training/baseline'))
    events = EventAccumulator(str(event_dir)).Reload()
    assert len(events.Scalars('train/loss')) == 1
    assert events.Scalars('train/loss')[0].value == .25
    assert [x.step for x in events.Scalars('validation/RES_db')] == [0, 1]
    assert events.Scalars('test/RES_db')[0].value == 7.
    figures = EventAccumulator(str(next(root.glob('*/figures')))).Reload()
    assert figures.Images('figures/training/baseline/figs/coeffs/lut.png')[0].width == 12
    assert (model/'steps.jsonl').read_bytes() == original
    # A subsequently flushed observation appends without duplicating earlier points.
    with (model/'steps.jsonl').open('a') as handle:
        handle.write(json.dumps({'optimizer_step': 2, 'training_loss': .125})+'\n')
    sync_run_visualizations(run)
    events.Reload()
    assert [x.value for x in events.Scalars('train/loss')] == [.25, .125]


def test_missing_curves_and_external_images_are_reported(tmp_path: Path) -> None:
    run = tmp_path/'run'
    attempt = run/'execution/local_commands/experiment/invocation'
    attempt.mkdir(parents=True)
    (attempt/'job.json').write_text(json.dumps({'run_id': run.name, 'experiment_id': 'experiment'}))
    (attempt/'result.json').write_text('{}')
    external = tmp_path/'external.png'
    Image.new('RGB', (10, 10)).save(external)
    try:
        (attempt/'external.png').symlink_to(external)
    except OSError as exc:
        if getattr(exc, 'winerror', None) == 1314:
            pytest.skip('Windows user cannot create symbolic links')
        raise
    report = sync_run_visualizations(run)
    assert report['images'] == 0
    assert len(report['warnings']) == 2


def test_sparse_csv_observations_link_figures_without_filling_missing_epochs(tmp_path: Path) -> None:
    # Caller-authored visualization input only; no execution success is asserted.
    run = tmp_path / 'csv-run'
    attempt = run / 'execution/local_commands/candidate/invocation'
    model = attempt / 'training'
    model.mkdir(parents=True)
    (attempt / 'job.json').write_text(json.dumps({'run_id': run.name, 'experiment_id': 'candidate'}))
    source = model / 'training.csv'
    source.write_text('epoch,updates,loss_mse,lr,train_PIM,train_RES,validation_PIM,validation_RES\n'
                      '1,79,0.05,0.004,25,22,25,23\n'
                      '25,1975,0.001,0.004,25,6,25,5\n'
                      '50,3950,0.0008')  # incomplete concurrent write
    Image.new('RGB', (12, 10), 'blue').save(model / 'spectrum.png')
    original = source.read_bytes()
    report = sync_run_visualizations(run)
    assert len(report['models']) == 1 and report['images'] == 1 and not report['warnings']
    record = report['models'][0]
    assert record['label'] == 'candidate' and record['validation_points'] == 2
    assert record['training_steps'] == 1975
    assert record['image_tags'] == ['figures/training/spectrum.png']
    event_files = set(run.rglob('events.out.tfevents.*'))
    for _ in range(3):
        assert sync_run_visualizations(run) == report and source.read_bytes() == original
    assert set(run.rglob('events.out.tfevents.*')) == event_files
    events = EventAccumulator(str(run / record['scalar_run'])).Reload()
    assert [x.step for x in events.Scalars('validation/RES_db')] == [79, 1975]
    assert [x.value for x in events.Scalars('validation/RES_db')] == [23, 5]
    with source.open('a') as handle:
        handle.write(',0.001,25,5,25,4\n')
    updated = sync_run_visualizations(run)
    assert updated['models'][0]['validation_points'] == 3
    events.Reload()
    assert [x.step for x in events.Scalars('train/loss')] == [79, 1975, 3950]


def test_native_circle_csv_streams_saved_rows_without_inventing_validation(tmp_path: Path) -> None:
    run = tmp_path / 'native-csv-run'
    attempt = run / 'execution/local_commands/candidate/invocation'
    attempt.mkdir(parents=True)
    (attempt / 'job.json').write_text(json.dumps({'run_id': run.name, 'experiment_id': 'candidate'}))
    source = attempt / 'training.csv'
    source.write_text('circle,updates,lr,mean_loss_sum,train_PIM_native,train_RES_native,rx_centers_mhz,err_centers_mhz\n'
                      '0,0,0.004,,25.84,25.78,[34.3],[34.3]\n'
                      '1,79,0.004,0.25,25.84,22,[34.3],[34.2]\n'
                      '2,158,0.004,0.125')
    Image.new('RGB', (12, 10), 'blue').save(attempt / 'spectrum.png')
    original = source.read_bytes()
    report = sync_run_visualizations(run)
    assert not report['warnings']
    record = report['models'][0]
    assert record['training_steps'] == 79 and record['validation_points'] == 0
    assert record['image_tags'] == ['figures/spectrum.png']
    events = EventAccumulator(str(run / record['scalar_run'])).Reload()
    assert [x.step for x in events.Scalars('train/native_RES_db')] == [0, 79]
    assert [x.value for x in events.Scalars('runtime/circle')] == [0, 1]
    assert [x.value for x in events.Scalars('train/mean_loss_sum')] == [.25]
    assert not any(tag.startswith(('validation/', 'test/')) for tag in events.Tags()['scalars'])
    event_files = set(run.rglob('events.out.tfevents.*'))
    assert sync_run_visualizations(run) == report
    assert source.read_bytes() == original
    assert set(run.rglob('events.out.tfevents.*')) == event_files
    with source.open('a') as handle:
        handle.write(',25.84,20,[34.3],[34.2]\n')
    sync_run_visualizations(run)
    events.Reload()
    assert [x.step for x in events.Scalars('train/native_RES_db')] == [0, 79, 158]
    assert [x.value for x in events.Scalars('train/mean_loss_sum')] == [.25, .125]


def test_unknown_csv_schema_is_explicitly_rejected(tmp_path: Path) -> None:
    from app.execution.tensorboard_csv import training_csv_rows

    source = tmp_path / 'training.csv'
    source.write_text('updates,loss\n1,0.25\n')
    with pytest.raises(ValueError, match='unsupported training.csv columns'):
        training_csv_rows(source)
