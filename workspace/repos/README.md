# `workspace/repos/` — 真实研究代码挂载点

> 本目录是 MARS 引用真实研究代码的位置。**真实代码不入 mars 仓**(CLAUDE.md 第 6 条),通过 `projects/<name>/repo_link.yaml` 接入。

## 怎么放代码

### 方案 A:本地路径(local_path,推荐用于开发)

```bash
# 把你的真实研究代码克隆到这里
cp -R /your/real/research/code   workspace/repos/pimc-current
# 或者用软链接(更省空间)
ln -s /your/real/research/code   workspace/repos/pimc-current
```

然后修改 `projects/moe-pimc/repo_link.yaml`:

```yaml
repo_mode: local_path
repo_path: ../../workspace/repos/pimc-current   # 相对于 projects/moe-pimc/
```

### 方案 B:Git submodule

```bash
git submodule add <git-url> workspace/repos/pimc-current
```

然后:

```yaml
repo_mode: git_submodule
repo_path: ../../workspace/repos/pimc-current
```

### 方案 C:Mirror(只读快照)

```bash
git clone --depth 1 <git-url> workspace/repos/pimc-current
```

```yaml
repo_mode: mirror
repo_path: ../../workspace/repos/pimc-current
sync_strategy: snapshot
```

## 把代码索引到 KB

放好真实代码后,跑:

```bash
python scripts/ingest_repo.py --project moe-pimc
```

这会读 `repo_link.yaml` 里的 `allowed_paths` + `ignore_patterns`,把匹配的代码文件切块写入 `knowledge/code_assets/`。Coding Agent 在 build_context 时会从这个 KB 检索相关片段。

`--dry-run` 先看会摄入哪些文件:

```bash
python scripts/ingest_repo.py --project moe-pimc --dry-run
```

## protected_paths(Gate 5 保护)

`projects/moe-pimc/repo_link.yaml::protected_paths` 列出的路径会触发 Gate 5。
默认值:

```yaml
protected_paths:
  - baseline/
  - libs/Model.py:Paper_Total_0327          # 类级别保护
  - production_interface/
```

按你真实代码的目录结构改,Gate 5 会在 Coding Agent 调 `code.patch_generator` 时
静态匹配,违反就阻塞。

## 已存在的 stub

`workspace/repos/pimc-stub/` 是 Dev E2E 用的最小骨架,**不要删**(测试和零依赖
demo 依赖它)。要切到真实代码时只改 `repo_link.yaml::repo_path` 指向另一个目录就行。
