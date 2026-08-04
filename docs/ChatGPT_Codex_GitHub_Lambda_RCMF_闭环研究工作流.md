# ChatGPT + Codex + GitHub + Lambda Cloud：RCMF 闭环研究工作流搭建规范

> **交给 Codex 直接执行的任务书**  
> 文档日期：2026-08-04  
> 项目：Reversible Compiled Memory Field（RCMF）/ AppWorld / Qwen3-8B  
> 持久化根目录：`/lambda/nfs/rcmf-persist`
>
> 本任务不是重新创建一个空项目，也不是覆盖当前 Lambda 上的已有项目。  
> 当前 filesystem 中已经存在代码、Git 历史、模型缓存、数据、checkpoint、日志和多轮实验结果。  
> **必须先非破坏性审计，再在现有真实状态上建立 GitHub 同步、实验账本、研究文档和 ChatGPT 交接机制。**

---

## 0. 本任务的最终目标

建立如下闭环：

```text
用户 + ChatGPT
    │
    │ 研究问题、创新假设、失败解释、判别实验
    ▼
GitHub 仓库
    │
    │ 代码、配置、研究状态、实验摘要、Codex handoff
    ▼
Codex
    │
    │ 工程实现、远程运行、调试、记录、commit、push
    ▼
Lambda Cloud
    │
    │ GPU训练、评测、checkpoint、大日志、大型artifact
    ▼
GitHub 仓库
    │
    │ 小型可读结果、manifest、per-task差异、最新状态
    ▼
ChatGPT
    │
    └── 读取仓库后继续做机制分析和下一轮研究设计
```

完成后应满足：

1. GitHub 是代码、配置和研究状态的唯一可共享事实来源。
2. Lambda 是计算环境和大型 artifact 的存储位置，不是唯一代码副本。
3. ChatGPT 无需 SSH 登录 Lambda，也能从 GitHub 准确了解：
   - 当前方法实际做了什么；
   - 当前代码位于哪里；
   - baseline 和各版本结果；
   - 每次实验使用的 commit、配置、seed、命令和 checkpoint；
   - 哪些任务变好、变差或保持不变；
   - 已经排除哪些解释；
   - 下一步最值得验证什么。
4. Codex 对话不是项目事实来源。重要内容必须进入 Git、实验账本或结构化 handoff。
5. 不移动、覆盖或删除任何尚未完成审计的现有项目、checkpoint、模型缓存、数据和日志。

---

# 第一部分：角色边界

## 1. ChatGPT

ChatGPT 负责：

- 研究问题分解；
- 方法创新与相关工作对比；
- 从负结果提出机制解释；
- 设计能够区分不同解释的最小实验；
- 阅读 GitHub 中的代码、配置、实验结果和 handoff；
- 判断结果是否真的支持某项结论；
- 给出下一轮研究计划。

ChatGPT 不应被要求：

- 仅凭一段泛泛总结猜测完整代码行为；
- 直接登录 Lambda；
- 从 Codex 私有聊天历史自动恢复项目；
- 根据未经核验的口头结果作结论。

## 2. Codex

Codex 负责：

- 审计现有 Lambda filesystem 和 Git 状态；
- 实现和修改代码；
- 在 Lambda 上运行测试、训练与评估；
- 保存完整实验 artifact；
- 将可共享的小型结果和研究状态提交到 GitHub；
- 每次工作结束时写结构化 handoff；
- 明确区分事实、推测和未验证内容。

## 3. GitHub

GitHub 保存：

- 源代码；
- 配置；
- 测试；
- 运行入口；
- 研究设计文档；
- 当前状态；
- 实验账本；
- 小型结果摘要；
- per-task 成败差异；
- Codex handoff；
- 复现命令；
- 指向 Lambda artifact 的路径和 checksum。

GitHub 不保存：

- Hugging Face 模型缓存；
- AppWorld 大型数据；
- 完整 checkpoint；
- 大型日志；
- TensorBoard event；
- 密钥、token、`.env`；
- Lambda SSH 私钥；
- 任何不应公开的内部数据。

## 4. Lambda Cloud

Lambda 保存：

- 实际训练环境；
- pretrained model cache；
- benchmark 数据；
- checkpoint；
- 完整日志；
- 完整 per-task 输出；
- TensorBoard 数据；
- 大型中间结果；
- 可重建但不适合进入 GitHub 的 artifact。

## 5. 用户

以下操作必须由用户完成或明确授权：

- 提供当前 Lambda Public IP；
- 在 GitHub 创建仓库或批准 `gh` 创建仓库；
- 完成 GitHub OAuth / SSH / credential 授权；
- 确认是否公开仓库；
- 在 Lambda 控制台 Terminate 实例；
- 批准删除 checkpoint、缓存、数据或旧项目；
- 批准会产生明显 GPU 费用的长训练。

绝不向用户索要 GitHub 密码、PAT 明文、SSH 私钥内容或验证码。

---

# 第二部分：已知环境与需要核验的初始事实

## 6. 已知环境

```text
Cloud provider: Lambda Cloud
Remote OS: Ubuntu / Lambda Stack
SSH user: ubuntu
Persistent filesystem: /lambda/nfs/rcmf-persist
Current project family: RCMF / AppWorld / Qwen3-8B
Current GPU historically used: single H100 80GB SXM5
```

现有约定可能包括：

```text
/lambda/nfs/rcmf-persist/
├── project/
├── data/
├── hf-cache/
├── cache/
├── runs/
├── artifacts/
├── bootstrap/
├── secrets/
└── env.sh
```

但不得假定当前状态完全等于上述结构。必须实际检查。

## 7. 当前研究结果的 provisional 信息

以下内容来自用户当前描述，只能作为迁移线索，必须用现有结果文件、日志和评估输出核验：

```text
Bare Qwen3-8B baseline:
- full evaluation: 53 / 168 = 31.55%
- first 10 tasks: 3 / 10 = 30%

Earlier low-perturbation checkpoint:
- step 100, first 10: 3 / 10
- success set与baseline不同
- final step约638，first 10降至2 / 10

Current implemented version:
- 已经不是0%正确率
- 但整体表现约比baseline低50%
```

迁移后必须在 `research/CURRENT_STATE.md` 中写出：

- 哪些数字已从原始结果核验；
- 对应 run ID；
- 对应 Git commit；
- 对应 config；
- 对应 checkpoint；
- 对应完整评测文件；
- 哪些仍然只是用户回忆或估计。

未经核验，不得把“约低50%”转换为精确数值。

---

# 第三部分：最高优先级安全约束

## 8. Persistent Filesystem 检查

任何读取之外的操作、安装、下载、修改、训练或迁移之前必须执行：

```bash
set -euo pipefail

test -d /lambda/nfs/rcmf-persist
mountpoint -q /lambda/nfs/rcmf-persist

touch /lambda/nfs/rcmf-persist/.codex_workflow_write_test
rm /lambda/nfs/rcmf-persist/.codex_workflow_write_test
```

失败时立即停止。

不得把重要文件的唯一副本放入：

```text
/home/ubuntu
/tmp
实例根盘
```

## 9. 禁止操作

未经用户明确授权，禁止：

```bash
rm -rf /lambda/nfs/rcmf-persist
rm -rf /lambda/nfs/rcmf-persist/project
git reset --hard
git clean -fdx
git push --force
git rebase --onto
sudo shutdown
sudo poweroff
sudo reboot
chmod -R 777 /lambda/nfs/rcmf-persist
```

也禁止：

- 删除或移动未知用途的现有目录；
- 覆盖现有 `env.sh`；
- 覆盖已有 Git remote；
- 删除旧 branch、tag、stash；
- 清空 Hugging Face cache；
- 删除 checkpoint；
- 重写已经存在的 Git 历史；
- 将 token、密钥或 `.env` 加入 Git；
- 未经批准启动完整长训练；
- 仅因为目录看似重复就删除；
- 将现有 dirty working tree 强制恢复。

## 10. 最小修改原则

- 优先保留现有变量名、类名、函数名和文件结构。
- 工作流层尽量以新增 `research/` 和 `tools/research_ops/` 实现。
- 不为了“目录更漂亮”大规模重构已有代码。
- 旧路径可通过 manifest 和映射记录，不必立即搬迁。
- 所有迁移操作必须可回退。

---

# 第四部分：Phase A——非破坏性审计

## 11. 审计要求

先创建一个时间戳：

```bash
export RCMF_PERSIST=/lambda/nfs/rcmf-persist
export AUDIT_TS="$(date -u +%Y%m%dT%H%M%SZ)"
export AUDIT_DIR="$RCMF_PERSIST/artifacts/workflow_migration/$AUDIT_TS"
mkdir -p "$AUDIT_DIR"
```

执行只读审计，将输出保存到 `$AUDIT_DIR`。不得先移动文件。

### 11.1 环境与挂载

```bash
{
  date -u
  hostname
  whoami
  uname -a
  nvidia-smi || true
  findmnt /lambda/nfs/rcmf-persist || true
  df -h /lambda/nfs/rcmf-persist || true
  du -sh /lambda/nfs/rcmf-persist || true
} | tee "$AUDIT_DIR/environment.txt"
```

### 11.2 顶层目录和空间使用

```bash
{
  echo "=== top-level ==="
  find "$RCMF_PERSIST" -mindepth 1 -maxdepth 2 \
    -printf '%y %p\n' 2>/dev/null | sort

  echo "=== size ==="
  du -sh "$RCMF_PERSIST"/* 2>/dev/null | sort -h
} | tee "$AUDIT_DIR/filesystem_inventory.txt"
```

不得对大型 cache 递归生成数百万行清单。只对候选代码目录做进一步检查。

### 11.3 查找所有 Git 仓库

```bash
find "$RCMF_PERSIST" \
  -type d -name .git \
  -not -path '*/hf-cache/*' \
  -not -path '*/cache/*' \
  -not -path '*/data/*' \
  -not -path '*/runs/*' \
  2>/dev/null \
  | sed 's#/.git$##' \
  | sort \
  | tee "$AUDIT_DIR/git_repositories.txt"
```

对每个仓库保存：

```bash
while IFS= read -r repo; do
  [ -n "$repo" ] || continue
  safe_name="$(printf '%s' "$repo" | sed 's#/#__#g')"
  {
    echo "REPO=$repo"
    git -C "$repo" status --short --branch || true
    git -C "$repo" remote -v || true
    git -C "$repo" branch -vv || true
    git -C "$repo" tag --list || true
    git -C "$repo" log --oneline --decorate --graph -30 || true
    git -C "$repo" stash list || true
  } > "$AUDIT_DIR/git_${safe_name}.txt"
done < "$AUDIT_DIR/git_repositories.txt"
```

### 11.4 运行中的任务

```bash
{
  tmux ls || true
  ps -u "$USER" -f || true
  nvidia-smi || true
} | tee "$AUDIT_DIR/active_processes.txt"
```

若有训练正在运行：

- 不改变 branch；
- 不修改其正在读取的配置；
- 不移动输出目录；
- 记录 PID、命令、cwd、日志路径和 checkpoint 路径；
- 先完成工作流中不影响当前任务的部分。

### 11.5 环境定义

只检查路径和是否存在，不输出 secret：

```bash
{
  test -f "$RCMF_PERSIST/env.sh" && echo "env.sh: present" || echo "env.sh: absent"
  test -f "$RCMF_PERSIST/bootstrap/bootstrap_instance.sh" \
    && echo "bootstrap: present" || echo "bootstrap: absent"
  test -d "$RCMF_PERSIST/secrets" && echo "secrets dir: present" || true
  test -d "$RCMF_PERSIST/hf-cache" && echo "hf-cache: present" || true
} | tee "$AUDIT_DIR/persistent_environment.txt"
```

可以读取 `env.sh` 的非敏感路径定义，但不得回显任何 token 值。

## 12. 审计后的第一个交付物

创建：

```text
<当前主项目仓库>/research/migration/INITIAL_AUDIT.md
```

至少包含：

- 实际主项目路径；
- 是否存在多个候选项目；
- 哪个仓库包含最新代码；
- 当前 branch 和 HEAD；
- working tree 是否 dirty；
- 是否有 remote；
- remote 指向哪里；
- Git 是在 Lambda 上还是另有本地副本；
- 主要代码、配置、训练入口、评估入口；
- 模型、数据、checkpoint、日志、结果实际位置；
- 正在运行的任务；
- 旧版本和当前版本的定位；
- 风险和需要用户确认的事项。

若无法唯一判断哪个目录是当前主项目，停止任何 GitHub 迁移，只向用户展示候选目录及证据。

---

# 第五部分：Phase B——保护现有状态

## 13. 选择主项目前不得合并目录

可能出现：

```text
/lambda/nfs/rcmf-persist/project
/lambda/nfs/rcmf-persist/project_old
/lambda/nfs/rcmf-persist/rcmf
/lambda/nfs/rcmf-persist/.../another_git_repo
```

不得因为约定路径是 `project` 就认定其他目录无用。

主项目的判定证据：

1. 最近训练日志中的 cwd；
2. 当前运行进程的 cwd；
3. 最新 checkpoint manifest 或命令；
4. Git 最近 commit；
5. 用户当前版本实际调用的源码路径；
6. 配置中的 module path；
7. 最近结果中的 commit 或代码快照。

## 14. Dirty working tree 保护

若当前主项目存在未提交修改：

1. 先检查 `.gitignore`；
2. 区分源码、配置与大型生成文件；
3. 不执行 `git add -A`；
4. 先把不应提交内容加入 `.gitignore`；
5. 保存 patch：

```bash
mkdir -p "$AUDIT_DIR/git_safety"
git -C "$MAIN_REPO" diff \
  > "$AUDIT_DIR/git_safety/tracked_changes.patch"

git -C "$MAIN_REPO" status --porcelain=v1 \
  > "$AUDIT_DIR/git_safety/status.txt"
```

6. 对属于项目的源码和配置，创建迁移前安全 commit；
7. commit message 示例：

```text
chore(migration): preserve pre-workflow RCMF state
```

8. 不将 cache、data、checkpoint、日志或 secret 纳入该 commit。

如果无法确认某个 untracked 文件是否重要，保留原位并记录，不删除。

## 15. Git 历史保护

若仓库已有历史：

- 保留全部 commit、branch、tag 和 stash；
- 不 squash；
- 不 rebase；
- 不建立一个覆盖旧历史的全新 `.git`；
- 可创建 tag：

```bash
git tag -a pre-research-workflow-20260804 \
  -m "State before ChatGPT-Codex-GitHub-Lambda workflow migration"
```

若仓库没有 Git，才允许在确认主项目后执行 `git init`。

---

# 第六部分：Phase C——GitHub 远程仓库

## 16. GitHub 仓库要求

目标仓库建议：

```text
https://github.com/<USER_OR_ORG>/rcmf-research
```

实际名称由用户决定。

仓库可公开，但公开前必须扫描：

- token；
- `.env`；
- API key；
- SSH key；
- 内部 hostname；
- 私有数据；
- 含凭据的日志；
- 误保存的 prompt 或用户数据。

## 17. GitHub 授权规则

Codex 不得要求用户发送：

- GitHub 密码；
- Personal Access Token 明文；
- MFA 验证码；
- SSH 私钥内容。

允许的方式：

1. 用户在 GitHub 网页创建空仓库并提供 URL；
2. 本机已完成 `gh auth login`，Codex 检查后使用；
3. 已配置 GitHub SSH key；
4. Git credential manager 已授权。

先检查：

```bash
git remote -v
gh auth status 2>/dev/null || true
ssh -T git@github.com 2>&1 | sed -n '1,8p' || true
```

不得打印 credential。

## 18. Remote 迁移策略

### 情况 A：已有正确 GitHub remote

验证 owner、repo 和权限后：

```bash
git fetch --all --prune
git status
```

不得直接 pull 合并未知远程历史。先比较：

```bash
git log --oneline --left-right --graph HEAD...origin/main
```

### 情况 B：已有 remote，但不是目标 GitHub

不要覆盖原 remote。保留原名，例如：

```bash
git remote rename origin legacy-origin
git remote add origin <NEW_GITHUB_URL>
```

### 情况 C：没有 remote

```bash
git remote add origin <NEW_GITHUB_URL>
```

### 情况 D：GitHub 仓库尚未创建

若 `gh auth status` 正常，且用户已授权创建：

```bash
gh repo create <OWNER>/rcmf-research \
  --public \
  --source "$MAIN_REPO" \
  --remote origin
```

否则停止并只向用户请求：

```text
请创建一个空的 GitHub 仓库，并提供 repository URL。
不要提供密码、token 或私钥。
```

## 19. 首次 push

首次 push 前必须：

```bash
git status --short
git ls-files | sed -n '1,240p'
git check-ignore -v <candidate_large_or_sensitive_file> || true
```

建议先运行 secret scan。若环境没有扫描工具，至少使用受控关键词搜索，不打印匹配值：

```bash
git grep -IlE \
  '(api[_-]?key|access[_-]?token|secret[_-]?key|BEGIN .*PRIVATE KEY)' \
  -- . \
  > "$AUDIT_DIR/possible_secret_files.txt" || true
```

人工检查文件名和上下文。确认安全后：

```bash
git push -u origin <CURRENT_BRANCH>
```

不得 force push。

---

# 第七部分：GitHub 仓库中的研究状态层

## 20. 新增目录

在不破坏现有源码结构的情况下新增：

```text
<repo>/
├── AGENTS.md
├── REPO_MAP.md
├── research/
│   ├── CHATGPT_ENTRYPOINT.md
│   ├── VISION.md
│   ├── ARCHITECTURE.md
│   ├── CURRENT_STATE.md
│   ├── EVALUATION_CONTRACT.md
│   ├── DECISIONS.md
│   ├── FAILURE_ANALYSIS.md
│   ├── NEXT_EXPERIMENTS.md
│   ├── EXPERIMENT_SCHEMA.md
│   ├── experiments.jsonl
│   ├── handoffs/
│   ├── migration/
│   ├── results/
│   └── templates/
└── tools/
    └── research_ops/
        ├── audit_workspace.sh
        ├── start_run.py
        ├── finalize_run.py
        ├── collect_snapshot.py
        ├── backfill_run.py
        └── validate_research_state.py
```

现有项目已有同名文件时，先读取并合并，不能覆盖。

## 21. `AGENTS.md`

必须写入长期约束：

```markdown
# Codex Rules for This Repository

This repository is jointly used by ChatGPT for research analysis and Codex
for implementation and experimentation.

Before ending any substantial task, Codex must:

1. Preserve existing code, Git history, runs, checkpoints and logs.
2. Commit intended source and config changes.
3. Update research/CURRENT_STATE.md when the active method or result changes.
4. Append every completed, failed or aborted experiment to
   research/experiments.jsonl.
5. Create a structured handoff in research/handoffs/.
6. Record exact commit, config, seed, command, metrics and Lambda artifact paths.
7. Record per-task success-set changes against the locked baseline.
8. Separate VERIFIED facts, INFERENCES and UNVERIFIED claims.
9. Record implementation deviations and workarounds in research/DECISIONS.md.
10. Never silently simplify or replace a research mechanism.
11. Push the completed commit to the configured GitHub remote.
12. Never commit secrets, model caches, datasets, checkpoints or large logs.

A prose-only summary is not a valid handoff.
Codex chat history is not a source of truth.
```

同时保留用户已有的代码修改偏好：

- 最小范围修改；
- 不无故更改变量名、类名、函数名、格式和排版；
- 保持输入输出接口；
- 对研究机制的近似必须显式记录。

## 22. `REPO_MAP.md`

根据真实代码生成，不得套模板猜测。至少包含：

```text
用户命令
→ CLI入口
→ config解析
→ dataset/preprocessing
→ prompt构造
→ model加载
→ RCMF write/read/injection
→ loss
→ optimizer
→ checkpoint
→ generation
→ AppWorld execution
→ evaluation parser
→ result writer
```

每一项列出：

- 文件路径；
- 类或函数；
- 输入；
- 输出；
- 关键配置；
- 当前是否在正式 pipeline 中调用；
- legacy 但未使用的实现。

## 23. `VISION.md`

保留原始研究目标，不把当前实现当成目标本身。至少写：

- RCMF 要解决的问题；
- 相对 RAG、文本 memory、LoRA/adapter、普通 fast weights 的差异；
- experience-to-behavior memory 的定位；
- 可逆 add/remove/replace；
- 固定形状 memory state；
- 推理时不插入原始 memory 文本；
- 当前论文主张和不主张的内容；
- 当前关键假设。

## 24. `ARCHITECTURE.md`

必须基于代码，而不是旧设计文档。包含：

- 当前实际模块；
- tensor shape；
- write path；
- read path；
- injection path；
- trainable/frozen 参数；
- loss；
- data flow；
- generation；
- checkpoint；
- 与原始设计的偏差表。

建议表格：

| 设计项 | 原始意图 | 当前实现 | 代码位置 | 偏差影响 |
|---|---|---|---|---|

## 25. `CURRENT_STATE.md`

这是 ChatGPT 接手时首先阅读的文件。固定结构：

```markdown
# Current Research State

## 1. Snapshot
- Date:
- Branch:
- Commit:
- Lambda project path:
- Current best checkpoint:
- Current best run:
- Locked baseline run:

## 2. Verified Pipeline
明确哪些环节被独立验证。

## 3. Unverified Pipeline
尚未证明正确的环节。

## 4. Baseline
完整配置、命令、53/168等已核验数字。

## 5. Current Method
方法、配置、结果、checkpoint。

## 6. Per-task Delta
retained / gained / lost / both failed。

## 7. Training Dynamics
各checkpoint结果、loss、drift和异常。

## 8. Confirmed Conclusions
只能写证据直接支持的结论。

## 9. Open Hypotheses
例如训练信号无效、更新过强、局部提升全局损害等。

## 10. Immediate Next Experiments
按信息增益/成本排序。

## 11. Reproduction
一条baseline命令和一条当前方法命令。

## 12. Artifact Index
Lambda绝对路径、大小、checksum。
```

## 26. `DECISIONS.md`

采用 append-only 决策日志：

```markdown
## DEC-YYYYMMDD-NNN: <title>
- Status: proposed | accepted | superseded | rejected
- Context:
- Decision:
- Alternatives:
- Reason:
- Code impact:
- Experimental evidence:
- Reversible:
```

所有工程 workaround 必须记录，包括：

- 为避免模型胡言乱语而加入的低扰动约束；
- 冻结或解冻哪些参数；
- 注入规模限制；
- prompt 格式改变；
- evaluator/parser 修复；
- 与原始数学定义不同的实现。

## 27. `FAILURE_ANALYSIS.md`

不要仅记录“效果不好”。每项负结果写：

```markdown
## Failure F-XXX
- Run:
- Commit:
- Intended hypothesis:
- Observed result:
- Baseline:
- Per-task changes:
- Failure class:
  pipeline | optimization | objective | representation |
  interference | evaluation | data | unknown
- Evidence:
- Explanations ruled out:
- Explanations still possible:
- Cheapest discriminating experiment:
```

## 28. `NEXT_EXPERIMENTS.md`

每个实验必须是可判别假设，不是随意调参：

```markdown
## EXP-PROPOSAL-XXX
- Hypothesis:
- Why it fits current observations:
- Minimal code change:
- Controlled variables:
- Independent variable:
- Metrics:
- Expected result if true:
- Expected result if false:
- Stop condition:
- Estimated GPU cost:
- Priority:
```

---

# 第八部分：实验账本与 artifact 设计

## 29. 不强制搬迁旧 runs

现有 filesystem 可能已经使用：

```text
runs/checkpoints/
runs/logs/
runs/results/
runs/tensorboard/
```

不要为了新规范移动旧数据。

新增一个轻量 manifest 层：

```text
/lambda/nfs/rcmf-persist/runs/manifests/<run_id>/
├── manifest.json
├── command.sh
├── resolved_config.yaml
├── git.patch
├── environment.txt
├── metrics.json
├── per_task.json
├── comparison_to_baseline.json
├── log_tail.txt
└── artifact_index.json
```

manifest 中引用旧的 checkpoint、log、result 绝对路径即可。

如 `env.sh` 已存在，只做最小追加：

```bash
export RCMF_MANIFESTS="$RCMF_RUNS/manifests"
```

不得整体覆盖 `env.sh`。

## 30. Run ID

建议：

```text
<method>__<benchmark>__<split>__s<seed>__<YYYYMMDDTHHMMSSZ>
```

例如：

```text
rcmf_lowperturb__appworld__test__s0__20260804T021500Z
```

run ID 必须唯一且不得复用。

## 31. `manifest.json`

至少包含：

```json
{
  "schema_version": 1,
  "run_id": "...",
  "status": "running|completed|failed|aborted",
  "created_at_utc": "...",
  "finished_at_utc": null,
  "hypothesis_id": "EXP-PROPOSAL-XXX",
  "method": "...",
  "benchmark": "appworld",
  "split": "...",
  "seed": 0,
  "git_commit": "...",
  "git_branch": "...",
  "git_dirty": false,
  "config_path": "...",
  "config_sha256": "...",
  "command_path": "...",
  "parent_run_id": null,
  "baseline_run_id": "...",
  "checkpoint_path": null,
  "log_path": "...",
  "result_path": "...",
  "metrics": {},
  "notes": ""
}
```

## 32. `research/experiments.jsonl`

每个 run 一行，小型且可提交 GitHub：

```json
{
  "run_id": "...",
  "status": "completed",
  "commit": "...",
  "hypothesis": "...",
  "change": "...",
  "baseline_run": "...",
  "primary_metric": {
    "name": "success_rate",
    "value": 0.1548,
    "numerator": 26,
    "denominator": 168
  },
  "baseline_metric": {
    "value": 0.3155,
    "numerator": 53,
    "denominator": 168
  },
  "retained": 0,
  "gained": 0,
  "lost": 0,
  "artifact_manifest": "/lambda/nfs/rcmf-persist/runs/manifests/...",
  "result_summary": "research/results/<run_id>.md"
}
```

失败、崩溃和中止实验也必须记录，`status` 不得伪装为 completed。

## 33. 大型 artifact 索引

`artifact_index.json` 每项包含：

```json
{
  "type": "checkpoint|log|result|tensorboard|dataset|other",
  "path": "/lambda/nfs/rcmf-persist/...",
  "size_bytes": 123,
  "sha256": "...",
  "exists": true
}
```

对数十 GB checkpoint 计算 checksum 可能耗时。可以：

- 首次迁移只记录 path、size、mtime；
- 对正式论文结果再补 checksum；
- 不因为 checksum 阻塞当前运行。

---

# 第九部分：实现 research_ops 工具

## 34. 总体要求

使用 Python 标准库优先，避免为记录工具引入大型依赖。

工具必须：

- 对路径做显式校验；
- 不读取 secret；
- 原子写 JSON；
- 对中断有清晰状态；
- 不覆盖已有 run；
- 输出可读错误；
- 支持 `--dry-run`；
- 支持旧实验 backfill；
- 在 Git dirty 时默认警告。

## 35. `audit_workspace.sh`

实现第四部分审计并生成：

```text
research/migration/INITIAL_AUDIT.md
```

以及 Lambda 上的完整审计输出。

## 36. `start_run.py`

示例：

```bash
python tools/research_ops/start_run.py \
  --run-id "$RUN_ID" \
  --hypothesis EXP-PROPOSAL-001 \
  --method rcmf_lowperturb \
  --benchmark appworld \
  --split test \
  --seed 0 \
  --config configs/...yaml \
  --baseline-run qwen3_8b__appworld__test__s0__... \
  --command-file /path/to/command.sh
```

自动记录：

- UTC 时间；
- repo root；
- branch；
- commit；
- dirty 状态；
- `git diff`；
- config copy 和 SHA256；
- command；
- Python version；
- `pip freeze`；
- PyTorch/CUDA/GPU；
- hostname；
- relevant environment variable paths；
- run manifest。

默认要求训练前代码已有 commit。

允许 `--allow-dirty`，但必须：

- 保存 `git.patch`；
- 在 manifest 标记；
- handoff 中说明原因。

## 37. `finalize_run.py`

示例：

```bash
python tools/research_ops/finalize_run.py \
  --run-id "$RUN_ID" \
  --status completed \
  --metrics-file /path/to/metrics.json \
  --per-task-file /path/to/per_task.json \
  --checkpoint /path/to/checkpoint \
  --log /path/to/log
```

自动：

- 更新 manifest；
- 记录结束时间；
- 检查 metric denominator；
- 与 baseline 做 per-task join；
- 生成 retained / gained / lost / both_failed；
- 写 `comparison_to_baseline.json`；
- 生成 `research/results/<run_id>.md`；
- append `research/experiments.jsonl`；
- 防止重复 append；
- 生成 log tail，但不提交完整大日志；
- 更新 artifact index。

## 38. `collect_snapshot.py`

生成 ChatGPT 可读快照：

```text
research/results/<run_id>.md
```

包含：

- 方法摘要；
- 与 baseline 的唯一变量；
- commit 和 config；
- aggregate metric；
- retained / gained / lost；
- 训练 checkpoint sweep；
- 代表性输出；
- 错误类型统计；
- artifact 路径；
- 已知 caveat。

不得复制大量 copyrighted dataset 或完整长轨迹。只保存研究所需的最小代表性片段。

## 39. `backfill_run.py`

用于把现有旧实验纳入账本：

```bash
python tools/research_ops/backfill_run.py \
  --run-id ... \
  --commit ... \
  --config ... \
  --metrics-file ... \
  --per-task-file ... \
  --checkpoint ... \
  --log ...
```

若旧实验缺少 commit/config：

```text
verification_status: partial
```

不得虚构。

## 40. `validate_research_state.py`

检查：

- 必需文档存在；
- `experiments.jsonl` 每行合法 JSON；
- run ID 唯一；
- result summary 存在；
- GitHub 中没有绝对 secret 路径内容；
- 当前 baseline run 存在；
- `CURRENT_STATE.md` 中 commit 与 HEAD 是否一致；
- handoff 是否包含起止 commit；
- Git 不跟踪大型 cache/checkpoint；
- 每个 completed run 有 metric；
- numerator / denominator / rate 一致。

返回非零 exit code 表示不满足交接条件。

---

# 第十部分：RCMF 专用评估契约

## 41. 创建 `research/EVALUATION_CONTRACT.md`

baseline 和 RCMF 必须固定：

- 相同 Qwen3-8B base model revision；
- 相同 tokenizer；
- 相同 AppWorld task IDs；
- 相同 evaluator；
- 相同 prompt profile，除非 prompt 本身是独立变量；
- 相同 generation 参数；
- 相同 thinking 开关；
- 相同 max tokens；
- 相同 tool execution 限制；
- 相同 pass@k 定义；
- 相同 seed 处理；
- 相同 timeout；
- 相同 parser；
- 相同失败计数规则。

任何不同都必须显式列为实验变量。

## 42. Baseline 锁定

迁移中优先 backfill 并锁定 bare Qwen3-8B baseline。

至少记录：

```text
baseline_run_id
model revision
task list hash
prompt hash
generation config
evaluator version
53/168 原始结果文件
first-10 结果
per-task success set
```

在 baseline 未核验前：

- 可以继续搭建工作流；
- 不应对当前 RCMF 的相对下降作精确机制结论；
- 不应将不同 evaluator 产生的结果直接比较。

## 43. 必须保存 per-task 结果

每道题至少：

```json
{
  "task_id": "...",
  "success": true,
  "score": 1.0,
  "steps": 7,
  "termination": "completed",
  "format_valid": true,
  "tool_error_count": 0,
  "output_path": "...",
  "trace_path": "..."
}
```

比较输出：

```text
retained：baseline和方法都成功
gained：baseline失败、方法成功
lost：baseline成功、方法失败
both_failed：两者都失败
```

总正确率相同但 success set 不同，是重要研究信号，不能只看 aggregate。

## 44. Checkpoint sweep

对于训练模型，正式结论不能只使用 final checkpoint。至少支持：

- step 0 / initialization；
- early checkpoint；
- middle checkpoint；
- final checkpoint；
- best validation checkpoint。

记录：

- first-10；
- full 168；
- success-set delta；
- train loss；
- update magnitude；
- held-out drift。

## 45. 为 ChatGPT 收集的最低诊断集

在不显著增加成本的情况下，正式 run 尽量保存：

1. trainable 参数名称和数量；
2. frozen 参数名称和数量；
3. 每层或模块 update norm；
4. update norm / parameter norm；
5. gradient norm；
6. held-out token KL 或 logit drift；
7. format validity；
8. tool-call validity；
9. generation length；
10. per-task retained/gained/lost；
11. 各 checkpoint 的指标；
12. 随机种子；
13. 训练数据和 eval task ID hash；
14. 数据泄漏检查结果。

这些信息用于区分：

```text
pipeline错误
训练信号错误
学习率/累计更新过强
局部能力提升但通用能力损坏
输出格式漂移
工具调用能力损坏
评估器问题
数据分布问题
方法假设不成立
```

---

# 第十一部分：Codex 会话交接

## 46. Codex 对话不可作为唯一记录

ChatGPT 当前不会自动读取用户与 Codex 的全部历史聊天。

因此：

> 任何只存在于 Codex 对话、但没有进入 commit、实验账本、决策日志或 handoff 的信息，均视为尚未完成交接。

## 47. 每次创建 handoff

文件名：

```text
research/handoffs/YYYYMMDDTHHMMSSZ_<short_task>.md
```

模板：

```markdown
# Codex Session Handoff

## Session Metadata
- Date:
- User request:
- Lambda project path:
- Starting branch:
- Starting commit:
- Ending branch:
- Ending commit:

## 1. Requested Goal
准确保存用户要求和约束。

## 2. Initial State
开始时的代码、run、进程和问题。

## 3. Files Inspected
列出关键文件、函数、配置、日志和checkpoint。

## 4. Changes Made
逐文件说明修改和原因。

## 5. Intended Method vs Actual Implementation
说明是否偏离研究设计。

## 6. Commands Executed
保留关键、可复现命令。

## 7. Validation
compile / unit test / smoke / train / eval。

## 8. Results
run ID、metric、baseline、success-set差异。

## 9. Failed Attempts
尝试、表现、是否回滚、学到什么。

## 10. Engineering Workarounds
为了跑通而做的近似或临时方案。

## 11. Research-Relevant Observations
可能影响创新性或机制判断的现象。

## 12. Unresolved Questions for ChatGPT
需要研究分析的问题。

## 13. Exact Reproduction
从commit到结果的完整命令。

## 14. Artifact References
manifest、checkpoint、log、result、sample路径。

## 15. GitHub State
- Commit pushed:
- Remote:
- Branch:
- Working tree clean:
```

禁止只写：

```text
pipeline已跑通
效果不太好
修了一些bug
```

必须给出可核验信息。

---

# 第十二部分：现有历史的一次性迁移

## 48. 历史迁移文件

创建：

```text
research/migration/HISTORICAL_CODEX_HANDOFF.md
research/migration/LEGACY_RUN_INDEX.md
```

数据来源：

1. 现有 Git commit；
2. branch/tag/stash；
3. 配置；
4. 日志；
5. checkpoint 名称和 metadata；
6. result JSON；
7. 当前 Codex 能访问的历史上下文；
8. 用户提供的旧设计文档；
9. 文件 mtime，只作辅助，不作最终证据。

每条历史信息标记：

```text
VERIFIED
PARTIALLY_VERIFIED
UNVERIFIED
```

## 49. 优先 backfill 的实验

至少 backfill：

1. bare Qwen3-8B baseline：53/168；
2. baseline first-10：3/10；
3. 低扰动 step 100 first-10；
4. 低扰动 final checkpoint first-10；
5. 当前约为 baseline 一半的版本；
6. 能代表 pipeline 从 0% 修复到非零的关键版本。

若找不到完整 168 结果，不得用 first-10 推断完整结果。

## 50. 当前版本代码定位

必须回答：

- 当前训练实际 import 哪个 module；
- 当前评估实际加载哪个 checkpoint；
- `algorithm_final.py` 或类似入口指向哪个实现；
- 是否存在同名但未使用的旧文件；
- Lambda 上执行时 cwd 是什么；
- 环境变量或 `PYTHONPATH` 是否导致加载不同副本；
- 训练和评估是否使用同一代码版本；
- checkpoint config 是否与当前源码兼容。

---

# 第十三部分：日常闭环

## 51. ChatGPT 提出研究计划

ChatGPT 的下一轮建议应进入：

```text
research/NEXT_EXPERIMENTS.md
```

或由用户把建议原文交给 Codex。

Codex 开始前必须：

- 阅读 `CHATGPT_ENTRYPOINT.md`；
- 阅读 `CURRENT_STATE.md`；
- 阅读 `NEXT_EXPERIMENTS.md`；
- 阅读最新 handoff；
- 检查 GitHub HEAD 与 Lambda HEAD；
- 不在旧 commit 上误启动实验。

## 52. Codex 实现

```text
1. 创建 research/<experiment_slug> branch
2. 做最小代码修改
3. 增加或更新测试
4. 运行 compile/test/smoke
5. commit
6. start_run
7. 启动 Lambda 实验
8. finalize_run
9. 更新 research 文档
10. 写 handoff
11. validate_research_state
12. commit + push
```

## 53. 实验开始前

必须记录：

- hypothesis；
- 唯一主要变量；
- baseline；
- commit；
- config；
- seed；
- 预计成本；
- stop condition。

不得先跑完再编写假设。

## 54. 实验结束后

即使结果为负，也必须：

- finalize；
- 保存 per-task；
- 更新 failure analysis；
- 写 handoff；
- push。

负结果不得丢弃，因为它们是 ChatGPT 判断下一步的重要证据。

## 55. ChatGPT 接手入口

创建 `research/CHATGPT_ENTRYPOINT.md`，内容尽量短：

```markdown
# ChatGPT Entry Point

Read in this order:
1. research/CURRENT_STATE.md
2. research/ARCHITECTURE.md
3. REPO_MAP.md
4. research/EVALUATION_CONTRACT.md
5. research/FAILURE_ANALYSIS.md
6. research/NEXT_EXPERIMENTS.md
7. latest research/handoffs/*.md
8. relevant source files and configs

Current question:
...

Do not assume:
- pipeline correctness beyond verified items
- final checkpoint is best
- aggregate accuracy captures all changes
- old design document equals current implementation
```

---

# 第十四部分：分支、commit 与 push 规范

## 56. Branch

建议：

```text
main
research/<short_experiment>
fix/<pipeline_issue>
workflow/research-loop
```

工作流搭建使用：

```text
workflow/research-loop
```

不要在有未提交实验修改时随意切 branch。

## 57. Commit

建议拆分：

```text
chore(workflow): audit existing Lambda and Git state
chore(workflow): add research state documents
feat(research-ops): add run manifest tooling
docs(research): backfill baseline and current RCMF state
chore(workflow): add Codex handoff validation
```

不要把所有改动压成一个无法审查的 commit。

## 58. Push

每个实质阶段完成后 push。最终确保：

```bash
git status --short
git log -5 --oneline
git remote -v
git push
```

不得声称已 push，除非命令成功并可从 remote fetch 到该 commit。

---

# 第十五部分：`.gitignore`

## 59. 在保留现有规则的基础上补充

```gitignore
# Secrets
.env
.env.*
*.pem
*.key
secrets/
**/secrets/

# Python
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.venv/
venv/

# Model and data
hf-cache/
data/
datasets/
models/
*.safetensors
*.bin
*.pt
*.pth
*.ckpt

# Large runtime artifacts
runs/
artifacts/
tensorboard/
wandb/
outputs/
checkpoints/
logs/

# Local machine
.vscode/
.idea/
.DS_Store
Thumbs.db
```

但如果源码仓库中已有需要跟踪的小型 `outputs/example` 或测试 fixture，不得机械忽略。先检查。

允许提交：

- `research/results/*.md`；
- 小型 JSON summary；
- 实验 manifest 的 GitHub-safe 副本；
- 小型 per-task success/failure table；
- config；
- command；
- schema；
-测试 fixture。

---

# 第十六部分：工作流搭建期间不要做的研究改动

## 60. 范围控制

本任务的第一目标是建立可靠闭环，不是同时重新设计 RCMF。

除非为验证工作流所必需，不要：

- 改训练 objective；
- 改 memory architecture；
- 大规模调参；
- 替换 evaluator；
- 更换数据 split；
- 启动完整训练；
- 删除旧实现；
- 宣称解决当前性能下降。

允许：

- 修复明确阻碍记录或复现的 bug；
- 增加只读诊断；
- 增加 run manifest；
- 增加 deterministic metadata；
- 增加小规模 smoke test；
- backfill 现有结果。

任何研究行为变化必须单独 commit，不得混入 workflow commit。

---

# 第十七部分：验收测试

## 61. 必须完成的 dry run

无需启动昂贵完整训练。选择：

- mock；
- 单个 AppWorld task；
- 1–2 step smoke；
- 现有 checkpoint 的单题评估。

完成：

```text
start_run
→ 实际命令
→ finalize_run
→ per-task comparison
→ result summary
→ experiments.jsonl
→ handoff
→ validate_research_state
→ commit
→ push
```

## 62. 必须 backfill 一个真实旧实验

优先 baseline 53/168。证明：

- 旧结果可以进入账本；
- artifact 仍保留在 Lambda；
- GitHub 中只保存小型摘要；
- ChatGPT 可以从仓库追到真实路径和 commit。

## 63. 最终验收标准

```text
[ ] Persistent filesystem已核验
[ ] 未删除或覆盖现有目录
[ ] 所有Git仓库已列出
[ ] 当前主项目已用证据确认
[ ] 现有Git历史已保留
[ ] dirty state已安全处理
[ ] GitHub remote已配置
[ ] 目标branch已push
[ ] GitHub未包含secret、checkpoint、cache或大日志
[ ] AGENTS.md已创建
[ ] REPO_MAP.md已基于真实代码创建
[ ] CURRENT_STATE.md已创建
[ ] EVALUATION_CONTRACT.md已创建
[ ] DECISIONS.md已创建
[ ] FAILURE_ANALYSIS.md已创建
[ ] NEXT_EXPERIMENTS.md已创建
[ ] experiments.jsonl合法
[ ] handoff模板和至少一份实际handoff存在
[ ] start_run/finalize_run可用
[ ] 旧baseline已backfill或明确说明为何无法backfill
[ ] 一次dry-run端到端完成
[ ] validate_research_state通过
[ ] Lambda artifact路径可以从GitHub摘要追踪
[ ] working tree干净
[ ] 最新commit已push
```

---

# 第十八部分：Codex 最终报告格式

完成后必须向用户报告：

```text
Workflow branch:
GitHub repository:
Pushed commit:
Lambda project path:
Other Git repositories found:
Existing branches/tags preserved:
Dirty state handling:
Active processes left untouched:

Research documents created:
Research tools created:
Runs backfilled:
Dry run ID:
Baseline run ID:
Current method run ID:

Checkpoint paths:
Log paths:
Result paths:
Manifest paths:

Verified baseline:
Verified current result:
Still unverified:

Secret scan:
Large files tracked:
Validation command:
Validation result:

ChatGPT should read first:
1.
2.
3.

User action still required:
Safe to terminate Lambda now: yes/no
```

若 GitHub 授权或 repository URL 是唯一阻塞项，不要停掉整个审计和文档搭建。完成所有本地可完成部分，随后只请求仓库 URL 或网页授权，不请求 credential。

---

# 第十九部分：本次任务的执行顺序

Codex 现在按顺序执行：

```text
Phase A  挂载检查和非破坏性审计
Phase B  确认主项目、保护dirty状态和Git历史
Phase C  确认或配置GitHub remote
Phase D  添加research状态层和AGENTS.md
Phase E  实现research_ops工具
Phase F  回填baseline和当前关键版本
Phase G  运行一个低成本dry run
Phase H  生成CURRENT_STATE和历史handoff
Phase I  校验、commit、push
Phase J  按最终报告格式向用户交接
```

遇到以下情况必须暂停并询问用户：

1. 无法唯一识别当前主项目；
2. 发现多个互相分叉且都可能是最新的仓库；
3. 当前有长训练且迁移会影响它；
4. GitHub remote 指向用户不认识的账号；
5. 发现可能已经泄露的 secret；
6. 需要 force push；
7. 需要删除、搬迁或覆盖大型目录；
8. 需要启动明显产生费用的完整训练；
9. 旧 baseline 与用户记忆的 53/168 不一致；
10. evaluator 或 task list 已发生变化，导致结果不可直接比较。

除此之外，不要只给建议；应直接完成能够安全完成的搭建工作。
