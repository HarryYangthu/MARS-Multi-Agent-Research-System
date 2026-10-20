"""Real temporary observation files and event decoding; no substituted execution."""
import json
import hashlib
from pathlib import Path

from PIL import Image
from tensorboard.backend.event_processing.event_accumulator import EventAccumulator

from app.execution.tensorboard_artifacts import sync_run_visualizations
from app.execution.tensorboard_writer import ScalarWriter


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


def _native_method(run: Path, name: str, role: str) -> Path:
    """Actual temporary files/events describe observations, not a fake training run."""
    attempt = run / 'execution/local_commands' / name / ('invocation-' + name)
    method = attempt / 'measurement/model'
    method.mkdir(parents=True)
    (attempt / 'job.json').write_text(json.dumps({
        'schema': 'local_command_request.v1', 'run_id': run.name, 'project': 'real-files',
        'experiment_id': name, 'invocation_id': attempt.name,
        'config': {'role': role, 'budget_unit': 'epochs', 'max_iters': 2, 'seed': 2026}}))
    (method / 'config.yaml').write_text('model_name: ExistingModel\nmodel:\n  kernel: 3\n')
    rows = [{'optimizer_step': index, 'training_loss': loss, 'lr': .001}
            for index, loss in ((1, .25), (2, .125))]
    (method / 'steps.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    (method / 'epochs.jsonl').write_text(json.dumps({'epoch': 1, 'optimizer_steps': 2, 'RES': 8.}) + '\n')
    with ScalarWriter(method / 'tensorboard') as writer:
        for row in rows:
            writer.scalar('train/loss', row['training_loss'], int(row['optimizer_step']))
            writer.scalar('train/learning_rate', row['lr'], int(row['optimizer_step']))
        writer.scalar('heldout/RES_db', 8., 1)
    return method


def _native_files(methods: list[Path]) -> dict[Path, str]:
    return {path: hashlib.sha256(path.read_bytes()).hexdigest()
            for method in methods for path in method.rglob('*') if path.is_file()}


def test_model_and_comparison_images_have_independent_real_event_streams(tmp_path: Path) -> None:
    run = tmp_path / 'owned-image-streams'
    methods = [_native_method(run, 'control', 'baseline'), _native_method(run, 'variant', 'candidate')]
    for method, color in zip(methods, ('blue', 'green')):
        figure = method / 'figs/spectrum.png'
        figure.parent.mkdir()
        Image.new('RGB', (12, 10), color).save(figure)
        Image.new('RGB', (14, 9), color).save(method.parent.parent / 'comparison_spectrum.png')
    before = _native_files(methods)
    report = sync_run_visualizations(run)
    assert len(report['models']) == 2 and report['images'] == 4 and not report['warnings']
    for model in report['models']:
        assert len({model['image_run'], model['gallery_run'], model['comparison_run']}) == 3
        owned = EventAccumulator(str(run / model['image_run'])).Reload()
        assert owned.Tags()['images'] == ['figures/measurement/model/figs/spectrum.png']
        image = owned.Images(owned.Tags()['images'][0])
        assert len(image) == 1 and (image[0].width, image[0].height) == (12, 10)
        comparison = EventAccumulator(str(run / model['comparison_run'])).Reload()
        assert comparison.Tags()['images'] == ['figures/comparison_spectrum.png']
        assert comparison.Images('figures/comparison_spectrum.png')[0].width == 14
        gallery = EventAccumulator(str(run / model['gallery_run'])).Reload()
        assert set(gallery.Tags()['images']) == {
            'figures/measurement/model/figs/spectrum.png', 'figures/comparison_spectrum.png'}
    assert sync_run_visualizations(run) == report
    assert _native_files(methods) == before
    for model in report['models']:
        owned = EventAccumulator(str(run / model['image_run'])).Reload()
        assert len(owned.Images('figures/measurement/model/figs/spectrum.png')) == 1


def test_native_epochs_are_indexed_without_history_or_summary_and_events_remain_unchanged(tmp_path: Path) -> None:
    run = tmp_path / 'native-run'
    baseline = _native_method(run, 'control', 'baseline')
    candidate = _native_method(run, 'variant', 'candidate')
    # A model name is not its scientific role; only the actual job declares that.
    (candidate / 'config.yaml').write_text('model_name: ExistingModel\nmodel: baseline\n')
    for method in (baseline, candidate):
        figure = method / 'figs/spectrum.png'
        figure.parent.mkdir()
        Image.new('RGB', (12, 10), 'blue').save(figure)
    original = _native_files([baseline, candidate])
    report = sync_run_visualizations(run)
    models = report['models']
    assert len(models) == 2 and report['warnings'] == []
    assert report['images'] == 2
    assert [model['is_baseline'] for model in models] == [True, False]
    assert all(model['native'] and model['training_steps'] == 2
               and model['validation_points'] == 1 for model in models)
    assert all(model['label'].startswith('ExistingModel · ') for model in models)
    assert all(model['image_tags'] == ['figures/measurement/model/figs/spectrum.png'] for model in models)
    assert len({model['image_run'] for model in models}) == 2
    assert {model['scalar_run'] for model in models} == {
        (method / 'tensorboard').relative_to(run).as_posix() for method in (baseline, candidate)}
    assert sync_run_visualizations(run) == report
    assert _native_files([baseline, candidate]) == original
    for model in models:
        events = EventAccumulator(str(run / model['scalar_run'])).Reload()
        assert [event.value for event in events.Scalars('train/loss')] == [.25, .125]
        assert [event.step for event in events.Scalars('heldout/RES_db')] == [1]


def test_running_native_observations_ignore_partial_lines_and_refresh_real_counts(tmp_path: Path) -> None:
    run = tmp_path / 'running-native'
    method = _native_method(run, 'control', 'baseline')
    with (method / 'steps.jsonl').open('a') as handle:
        handle.write('{"optimizer_step": 3')
    with (method / 'epochs.jsonl').open('a') as handle:
        handle.write('{"epoch": 2')
    first = sync_run_visualizations(run)['models'][0]
    assert first['training_steps'] == 2 and first['validation_points'] == 1
    with (method / 'steps.jsonl').open('a') as handle:
        handle.write(', "training_loss": 0.0625}\n')
    with (method / 'epochs.jsonl').open('a') as handle:
        handle.write(', "optimizer_steps": 3, "RES": 7.0}\n')
    with ScalarWriter(method / 'tensorboard') as writer:
        writer.scalar('train/loss', .0625, 3)
        writer.scalar('heldout/RES_db', 7., 2)
    original = _native_files([method])
    second = sync_run_visualizations(run)['models'][0]
    assert second['id'] == first['id']
    assert second['training_steps'] == 3 and second['validation_points'] == 2
    assert _native_files([method]) == original


def test_native_epoch_discovery_rejects_external_files_and_mismatched_request_identity(tmp_path: Path) -> None:
    run = tmp_path / 'scoped-native'
    linked = _native_method(run, 'linked', 'baseline')
    external = tmp_path / 'external-steps.jsonl'
    (linked / 'steps.jsonl').replace(external)
    (linked / 'steps.jsonl').symlink_to(external)
    wrong = _native_method(run, 'wrong', 'candidate')
    request_path = wrong.parent.parent / 'job.json'
    request = json.loads(request_path.read_text())
    request['invocation_id'] = 'a-different-invocation'
    request_path.write_text(json.dumps(request))
    assert sync_run_visualizations(run)['models'] == []
    assert len(EventAccumulator(str(linked / 'tensorboard')).Reload().Scalars('train/loss')) == 2


def test_native_history_is_not_registered_twice_and_real_job_role_has_priority(tmp_path: Path) -> None:
    run = tmp_path / 'mixed-native'
    method = _native_method(run, 'candidate', 'candidate')
    (method / 'history.json').write_text(json.dumps([{'optimizer_steps': 2, 'validation_RES_db': 8., 'epoch': 1}]))
    (method / 'config.json').write_text('{"model":"baseline"}')
    original_events = {path: path.read_bytes() for path in (method / 'tensorboard').iterdir() if path.is_file()}
    models = sync_run_visualizations(run)['models']
    assert len(models) == 1 and models[0]['native'] and not models[0]['is_baseline']
    assert {path: path.read_bytes() for path in (method / 'tensorboard').iterdir() if path.is_file()} == original_events
