# 避险观察池生成器安装说明

本目录是可拷回的无密生成器。它不包含观察名单、资金数据、密码或令牌。真实 JSON 只在构建机上读取，不得提交到这个公开仓库。

当前 PR **没有**替换线上 `verify/flow/index.html`，也没有部署。线上要等父代理用生产口令加密后再发布。

## 拷贝

把整个 `verify/flow/generator/` 拷到构建机，例如：

`/workspace/flow_board/defensive_pool/`

不要修改 `/workspace/ashare_digest/build_page.py`。发布时本脚本只读导入其中的 `encrypt_bundle`、`site_salt` 和 `PBKDF2_ITERS`（必须是 600000）。

旧的 `/workspace/flow_board/build_flow_board.py` 不在本仓库里，本 PR 不改它。父代理切换例程时，应停止让旧脚本 `--publish`（它会拉取 ops-hub 并覆盖 `verify/flow`）。不要把 ops-hub 的任务并进这个生成器。

## 命令

校验（不写页面、不打印个股名称或金额）：

```bash
python3 build_defensive_pool.py --check \
  --latest web/latest.json \
  --history web/history.json \
  --pool web/pool.json \
  --review web/review.json
```

本地明文预览（真实数据的输出目录必须在 git 仓库之外）：

```bash
python3 build_defensive_pool.py --preview /tmp/defensive-pool-preview \
  --latest web/latest.json \
  --history web/history.json \
  --pool web/pool.json \
  --review web/review.json
```

加密发布（父代理在构建机执行；本云端任务不执行）：

```bash
bash -lc 'python3 build_defensive_pool.py --publish \
  --latest web/latest.json \
  --history web/history.json \
  --pool web/pool.json \
  --review web/review.json'
```

需要环境变量 `VERIFY_DASHBOARD_PASSWORD`。没有口令就失败退出，不写页面。发布还需要冻结的 `build_page.py`、`GH_FINE_TOKEN`，以及页面仓库 `/workspace/ashare_digest/pages`。令牌只出现在单次 git URL 里，不写 remote，不改全局 git 配置。

发布只提交 `verify/flow/index.html`。工作区不干净、锁被占用、与远端分叉时退出。不用 `--force`。不碰首页、`verify/index.html`、`brain/` 和 `verify/flow/archive/`。

脚本指纹：

```bash
python3 build_defensive_pool.py --manifest
```

父代理把输出的 sha256 写进自己的 sidecar 后，再切换例程。

历史密文归档是 `verify/flow/archive/index.html`，校验：

```bash
python3 build_defensive_pool.py --archive-check
```

## 仍然由父代理持有的输入

生成器不内置这些内容，发布前要由研究包提供：

- `web/latest.json`（当日 20 只、对照、宽度、解释、方法、`data_finality`）
- `web/history.json`（含回填标记；缺失日保持 null）
- 可选 `web/pool.json`、`web/review.json`（版本需与当日 `pool_version` 一致）
- 生产口令 `VERIFY_DASHBOARD_PASSWORD`
- 构建机上的冻结 `build_page.py` 与已有 `.site_salt`

定稿前 `pool_status` 应为 `proposed_for_confirmation`。页面会保持「建议采用，待定稿」，不会因为核心字段 20/20 就写成正式锁池。
