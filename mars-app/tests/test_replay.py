from mars_app.replay import DEFAULT_MODULE, _step, code_files, code_symbols, config_changes

DIFF = {"files": [
    {"path": "configs/candidate.yaml", "patch": "\n".join([
        "--- a/configs/candidate.yaml", "+++ b/configs/candidate.yaml",
        "-# 8 hidden units", "+# 12 hidden units",
        "-  hidden: 8", "+  hidden: 12",
        "+  lr_multiplier: 5.0",
        "-  init_scale: 0.001", "+  init_scale: 0.05",
        "+    note: \"a long free-text note that should never be shown as a config change\"",
        "   depth: 6",
    ])},
    {"path": "src/model.py", "patch": "--- a\n+++ b\n-old\n+new\n+newer"},
]}


def test_config_changes_pairs_old_and_new_and_skips_comments_and_text():
    changes = {c["key"]: (c["old"], c["new"]) for c in config_changes(DIFF)}
    assert changes == {"hidden": ("8", "12"), "lr_multiplier": (None, "5.0"), "init_scale": ("0.001", "0.05")}


def test_code_files_counts_lines():
    assert code_files(DIFF)[1] == {"path": "src/model.py", "added": 2, "removed": 1}


def test_code_symbols_reads_classes_and_functions_from_hunk_headers():
    diff = {"files": [{"path": "libs/m.py", "patch": "@@ -1,2 +1,3 @@ docstring line\n+x\n@@ -9,2 +9,3 @@ class Encoder(nn.Module):\n+y\n"
                                                    "@@ -20,1 +21,2 @@ def build_model(cfg):\n+z\n@@ -30,1 +31,2 @@ class Encoder(nn.Module):\n"}]}
    assert code_symbols(diff) == ["class Encoder", "def build_model"]


def test_changes_go_under_the_proposals_first_module():
    it = {"iteration": 4, "parent_iteration": 3, "screen": {}, "verify": {}}
    step = _step(it, {"task": {"modules": ["优化器", "训练"]}}, DIFF)
    assert list(step["modules"]) == ["优化器"] and "lr_multiplier: 5.0" in step["modules"]["优化器"]
    code_only = _step(it, {"task": {}}, {"files": [{"path": "libs/m.py", "patch": "@@ -1 +1 @@ class Decoder(nn.Module):\n-a\n+b"}]})
    assert code_only["modules"] == {DEFAULT_MODULE: ["class Decoder"]}
