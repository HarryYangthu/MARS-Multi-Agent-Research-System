"""Real temporary observation files and event decoding; no substituted execution."""
import json
from pathlib import Path

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
    (attempt/'external.png').symlink_to(external)
    report = sync_run_visualizations(run)
    assert report['images'] == 0
    assert len(report['warnings']) == 2
